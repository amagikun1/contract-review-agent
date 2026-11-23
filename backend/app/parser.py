"""文档解析器。

公开契约（ARCHITECTURE.md）::

    parser.parse_document(path: Path) -> {
        "text": str,
        "pages": [{"page": int, "text": str}, ...],
        "method": str,
        "warnings": [str, ...],
    }

支持 txt / md / docx / pdf / png / jpg / jpeg。PDF 默认使用 PyMuPDF 提取文本层，
逐页判断：某页文本过少时尝试 RapidOCR；扫描版 PDF 与图片依赖可选的
``rapidocr_onnxruntime``，缺失或识别失败时抛出带安装/操作指引的 ``ValueError``。

``method`` 取值：
* ``text`` / ``markdown`` / ``docx``；
* ``pdf-text``（全部来自文本层）、``pdf-ocr``（全部来自 OCR）、
  ``pdf-mixed``（文本层与 OCR 混用）；
* ``ocr``（图片）。

为避免超大资源开销：PDF 最多解析 50 页、PDF 最多 100 MB、文本最多 8 MB /
200 万字符、图片最多 30 MB 且识别前把长边缩到 2600 像素以内。

可选依赖（不在 requirements 中强制）：
* 文本层 PDF：``pip install pymupdf``
* DOCX：``pip install python-docx``
* 扫描件/图片 OCR：``pip install rapidocr_onnxruntime onnxruntime pillow numpy``
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(
    {".txt", ".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}
)

#: 文本文件读取上限。
MAX_TEXT_BYTES = 8 * 1024 * 1024
#: 文本字符上限，避免前端/LLM 被超大文本拖垮。
MAX_TEXT_CHARS = 2_000_000
#: DOCX 文件上限。
MAX_DOCX_BYTES = 50 * 1024 * 1024
#: PDF 文件上限。
MAX_PDF_BYTES = 100 * 1024 * 1024
#: PDF 最多解析页数（超出只解析前 N 页并给出 warning）。
MAX_PDF_PAGES = 50
#: 图片文件上限。
MAX_IMAGE_BYTES = 30 * 1024 * 1024
#: 单张图片像素上限（超过直接报错，避免解码耗尽内存）。
MAX_IMAGE_PIXELS = 40_000_000
#: OCR 前图片长边上限。
MAX_IMAGE_DIM = 2600
#: PDF 页面 OCR 渲染分辨率。
PDF_OCR_DPI = 200
#: 文本层少于该字符数时尝试 OCR（扫描页常常只有页眉/页码）。
PDF_OCR_MIN_NATIVE_CHARS = 20

_OCR_LOCK = threading.RLock()
_OCR_ENGINE: Any | None = None
_OCR_INIT_ERROR: str | None = None

_PYMUPDF_HINT = "pip install pymupdf"
_DOCX_HINT = "pip install python-docx"
_OCR_HINT = "pip install rapidocr_onnxruntime onnxruntime pillow numpy"


# --------------------------------------------------------------------------- 工具
def _add_warning(warnings: list[str], message: str) -> None:
    """去重并限制 warning 数量，避免扫描版 PDF 输出几十条重复提示。"""

    if message in warnings or len(warnings) >= 50:
        return
    warnings.append(message)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _empty_text_error() -> ValueError:
    return ValueError(
        "文档未提取到任何文本：文件可能为空、只包含图片，或 PDF 只有扫描图层。"
        "若为扫描版 PDF/图片，请安装 OCR 依赖后重试："
        f"{_OCR_HINT}；若文件确无正文，请上传包含文字的文档。"
    )


def _check_file(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"路径不是文件：{path}")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise ValueError(f"无法读取文件信息：{path}（{exc}）") from exc


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"无法读取文件：{path}（{exc}）") from exc


# --------------------------------------------------------------------------- TXT / MD
def _decode_text(data: bytes, filename: str) -> tuple[str, str]:
    """按 UTF-16(BOM) → UTF-8(BOM) → GB18030 顺序解码。"""

    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass

    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        return data.decode("gb18030"), "gb18030"
    except UnicodeDecodeError:
        pass

    raise ValueError(
        f"无法解码文本文件 {filename}：请将文件另存为 UTF-8 或 GB18030 编码后重试。"
    )


def _parse_plain_text(path: Path, suffix: str) -> dict[str, Any]:
    size = _file_size(path)
    if size > MAX_TEXT_BYTES:
        raise ValueError(
            f"文本文件过大（{size / 1024 / 1024:.1f} MB），"
            f"最大支持 {MAX_TEXT_BYTES // 1024 // 1024} MB。请拆分后重新上传。"
        )

    data = _read_bytes(path)
    if not data.strip():
        raise _empty_text_error()

    text, encoding = _decode_text(data, path.name)
    warnings: list[str] = []
    if encoding != "utf-8":
        _add_warning(
            warnings,
            f"文件不是 UTF-8 编码，已按 {encoding} 解码；如出现乱码请转存为 UTF-8。",
        )

    text = _normalize_newlines(text).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip()
        _add_warning(
            warnings,
            f"文本超过 {MAX_TEXT_CHARS} 字符，已截断；请拆分文件以获得完整审查结果。",
        )
    if not text:
        raise _empty_text_error()

    return {
        "text": text,
        "pages": [{"page": 1, "text": text}],
        "method": "markdown" if suffix == ".md" else "text",
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- DOCX
def _import_docx() -> Any:
    try:
        from docx import Document  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ValueError(
            "解析 DOCX 需要可选依赖 python-docx，请先安装："
            f"{_DOCX_HINT}，然后重新上传。"
        ) from exc
    return Document


def _parse_docx(path: Path) -> dict[str, Any]:
    size = _file_size(path)
    if size > MAX_DOCX_BYTES:
        raise ValueError(
            f"DOCX 文件过大（{size / 1024 / 1024:.1f} MB），"
            f"最大支持 {MAX_DOCX_BYTES // 1024 // 1024} MB。请拆分后重新上传。"
        )

    Document = _import_docx()
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ValueError(
            f"无法打开 DOCX 文件（{type(exc).__name__}: {exc}）。"
            "请确认文件未损坏，且确实是 .docx（不是改后缀的 .doc）。"
        ) from exc

    warnings: list[str] = []
    blocks: list[str] = []
    try:
        for paragraph in document.paragraphs:
            value = paragraph.text.strip()
            if value:
                blocks.append(value)

        for table in document.tables:
            for row in table.rows:
                cells: list[str] = []
                seen: set[str] = set()
                for cell in row.cells:
                    value = " ".join(cell.text.split())
                    if value and value not in seen:
                        seen.add(value)
                        cells.append(value)
                if cells:
                    blocks.append(" | ".join(cells))
    except Exception as exc:
        raise ValueError(
            f"读取 DOCX 内容失败（{type(exc).__name__}: {exc}）。"
            "请检查文档是否损坏，或另存为 PDF/TXT 后重新上传。"
        ) from exc

    text = "\n".join(blocks).strip()
    if not text:
        raise ValueError(
            "DOCX 中没有可提取文本。若正文位于图片、文本框或扫描页中，"
            "请另存为 PDF 后上传，并安装 OCR 依赖："
            f"{_OCR_HINT}。"
        )
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip()
        _add_warning(
            warnings,
            f"文本超过 {MAX_TEXT_CHARS} 字符，已截断；请拆分文件以获得完整审查结果。",
        )

    return {
        "text": text,
        "pages": [{"page": 1, "text": text}],
        "method": "docx",
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- PDF
def _import_fitz() -> Any:
    try:
        import pymupdf as fitz  # type: ignore[import-not-found]
    except ImportError:
        try:
            import fitz  # type: ignore[import-not-found,no-redef]
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ValueError(
                "解析 PDF 需要可选依赖 PyMuPDF，请先安装："
                f"{_PYMUPDF_HINT}，然后重新上传。"
            ) from exc
    return fitz


def _pdf_page_text(page: Any, page_number: int) -> str:
    try:
        return page.get_text("text") or ""
    except Exception as exc:
        raise ValueError(
            f"读取 PDF 第 {page_number} 页文本失败（{type(exc).__name__}: {exc}）。"
            "请确认文件未损坏，或另存为新的 PDF 后重试。"
        ) from exc


def _pdf_page_to_image(page: Any, fitz: Any) -> Any:
    """把 PDF 页面渲染为适合 OCR 的 RGB numpy 数组（或 PNG bytes）。"""

    rect = page.rect
    longest = max(float(rect.width), float(rect.height), 1.0)
    scale = PDF_OCR_DPI / 72.0
    if longest * scale > MAX_IMAGE_DIM:
        scale = MAX_IMAGE_DIM / longest
    scale = max(scale, 0.05)

    try:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False
        )
    except Exception as exc:
        raise ValueError(
            f"渲染 PDF 页面失败（{type(exc).__name__}: {exc}）。"
            "请尝试重新导出 PDF 后上传。"
        ) from exc

    try:
        import numpy as np  # type: ignore[import-not-found]

        array = np.frombuffer(pixmap.samples, dtype=np.uint8)
        if pixmap.n == 1:
            array = array.reshape(pixmap.height, pixmap.width)
            array = np.repeat(array[:, :, None], 3, axis=2)
        else:
            array = array.reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
        return array
    except ImportError:
        # rapidocr 能直接解码 PNG bytes；numpy 只是更高效。
        return pixmap.tobytes("png")


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE, _OCR_INIT_ERROR

    with _OCR_LOCK:
        if _OCR_ENGINE is not None:
            return _OCR_ENGINE
        if _OCR_INIT_ERROR is not None:
            raise ValueError(_OCR_INIT_ERROR)

        try:
            from rapidocr_onnxruntime import (  # type: ignore[import-not-found]
                RapidOCR,
            )
        except ImportError as exc:
            _OCR_INIT_ERROR = (
                "扫描件/图片识别需要可选依赖 rapidocr_onnxruntime 与 onnxruntime，"
                f"请先安装：{_OCR_HINT}，然后重新上传。"
            )
            raise ValueError(_OCR_INIT_ERROR) from exc

        try:
            _OCR_ENGINE = RapidOCR()
        except Exception as exc:
            _OCR_INIT_ERROR = (
                "OCR 引擎初始化失败"
                f"（{type(exc).__name__}: {exc}）。请确认 rapidocr_onnxruntime / "
                "onnxruntime 安装完整且模型文件可下载；也可上传带文本层的 PDF。"
            )
            raise ValueError(_OCR_INIT_ERROR) from exc

        return _OCR_ENGINE


def _extract_ocr_text(result: Any) -> str:
    """兼容 RapidOCR 不同版本的返回结构，提取文本行。"""

    if result is None:
        return ""
    if isinstance(result, tuple):
        # 旧版返回 (result, elapse)。
        result = result[0] if result else None
    if result is None:
        return ""

    lines: list[str] = []

    if hasattr(result, "txts"):
        for value in getattr(result, "txts") or []:
            if isinstance(value, str) and value.strip():
                lines.append(value.strip())
        return "\n".join(lines)

    items = result if isinstance(result, (list, tuple)) else []
    for item in items:
        value: Any = None
        if isinstance(item, dict):
            value = item.get("text") or item.get("txt")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            value = item[1]
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
    return "\n".join(lines)


def _run_ocr(image: Any) -> str:
    """执行 OCR；失败统一转换为带操作指引的 ValueError。"""

    with _OCR_LOCK:
        engine = _get_ocr_engine()
        try:
            result = engine(image)
        except Exception as exc:
            raise ValueError(
                f"OCR 识别失败（{type(exc).__name__}: {exc}）。"
                "请确认图片清晰、方向正常、文字占比较大；也可改用带文本层的 PDF。"
            ) from exc
    return _extract_ocr_text(result).strip()


def _parse_pdf(path: Path) -> dict[str, Any]:
    size = _file_size(path)
    if size > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF 文件过大（{size / 1024 / 1024:.1f} MB），"
            f"最大支持 {MAX_PDF_BYTES // 1024 // 1024} MB。请拆分后重新上传。"
        )

    fitz = _import_fitz()
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise ValueError(
            f"无法打开 PDF（{type(exc).__name__}: {exc}）。"
            "请确认文件未损坏、未加密，或重新导出 PDF 后重试。"
        ) from exc

    warnings: list[str] = []
    try:
        if getattr(document, "needs_pass", False) or getattr(
            document, "is_encrypted", False
        ):
            raise ValueError(
                "PDF 已加密，无法提取文本。请解除密码保护（或提供未加密副本）后重新上传。"
            )

        total_pages = int(getattr(document, "page_count", 0))
        if total_pages <= 0:
            raise ValueError("PDF 不包含任何页面，请确认文件内容后重新上传。")

        parse_pages = min(total_pages, MAX_PDF_PAGES)
        if total_pages > MAX_PDF_PAGES:
            _add_warning(
                warnings,
                f"PDF 共 {total_pages} 页，仅解析前 {MAX_PDF_PAGES} 页以避免资源开销过大。",
            )

        pages: list[dict[str, Any]] = []
        has_native = False
        has_ocr = False
        ocr_error_reported = False

        for index in range(parse_pages):
            page_number = index + 1
            try:
                page = document.load_page(index)
            except Exception as exc:
                raise ValueError(
                    f"加载 PDF 第 {page_number} 页失败（{type(exc).__name__}: {exc}）。"
                    "请确认文件完整后重试。"
                ) from exc

            native_text = _pdf_page_text(page, page_number).strip()
            page_text = native_text
            page_ocr_used = False

            if len(native_text) < PDF_OCR_MIN_NATIVE_CHARS:
                try:
                    image = _pdf_page_to_image(page, fitz)
                    ocr_text = _run_ocr(image)
                except ValueError as exc:
                    if not ocr_error_reported:
                        _add_warning(warnings, f"第 {page_number} 页 OCR 不可用：{exc}")
                        ocr_error_reported = True
                    elif native_text:
                        _add_warning(
                            warnings,
                            f"第 {page_number} 页文字较少，OCR 不可用，保留文本层内容。",
                        )
                    else:
                        _add_warning(
                            warnings,
                            f"第 {page_number} 页未提取到文本，OCR 不可用。",
                        )
                else:
                    if ocr_text and len(ocr_text) > len(native_text):
                        page_text = ocr_text
                        page_ocr_used = True
                    if not ocr_text:
                        _add_warning(
                            warnings, f"第 {page_number} 页 OCR 未识别出文字。"
                        )

            if page_ocr_used:
                has_ocr = True
            elif page_text:
                has_native = True

            if not page_text:
                _add_warning(
                    warnings,
                    f"第 {page_number} 页没有可提取文本（可能是空白页或纯图片页）。",
                )

            pages.append({"page": page_number, "text": page_text})

        text = "\n\n".join(page["text"] for page in pages if page["text"]).strip()
        if not text:
            raise _empty_text_error()

        if has_ocr and has_native:
            method = "pdf-mixed"
        elif has_ocr:
            method = "pdf-ocr"
        else:
            method = "pdf-text"

        return {
            "text": text,
            "pages": pages,
            "method": method,
            "warnings": warnings,
        }
    finally:
        try:
            document.close()
        except Exception:  # pragma: no cover - 关闭失败不影响已解析结果
            pass


# --------------------------------------------------------------------------- 图片
def _prepare_image(path: Path) -> tuple[Any, list[str]]:
    """校验图片尺寸并缩放到 OCR 友好大小，返回 (输入, warnings)。"""

    warnings: list[str] = []
    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        _add_warning(
            warnings,
            "未安装 Pillow，跳过图片尺寸预缩放；识别超大图片可能占用较多内存。"
            f"建议安装：{_OCR_HINT}。",
        )
        return str(path), warnings

    try:
        with Image.open(path) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise ValueError("图片尺寸无效。")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError(
                    f"图片像素过多（{width}x{height}），最大支持约 "
                    f"{MAX_IMAGE_PIXELS // 1_000_000} 百万像素。"
                    "请压缩/缩小图片后重新上传。"
                )

            oriented = ImageOps.exif_transpose(opened)
            longest = max(width, height)
            if longest > MAX_IMAGE_DIM:
                ratio = MAX_IMAGE_DIM / float(longest)
                new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
                oriented = oriented.resize(new_size, Image.LANCZOS)
                _add_warning(
                    warnings,
                    f"图片分辨率较高（{width}x{height}），"
                    f"已缩小到 {new_size[0]}x{new_size[1]} 后识别。",
                )
            rgb = oriented.convert("RGB")
            rgb.load()
    except ValueError:
        raise
    except Image.DecompressionBombError as exc:  # type: ignore[attr-defined]
        raise ValueError(
            "图片像素尺寸过大，已拒绝解码以避免耗尽内存。请压缩后重新上传。"
        ) from exc
    except Exception as exc:
        raise ValueError(
            f"无法读取图片（{type(exc).__name__}: {exc}）。"
            "请确认文件是有效的 PNG/JPG，且未损坏。"
        ) from exc

    try:
        import numpy as np  # type: ignore[import-not-found]

        return np.asarray(rgb), warnings
    except ImportError:
        # RapidOCR 也支持 PIL.Image；缺少 numpy 时直接传 PIL 对象。
        return rgb, warnings


def _parse_image(path: Path) -> dict[str, Any]:
    size = _file_size(path)
    if size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"图片文件过大（{size / 1024 / 1024:.1f} MB），"
            f"最大支持 {MAX_IMAGE_BYTES // 1024 // 1024} MB。请压缩后重新上传。"
        )

    image, warnings = _prepare_image(path)
    text = _run_ocr(image).strip()
    if not text:
        raise ValueError(
            "图片中未识别到文字。请确认图片清晰、方向正常、文字占比较大；"
            f"若尚未安装 OCR 依赖，请先安装：{_OCR_HINT}。"
        )
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip()
        _add_warning(
            warnings,
            f"识别文本超过 {MAX_TEXT_CHARS} 字符，已截断；请拆分图片后重试。",
        )

    return {
        "text": text,
        "pages": [{"page": 1, "text": text}],
        "method": "ocr",
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- 入口
def parse_document(path: Path) -> dict[str, Any]:
    """解析文档，返回 ``text`` / ``pages`` / ``method`` / ``warnings``。

    :raises ValueError: 文件不存在、格式不支持、依赖缺失、扫描件无法识别、
        文本为空或超出资源限制。
    """

    document_path = Path(path)
    _check_file(document_path)

    suffix = document_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(
            f"不支持的文件格式：{suffix or '（无后缀）'}。"
            f"当前支持：{supported}。请转换格式后重新上传。"
        )

    if suffix in {".txt", ".md"}:
        return _parse_plain_text(document_path, suffix)
    if suffix == ".docx":
        return _parse_docx(document_path)
    if suffix == ".pdf":
        return _parse_pdf(document_path)
    return _parse_image(document_path)
