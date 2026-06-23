"""串联：input → extract → remove_text → export。复用 banana-slides 的 PPTXBuilder 导出。"""
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image

from .input.loader import download_url_to_temp, is_http_url, load_images
from .extract.ocr import run_ocr, resolve_ocr_engine
from .extract.style import infer_styles
from .remove_text.design_reconstruction import reconstruct_background
from .export.ppt import build_editable_pptx
from .export.merge_blocks import mark_background_blocks
from .utils.fonts import default_fonts


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
    font_normal: Optional[str] = None,
    font_bold: Optional[str] = None,
    ocr_engine: str = "auto",
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

    report(f"OCR 识别({ocr_engine})")
    ocr_result = run_ocr(image, ocr_engine=ocr_engine)
    report("样式推断")
    styled = infer_styles(image, ocr_result)
    # 在去字重建之前，标记"应保留在背景图"的块（列表标号 / 项目符号 / 图标字符 /
    # 带标号前缀的列表项）：这些块打 _skip_render=True，去字重建会跳过它们的区域，
    # 从而原样保留在背景图上，避免"被抹掉又不渲染、导致视觉丢失"。
    styled = mark_background_blocks(styled)
    report("去字重建")
    cleaned = reconstruct_background(image, styled)
    return cleaned, styled


def _resolve_slide_size_px(
    images: list[Image.Image],
    slide_size_mode: str,
    dpi: int = 96,
) -> tuple[int, int]:
    """
    根据 slide_size_mode 决定 PPT 逻辑画布尺寸（像素）。

    - widescreen: 固定 16:9 (1280×720)，保持现有行为。
    - native: 严格匹配单张输入图片尺寸，适合海报；多页输入直接报错。
    """
    from .export.ppt import SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX

    if slide_size_mode == "widescreen":
        return SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX

    if slide_size_mode == "native":
        if len(images) != 1:
            raise ValueError(
                "--slide-size-mode native 仅支持单张图片输入。"
                "海报模式要求 PPT 页面尺寸严格匹配唯一输入图片尺寸；"
                "多页 PDF 或图片目录请使用默认 widescreen 模式。"
            )
        img = images[0]
        w_px, h_px = img.width, img.height
        # PowerPoint 单页最大约 56 英寸，超限会触发底层缩放，破坏 1:1 语义
        max_inches = 56.0
        if w_px / dpi > max_inches or h_px / dpi > max_inches:
            raise ValueError(
                f"--slide-size-mode native 下输入图片尺寸过大（{w_px}x{h_px}px），"
                f"按 {dpi} DPI 换算会超过 PowerPoint 最大页面尺寸 {max_inches} 英寸。"
                "请先缩小图片尺寸，或使用默认 widescreen 模式。"
            )
        return w_px, h_px

    raise ValueError(f"未知 slide_size_mode: {slide_size_mode}")


def run_pipeline(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    font_normal: Optional[str] = None,
    font_bold: Optional[str] = None,
    font_ea_normal: Optional[str] = None,
    font_ea_bold: Optional[str] = None,
    text_lang: str = "zh-CN",
    text_alt_lang: str = "en-US",
    text_pad_ratio: float = 0.08,
    width_safety: float = 0.96,
    merge_textbox: bool = True,
    ocr_engine: str = "auto",
    pdf_output_path: Optional[Union[str, Path]] = None,
    slide_size_mode: str = "widescreen",
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> None:
    """
    从单图或 PDF 生成可编辑 PPT（复用 banana-slides 的 PPTXBuilder）。
    - input_path: 本地图片/PDF/目录路径，或 http/https 直链（自动下载临时文件后处理）
    - output_path: 输出的 .pptx 路径
    - pdf_output_path: 当 input_path 为目录时，可选输出合并后的 PDF 路径；不传则默认与 ppt 同名 .pdf
    - font_normal/font_bold/font_ea_normal/font_ea_bold: 为 None 时按系统语言自适应
      （中文→腾讯字体 W3/W7；英文→TencentSans W3/W7）。
    - progress_callback: 可选，(phase, current, total, message) -> None。
      phase 为 "load"|"page"|"export"，current/total 为当前步与总步数，message 为简短说明。
    """
    # 字体兜底：未显式指定则按系统语言选默认
    _dl_normal, _dl_bold, _dea_normal, _dea_bold = default_fonts()
    font_normal = font_normal or _dl_normal
    font_bold = font_bold or _dl_bold
    font_ea_normal = font_ea_normal or _dea_normal
    font_ea_bold = font_ea_bold or _dea_bold
    def report(phase: str, current: int, total: int, message: str):
        if progress_callback:
            progress_callback(phase, current, total, message)

    temp_download: Optional[Path] = None
    try:
        report("load", 0, 1, "加载输入…")
        if is_http_url(input_path):
            report("load", 0, 1, "正在从 URL 下载…")
            temp_download = download_url_to_temp(str(input_path).strip())
            resolved_input = temp_download
        else:
            resolved_input = Path(input_path)

        selected_engine = resolve_ocr_engine(ocr_engine=ocr_engine)
        images = load_images(resolved_input)
        if not images:
            raise ValueError("未得到任何图片")
        n = len(images)
        report("load", 1, 1, f"已加载 {n} 页")
        report("load", 1, 1, f"OCR 引擎: {selected_engine}")

        # 根据尺寸模式决定 PPT 逻辑画布尺寸
        slide_width_px, slide_height_px = _resolve_slide_size_px(images, slide_size_mode)

        if resolved_input.is_dir():
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
                ocr_engine=selected_engine,
                page_index=i,
                total_pages=n,
                progress_callback=progress_callback,
            )
            w, h = cleaned.size
            slides_data.append((cleaned, styled, w, h))

        report("export", 0, n, "开始写入 PPT…")
        build_editable_pptx(
            slides_data,
            output_path,
            progress_callback=progress_callback,
            font_normal=font_normal,
            font_bold=font_bold,
            font_ea_normal=font_ea_normal,
            font_ea_bold=font_ea_bold,
            text_lang=text_lang,
            text_alt_lang=text_alt_lang,
            text_pad_ratio=text_pad_ratio,
            width_safety=width_safety,
            merge_textbox=merge_textbox,
            slide_width_px=slide_width_px,
            slide_height_px=slide_height_px,
        )
        report("export", n, n, "写入完成")
    finally:
        if temp_download is not None and temp_download.exists():
            try:
                temp_download.unlink()
            except OSError:
                pass
