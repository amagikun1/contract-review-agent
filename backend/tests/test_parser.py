"""Parser 单元测试。

无需可选依赖的 txt/md 与错误分支始终运行；DOCX/PyMuPDF/RapidOCR 相关用例
在依赖缺失时自动 skip，便于父代理先跑基础测试、安装依赖后再跑全量。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import parser  # noqa: E402

SAMPLES_DIR = ROOT / "samples"


@pytest.fixture()
def workdir() -> Path:
    """独立临时目录；避免 pytest 默认临时目录在受限 Windows 沙箱中的 0o700 问题。"""

    path = Path(tempfile.gettempdir()) / f"contract-parser-{uuid.uuid4().hex}"
    os.mkdir(path, 0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _fitz():
    try:
        import pymupdf as fitz  # type: ignore[import-not-found]

        return fitz
    except ImportError:
        return pytest.importorskip("fitz")


def _make_pdf(path: Path, page_texts: list[str]) -> None:
    fitz = _fitz()
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=14, fontname="china-s")
    document.save(str(path))
    document.close()


def _make_png(path: Path, size: tuple[int, int] = (480, 160)) -> None:
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 60), "CONTRACT 2026", fill="black")
    image.save(path)


def _has_rapidocr() -> bool:
    try:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except (ImportError, ValueError):
        return False


def _assert_contract(result: dict) -> None:
    assert set(result) == {"text", "pages", "method", "warnings"}
    assert isinstance(result["text"], str) and result["text"].strip()
    assert isinstance(result["method"], str) and result["method"]
    assert isinstance(result["warnings"], list)
    assert all(isinstance(item, str) for item in result["warnings"])
    assert isinstance(result["pages"], list) and result["pages"]
    for index, page in enumerate(result["pages"], start=1):
        assert set(page) == {"page", "text"}
        assert page["page"] == index
        assert isinstance(page["text"], str)


# --------------------------------------------------------------------------- TXT / MD
def test_parse_txt(workdir: Path) -> None:
    path = workdir / "合同.txt"
    content = (
        "采购合同\n\n"
        "第一条 付款：甲方应在签订后3日内支付100%预付款。\n"
        "第二条 验收：货物到达后7日内验收。"
    )
    path.write_text(content, encoding="utf-8")

    result = parser.parse_document(path)

    _assert_contract(result)
    assert result["text"] == content
    assert result["method"] == "text"
    assert result["pages"] == [{"page": 1, "text": content}]
    assert result["warnings"] == []


def test_parse_markdown(workdir: Path) -> None:
    path = workdir / "服务合同.md"
    path.write_text("# 服务合同\n\n- 自动续约两年\n- 已付款不退", encoding="utf-8")

    result = parser.parse_document(path)

    _assert_contract(result)
    assert result["method"] == "markdown"
    assert "自动续约" in result["text"]


def test_parse_txt_normalizes_crlf(workdir: Path) -> None:
    path = workdir / "crlf.txt"
    path.write_bytes("第一行\r\n第二行\r第三行".encode("utf-8"))

    result = parser.parse_document(path)

    assert result["text"] == "第一行\n第二行\n第三行"
    assert "\r" not in result["text"]


def test_parse_gb18030_text_with_warning(workdir: Path) -> None:
    path = workdir / "gbk.txt"
    path.write_bytes("采购合同：预付款比例100%。".encode("gb18030"))

    result = parser.parse_document(path)

    assert "采购合同" in result["text"]
    assert result["warnings"]
    assert "gb18030" in result["warnings"][0]


def test_parse_txt_truncates_over_limit(workdir: Path, monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_TEXT_CHARS", 10)
    path = workdir / "long.txt"
    path.write_text("一二三四五六七八九十十一十二", encoding="utf-8")

    result = parser.parse_document(path)

    assert result["text"] == "一二三四五六七八九十"
    assert any("截断" in warning for warning in result["warnings"])


def test_parse_txt_rejects_too_large_file(workdir: Path, monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_TEXT_BYTES", 4)
    path = workdir / "big.txt"
    path.write_text("超过四个字节", encoding="utf-8")

    with pytest.raises(ValueError, match="过大"):
        parser.parse_document(path)


def test_parse_missing_file(workdir: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        parser.parse_document(workdir / "missing.txt")


def test_parse_directory(workdir: Path) -> None:
    with pytest.raises(ValueError, match="不是文件"):
        parser.parse_document(workdir)


def test_parse_unsupported_suffix(workdir: Path) -> None:
    path = workdir / "data.csv"
    path.write_text("a,b", encoding="utf-8")

    with pytest.raises(ValueError, match="不支持的文件格式"):
        parser.parse_document(path)


@pytest.mark.parametrize("content", ["", "   \n\t  \n"])
def test_parse_empty_text_raises(workdir: Path, content: str) -> None:
    path = workdir / "empty.txt"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="未提取到任何文本"):
        parser.parse_document(path)


def test_samples_are_parseable() -> None:
    samples = sorted(SAMPLES_DIR.glob("*.txt"))
    if not samples:
        pytest.skip("samples/*.txt 不存在")

    for sample in samples:
        result = parser.parse_document(sample)
        _assert_contract(result)
        assert result["method"] == "text"
        assert len(result["text"]) > 500
        assert "合同" in result["text"]


# --------------------------------------------------------------------------- DOCX
def test_parse_docx(workdir: Path) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("技术服务合同")
    document.add_paragraph("第一条 甲方应在签订后支付100%年费。")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "服务费"
    table.cell(0, 1).text = "玖拾陆万元"
    path = workdir / "service.docx"
    document.save(str(path))

    result = parser.parse_document(path)

    _assert_contract(result)
    assert result["method"] == "docx"
    assert "100%年费" in result["text"]
    assert "玖拾陆万元" in result["text"]


def test_parse_empty_docx_raises(workdir: Path) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("   ")
    path = workdir / "blank.docx"
    document.save(str(path))

    with pytest.raises(ValueError, match="没有可提取文本"):
        parser.parse_document(path)


# --------------------------------------------------------------------------- PDF
def test_parse_pdf_with_text_layer(workdir: Path, monkeypatch) -> None:
    pytest.importorskip("pymupdf")
    monkeypatch.setattr(parser, "_run_ocr", lambda image: "")
    path = workdir / "合同.pdf"
    _make_pdf(path, ["采购合同风险测试文本，预付款比例为百分之一百。"])

    result = parser.parse_document(path)

    _assert_contract(result)
    assert result["method"] == "pdf-text"
    assert "采购合同" in result["text"]
    assert result["pages"][0]["page"] == 1


def test_parse_pdf_keeps_blank_pages_with_warning(workdir: Path, monkeypatch) -> None:
    pytest.importorskip("pymupdf")
    monkeypatch.setattr(parser, "_run_ocr", lambda image: "")
    path = workdir / "two-pages.pdf"
    _make_pdf(path, ["第一页：质量保证期为十二个月。", ""])

    result = parser.parse_document(path)

    assert len(result["pages"]) == 2
    assert result["pages"][1]["text"] == ""
    assert any("第 2 页" in warning for warning in result["warnings"])


def test_parse_blank_pdf_raises(workdir: Path, monkeypatch) -> None:
    pytest.importorskip("pymupdf")
    monkeypatch.setattr(parser, "_run_ocr", lambda image: "")
    path = workdir / "blank.pdf"
    _make_pdf(path, [""])

    with pytest.raises(ValueError, match="未提取到任何文本"):
        parser.parse_document(path)


def test_parse_pdf_limits_page_count(workdir: Path, monkeypatch) -> None:
    pytest.importorskip("pymupdf")
    monkeypatch.setattr(parser, "_run_ocr", lambda image: "")
    monkeypatch.setattr(parser, "MAX_PDF_PAGES", 3)
    path = workdir / "many-pages.pdf"
    _make_pdf(
        path,
        ["第一页：甲方应在合同签订后三个工作日内支付全部预付款。"] + [""] * 4,
    )

    result = parser.parse_document(path)

    assert len(result["pages"]) == 3
    assert any("仅解析前 3 页" in warning for warning in result["warnings"])


def test_parse_pdf_ocr_failure_is_reported(workdir: Path) -> None:
    pytest.importorskip("pymupdf")
    path = workdir / "scan-like.pdf"
    _make_pdf(path, [""])

    with pytest.raises(ValueError, match="未提取到任何文本"):
        parser.parse_document(path)


# --------------------------------------------------------------------------- 图片
def test_image_without_ocr_dependency_gives_actionable_error(
    workdir: Path, monkeypatch
) -> None:
    if _has_rapidocr():
        pytest.skip("已安装 rapidocr_onnxruntime，此用例仅验证缺少依赖时的错误")
    path = workdir / "scan.png"
    _make_png(path)

    with pytest.raises(ValueError, match="rapidocr_onnxruntime"):
        parser.parse_document(path)


def test_image_too_large_raises(workdir: Path, monkeypatch) -> None:
    path = workdir / "big.png"
    _make_png(path, size=(480, 160))
    monkeypatch.setattr(parser, "MAX_IMAGE_BYTES", 8)

    with pytest.raises(ValueError, match="图片文件过大"):
        parser.parse_document(path)


def test_image_too_many_pixels_raises(workdir: Path, monkeypatch) -> None:
    path = workdir / "huge.png"
    _make_png(path, size=(480, 160))
    monkeypatch.setattr(parser, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(ValueError, match="像素过多"):
        parser.parse_document(path)


@pytest.mark.skipif(
    not _has_rapidocr(), reason="需要可选依赖 rapidocr_onnxruntime"
)
def test_ocr_png_end_to_end(workdir: Path) -> None:
    pytest.importorskip("pymupdf")
    fitz = _fitz()
    pdf_path = workdir / "text.pdf"
    _make_pdf(pdf_path, ["CONTRACT 2026"])
    document = fitz.open(str(pdf_path))
    try:
        pixmap = document.load_page(0).get_pixmap(dpi=150)
        image_path = workdir / "scan.png"
        pixmap.save(str(image_path))
    finally:
        document.close()

    result = parser.parse_document(image_path)

    _assert_contract(result)
    assert result["method"] == "ocr"
    assert result["pages"][0]["page"] == 1
