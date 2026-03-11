"""串联：input → extract → remove_text → export。复用 banana-slides 的 PPTXBuilder 导出。"""
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image

from .input.loader import load_images
from .extract.ocr import run_ocr
from .extract.style import infer_styles
from .remove_text.design_reconstruction import reconstruct_background
from .export.ppt import build_editable_pptx


def _export_images_to_pdf(images: list[Image.Image], pdf_path: Union[str, Path]) -> None:
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not images:
        raise ValueError("没有图片可导出为 PDF")

    first = images[0].convert("RGB")
    rest = [img.convert("RGB") for img in images[1:]]
    first.save(str(pdf_path), "PDF", save_all=True, append_images=rest)


def process_one_image(
    image: Image.Image,
    font_normal: str = "Tencent Sans W3",
    font_bold: str = "Tencent Sans W7",
    page_index: int = 0,
    total_pages: int = 1,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> tuple:
    """
    对单张图跑完整流水线，返回 (去字后的 PIL Image, styled_blocks)。
    使用设计语义分层重建（Design-aware Reconstruction）方法。
    """
    def report(step: str):
        if progress_callback:
            progress_callback("page", page_index + 1, total_pages, step)

    report("OCR 识别")
    ocr_result = run_ocr(image)
    report("样式推断")
    styled = infer_styles(image, ocr_result)
    report("去字重建")
    cleaned = reconstruct_background(image, styled)
    return cleaned, styled


def run_pipeline(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    font_normal: str = "Tencent Sans W3",
    font_bold: str = "Tencent Sans W7",
    pdf_output_path: Optional[Union[str, Path]] = None,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> None:
    """
    从单图或 PDF 生成可编辑 PPT（复用 banana-slides 的 PPTXBuilder）。
    - input_path: 图片或 PDF 路径
    - output_path: 输出的 .pptx 路径
    - pdf_output_path: 当 input_path 为目录时，可选输出合并后的 PDF 路径；不传则默认与 ppt 同名 .pdf
    - progress_callback: 可选，(phase, current, total, message) -> None。
      phase 为 "load"|"page"|"export"，current/total 为当前步与总步数，message 为简短说明。
    """
    def report(phase: str, current: int, total: int, message: str):
        if progress_callback:
            progress_callback(phase, current, total, message)

    report("load", 0, 1, "加载输入…")
    input_path = Path(input_path)
    images = load_images(input_path)
    if not images:
        raise ValueError("未得到任何图片")
    n = len(images)
    report("load", 1, 1, f"已加载 {n} 页")

    if input_path.is_dir():
        default_pdf = Path(output_path).with_suffix(".pdf")
        pdf_path = Path(pdf_output_path) if pdf_output_path else default_pdf
        report("load", 1, 1, f"合并目录图片为 PDF…")
        _export_images_to_pdf(images, pdf_path)
        report("load", 1, 1, f"已生成合并 PDF: {pdf_path}")

    slides_data = []
    for i, img in enumerate(images):
        cleaned, styled = process_one_image(
            img,
            font_normal=font_normal,
            font_bold=font_bold,
            page_index=i,
            total_pages=n,
            progress_callback=progress_callback,
        )
        w, h = cleaned.size
        slides_data.append((cleaned, styled, w, h))

    report("export", 0, n, "开始写入 PPT…")
    build_editable_pptx(slides_data, output_path, progress_callback=progress_callback)
    report("export", n, n, "写入完成")
