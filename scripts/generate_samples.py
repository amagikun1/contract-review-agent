#!/usr/bin/env python3
"""从 ``samples/*.txt`` 生成 DOCX / PDF / 扫描版 PDF / PNG / JPG 样例。

只依赖可选的 python-docx 与 PyMuPDF，不会自动安装任何包；缺少依赖时打印
安装命令并跳过对应格式。生成的样例不会覆盖手写的 txt 原文。

用法::

    python scripts/generate_samples.py --check
    python scripts/generate_samples.py
    python scripts/generate_samples.py --formats docx,pdf --out samples/generated

依赖::

    pip install python-docx pymupdf        # DOCX + PDF + 扫描版 PDF + PNG
    pip install pillow                     # JPG（可选，缺失时尝试 PyMuPDF 直存）
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "samples"
DEFAULT_OUT = SAMPLES_DIR / "generated"

SAMPLE_FILES = (
    SAMPLES_DIR / "purchase_contract.txt",
    SAMPLES_DIR / "sales_contract.txt",
    SAMPLES_DIR / "service_contract.txt",
)

ALL_FORMATS = ("docx", "pdf", "scan", "png", "jpg")

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
PDF_MARGIN = 50.0
PDF_FONT_SIZE = 10.5
PDF_CHARS_PER_LINE = 42
PDF_LINES_PER_PAGE = 44


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _has_pymupdf() -> bool:
    return _has("pymupdf") or _has("fitz")


def _import_fitz():
    try:
        import pymupdf as fitz  # type: ignore[import-not-found]

        return fitz
    except ImportError:
        import fitz  # type: ignore[import-not-found]

        return fitz


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _wrap_lines(text: str, max_chars: int = PDF_CHARS_PER_LINE) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if not line:
            lines.append("")
            continue
        while len(line) > max_chars:
            cut = max_chars
            space = line.rfind(" ", 0, max_chars + 1)
            if space >= int(max_chars * 0.6):
                cut = space
            lines.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        lines.append(line)
    return lines


# --------------------------------------------------------------------------- DOCX
def build_docx(text: str, out_path: Path) -> None:
    from docx import Document  # type: ignore[import-not-found]
    from docx.oxml.ns import qn  # type: ignore[import-not-found]
    from docx.shared import Pt  # type: ignore[import-not-found]

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    try:
        normal.element.get_or_add_rPr().get_or_add_rFonts().set(
            qn("w:eastAsia"), "宋体"
        )
    except Exception:
        # 字体设置失败不影响正文生成。
        pass

    for index, line in enumerate(text.splitlines()):
        paragraph = document.add_paragraph(line.rstrip())
        if index == 0:
            for run in paragraph.runs:
                run.bold = True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))


# --------------------------------------------------------------------------- PDF
def build_pdf(text: str, out_path: Path, title: str) -> None:
    fitz = _import_fitz()
    document = fitz.open()
    lines = _wrap_lines(text)

    for start in range(0, max(len(lines), 1), PDF_LINES_PER_PAGE):
        chunk = lines[start : start + PDF_LINES_PER_PAGE]
        page = document.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        page.insert_text(
            (PDF_MARGIN, PDF_MARGIN + 12),
            "\n".join(chunk),
            fontsize=PDF_FONT_SIZE,
            fontname="china-s",
            lineheight=1.45,
        )
        footer = f"第 {start // PDF_LINES_PER_PAGE + 1} 页 / 共 {max(1, (len(lines) + PDF_LINES_PER_PAGE - 1) // PDF_LINES_PER_PAGE)} 页"
        page.insert_text(
            (A4_WIDTH / 2 - 40, A4_HEIGHT - 30),
            footer,
            fontsize=9,
            fontname="china-s",
            color=(0.4, 0.4, 0.4),
        )

    document.set_metadata({"title": title, "author": "generate_samples.py"})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path), garbage=4, deflate=True)
    document.close()


def build_scanned_pdf(pdf_path: Path, out_path: Path, max_pages: int = 5) -> int:
    """把文本版 PDF 每页渲染为灰度图，再封装成只有图片的“扫描版” PDF。"""

    fitz = _import_fitz()
    source = fitz.open(str(pdf_path))
    target = fitz.open()
    count = min(source.page_count, max_pages)
    try:
        for index in range(count):
            page = source.load_page(index)
            pixmap = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY, alpha=False)
            image_bytes = pixmap.tobytes("png")
            target_page = target.new_page(width=page.rect.width, height=page.rect.height)
            target_page.insert_image(target_page.rect, stream=image_bytes)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        target.save(str(out_path), garbage=4, deflate=True)
    finally:
        target.close()
        source.close()
    return count


def build_image(pdf_path: Path, out_path: Path, fmt: str) -> None:
    """渲染 PDF 首页为 PNG/JPG，模拟手机拍照/扫描件。"""

    fitz = _import_fitz()
    source = fitz.open(str(pdf_path))
    try:
        page = source.load_page(0)
        pixmap = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY, alpha=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "png":
            pixmap.save(str(out_path))
            return

        try:
            from PIL import Image  # type: ignore[import-not-found]

            image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
            image.save(str(out_path), format="JPEG", quality=82)
        except ImportError:
            # PyMuPDF 在部分版本可直接按扩展名保存 JPEG。
            try:
                pixmap.save(str(out_path))
            except Exception as exc:
                raise RuntimeError(
                    "生成 JPG 需要 Pillow，请先安装：pip install pillow"
                ) from exc
    finally:
        source.close()


# --------------------------------------------------------------------------- 主流程
def _dependency_report() -> dict[str, bool]:
    return {
        "python-docx": _has("docx"),
        "PyMuPDF": _has_pymupdf(),
        "Pillow": _has("PIL"),
    }


def _print_check() -> int:
    report = _dependency_report()
    print("可选依赖检查：")
    for name, ok in report.items():
        print(f"  {name}: {'可用' if ok else '缺失'}")
    print("\n安装命令：pip install python-docx pymupdf pillow")
    return 0 if all(report.values()) else 1


def generate(
    formats: list[str], output_dir: Path, max_scan_pages: int, strict: bool
) -> int:
    missing = [path.name for path in SAMPLE_FILES if not path.is_file()]
    if missing:
        print(f"缺少样例文件：{', '.join(missing)}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    deps = _dependency_report()
    generated = 0
    skipped = 0

    for sample in SAMPLE_FILES:
        text = _read_text(sample)
        title = text.splitlines()[0].strip() if text.splitlines() else sample.stem
        stem = sample.stem
        pdf_path = output_dir / f"{stem}.pdf"

        if "docx" in formats:
            if not deps["python-docx"]:
                print(f"跳过 {stem}.docx：缺少 python-docx（pip install python-docx）")
                skipped += 1
            else:
                out = output_dir / f"{stem}.docx"
                try:
                    build_docx(text, out)
                    print(f"生成 {out.relative_to(ROOT)}")
                    generated += 1
                except Exception as exc:
                    print(f"生成 {out.name} 失败：{type(exc).__name__}: {exc}")
                    skipped += 1
                    if strict:
                        return 1

        if "pdf" in formats:
            if not deps["PyMuPDF"]:
                print(f"跳过 {stem}.pdf：缺少 PyMuPDF（pip install pymupdf）")
                skipped += 1
            else:
                try:
                    build_pdf(text, pdf_path, title)
                    print(f"生成 {pdf_path.relative_to(ROOT)}")
                    generated += 1
                except Exception as exc:
                    print(f"生成 {pdf_path.name} 失败：{type(exc).__name__}: {exc}")
                    skipped += 1
                    if strict:
                        return 1

        scan_path = output_dir / f"{stem}_scan.pdf"
        if "scan" in formats:
            if not deps["PyMuPDF"]:
                print(f"跳过 {stem}_scan.pdf：缺少 PyMuPDF（pip install pymupdf）")
                skipped += 1
            elif not pdf_path.is_file():
                print(f"跳过 {stem}_scan.pdf：需要先生成 {pdf_path.name}")
                skipped += 1
            else:
                try:
                    pages = build_scanned_pdf(pdf_path, scan_path, max_scan_pages)
                    print(f"生成 {scan_path.relative_to(ROOT)}（{pages} 页）")
                    generated += 1
                except Exception as exc:
                    print(f"生成 {scan_path.name} 失败：{type(exc).__name__}: {exc}")
                    skipped += 1
                    if strict:
                        return 1

        if "png" in formats or "jpg" in formats:
            if not deps["PyMuPDF"]:
                print(f"跳过 {stem} 图片：缺少 PyMuPDF（pip install pymupdf）")
                skipped += 1
            elif not pdf_path.is_file():
                print(f"跳过 {stem} 图片：需要先生成 {pdf_path.name}")
                skipped += 1
            else:
                for fmt in ("png", "jpg"):
                    if fmt not in formats:
                        continue
                    out = output_dir / f"{stem}_scan_page1.{fmt}"
                    try:
                        build_image(pdf_path, out, fmt)
                        print(f"生成 {out.relative_to(ROOT)}")
                        generated += 1
                    except Exception as exc:
                        print(f"生成 {out.name} 失败：{type(exc).__name__}: {exc}")
                        skipped += 1
                        if strict:
                            return 1

    print(f"\n完成：生成 {generated} 个文件，跳过/失败 {skipped} 个。")
    print(f"输出目录：{output_dir}")
    if generated == 0:
        print("提示：先安装依赖 pip install python-docx pymupdf pillow", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 samples/*.txt 生成 docx/pdf/扫描版 PDF/图片样例"
    )
    parser.add_argument(
        "--formats",
        default=",".join(ALL_FORMATS),
        help=f"逗号分隔的输出格式，可选：{', '.join(ALL_FORMATS)}（默认全部）",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT.relative_to(ROOT)),
        help="输出目录（相对项目根目录），默认 samples/generated",
    )
    parser.add_argument(
        "--max-scan-pages",
        type=int,
        default=5,
        help="扫描版 PDF 最多生成页数，默认 5",
    )
    parser.add_argument("--check", action="store_true", help="只检查可选依赖")
    parser.add_argument(
        "--strict", action="store_true", help="任一文件生成失败时以非零退出码结束"
    )
    args = parser.parse_args(argv)

    if args.check:
        return _print_check()

    formats = [value.strip().lower() for value in args.formats.split(",") if value.strip()]
    invalid = [value for value in formats if value not in ALL_FORMATS]
    if invalid:
        parser.error(f"不支持的格式：{', '.join(invalid)}")
    if not formats:
        parser.error("--formats 不能为空")

    output_dir = Path(args.out)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    return generate(formats, output_dir, max(1, args.max_scan_pages), args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
