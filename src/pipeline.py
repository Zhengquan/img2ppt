"""串联：input → extract → remove_text → export。复用 banana-slides 的 PPTXBuilder 导出。"""
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Union

from PIL import Image

from .input.loader import (
    download_url_to_temp,
    is_http_url,
    load_image_entries,
    naming_warnings,
)
from .extract.ocr import run_ocr, resolve_ocr_engine
from .extract.style import infer_styles
from .remove_text.design_reconstruction import reconstruct_background
from .export.ppt import build_editable_pptx
from .export.merge_blocks import mark_background_blocks
from .utils.fonts import default_fonts_for_text


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

    - widescreen: 固定 16:9 (1280×720)。
    - auto: 选择输入中出现次数最多的画幅。多页转换时保持一个统一画布，
      同时避免把 3:2 的整套资料默认塞进 16:9 所产生的左右留白。
    - native: 严格匹配单张输入图片尺寸，适合海报；多页输入直接报错。
    """
    from .export.ppt import SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX

    if slide_size_mode == "widescreen":
        return SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX

    if slide_size_mode == "auto":
        # PPT 全部页面必须共享画布大小。按四舍五入到 0.01 的宽高比聚类，
        # 既容忍同一模板的 1~2px 误差，也不会把 3:2 与 16:9 混在一起。
        from collections import Counter

        ratios = [round(img.width / max(1, img.height), 2) for img in images]
        dominant_ratio, _ = Counter(ratios).most_common(1)[0]
        candidates = [img for img, ratio in zip(images, ratios) if ratio == dominant_ratio]
        # 选该画幅中像素最多的一页作为逻辑尺寸，保留源资料的清晰度语义。
        reference = max(candidates, key=lambda img: img.width * img.height)
        return reference.width, reference.height

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


def _page_fit_qa(
    images: list[Image.Image],
    slide_width_px: int,
    slide_height_px: int,
) -> list[dict]:
    """记录每页的等比适配结果，显式暴露留白而非让它悄悄进入成品。"""
    out: list[dict] = []
    for img in images:
        scale = min(slide_width_px / img.width, slide_height_px / img.height)
        drawn_w = img.width * scale
        drawn_h = img.height * scale
        pad_x = max(0.0, (slide_width_px - drawn_w) / 2.0)
        pad_y = max(0.0, (slide_height_px - drawn_h) / 2.0)
        out.append({
            "source_size_px": [img.width, img.height],
            "scale": round(scale, 5),
            "padding_px": [round(pad_x, 1), round(pad_y, 1)],
            "letterboxed": bool(pad_x > 0.5 or pad_y > 0.5),
        })
    return out


def _build_page_mapping_message(labels: List[str]) -> str:
    lines = [f"页序映射（共 {len(labels)} 页）:"]
    for i, label in enumerate(labels, start=1):
        lines.append(f"  第 {i:>2} 页 ← {label}")
    return "\n".join(lines)


def _collect_page_qa(
    styled: List[dict],
    low_conf_threshold: float,
) -> dict:
    """汇总单页文本块统计：总数 / 可编辑渲染数 / 保留背景数 / 低置信文本。"""
    total = 0
    rendered = 0
    kept_background = 0
    low_conf: List[dict] = []
    for blk in styled:
        text = (blk.get("text") or "").strip()
        if not text:
            continue
        total += 1
        if blk.get("_skip_render") and not blk.get("_erase_only"):
            kept_background += 1
            continue
        rendered += 1
        score = float(blk.get("ocr_score", 1.0) or 1.0)
        if score < low_conf_threshold:
            low_conf.append({"text": text, "ocr_score": round(score, 3)})
    return {
        "text_blocks": total,
        "rendered_blocks": rendered,
        "kept_in_background_blocks": kept_background,
        "low_confidence_blocks": low_conf,
    }


def _write_qa_report(qa: dict, output_path: Union[str, Path]) -> Optional[Path]:
    """把 QA 报告写到 <output>.qa.json；写失败时仅告警不阻断主流程。"""
    qa_path = Path(output_path).with_suffix(".qa.json")
    try:
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        with qa_path.open("w", encoding="utf-8") as f:
            json.dump(qa, f, ensure_ascii=False, indent=2)
        return qa_path
    except OSError:
        return None


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
    slide_size_mode: str = "auto",
    expected_pages: Optional[int] = None,
    qa_report: bool = True,
    qa_low_conf_threshold: float = 0.85,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> Optional[dict]:
    """
    从单图或 PDF 生成可编辑 PPT（复用 banana-slides 的 PPTXBuilder）。
    - input_path: 本地图片/PDF/目录路径，或 http/https 直链（自动下载临时文件后处理）
    - output_path: 输出的 .pptx 路径
    - pdf_output_path: 当 input_path 为目录时，可选输出合并后的 PDF 路径；不传则默认与 ppt 同名 .pdf
    - font_normal/font_bold/font_ea_normal/font_ea_bold: 为 None 时按 **OCR 识别出的内容语言** 自适应
      （中文内容→腾讯字体 W3/W7；英文内容→TencentSans W3/W7；无文本时回退系统语言）。
      比纯按系统语言更稳：Agent/CI 的 shell 常无中文 locale，纯按系统语言会把中文 deck 错配成西文字体。
    - expected_pages: 可选，期望页数；加载后页数不符立即抛 ValueError（在 OCR 之前失败，不烧调用额度）。
    - qa_report: 默认在输出旁写 <output>.qa.json（页序映射、每页文本块统计、低置信文本清单）。
    - qa_low_conf_threshold: OCR 置信度低于该值的文本块计入 QA 低置信清单（默认 0.85）。
    - progress_callback: 可选，(phase, current, total, message) -> None。
      phase 为 "load"|"page"|"export"，current/total 为当前步与总步数，message 为简短说明。

    返回 QA 报告 dict（qa_report=False 时仍返回内存中的报告，仅不落盘）。
    """

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
        labels, images = load_image_entries(resolved_input)
        if not images:
            raise ValueError("未得到任何图片")
        n = len(images)
        report("load", 1, 1, f"已加载 {n} 页")

        # 页数守卫：在 OCR 之前失败，避免页数不对还烧调用额度
        if expected_pages is not None and n != expected_pages:
            raise ValueError(
                f"页数校验失败：期望 {expected_pages} 页，实际加载 {n} 页。"
                "请检查输入是否缺页/多页（可用 --dry-run 查看页序映射）。"
            )

        # 页序映射与命名健康检查：让页序错乱在 OCR 之前暴露
        if n >= 2:
            report("load", 1, 1, _build_page_mapping_message(labels))
            for warn in naming_warnings(labels):
                report("load", 1, 1, f"[命名警告] {warn}")

        report("load", 1, 1, f"OCR 引擎: {selected_engine}")

        # 根据尺寸模式决定 PPT 逻辑画布尺寸
        slide_width_px, slide_height_px = _resolve_slide_size_px(images, slide_size_mode)
        page_fit = _page_fit_qa(images, slide_width_px, slide_height_px)

        if resolved_input.is_dir():
            default_pdf = Path(output_path).with_suffix(".pdf")
            pdf_path = Path(pdf_output_path) if pdf_output_path else default_pdf
            report("load", 1, 1, f"合并目录图片为 PDF…")
            _export_images_to_pdf(images, pdf_path)
            report("load", 1, 1, f"已生成合并 PDF: {pdf_path}")

        slides_data = []
        qa_pages: List[dict] = []
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
            page_qa = _collect_page_qa(styled, qa_low_conf_threshold)
            page_qa["page"] = i + 1
            page_qa["label"] = labels[i] if i < len(labels) else f"page-{i + 1}"
            qa_pages.append(page_qa)

        # 字体兜底推迟到 OCR 之后：未显式指定的项按识别出的内容语言自适应
        # （中文内容→腾讯字体 W3/W7；英文内容→TencentSans W3/W7），
        # 避免 Agent/CI shell 无中文 locale 时把中文 deck 错配成西文字体。
        if not (font_normal and font_bold and font_ea_normal and font_ea_bold):
            sample_text = " ".join(
                (b.get("text") or "")
                for _, styled, _, _ in slides_data
                for b in styled
            )
            _c_normal, _c_bold, _cea_normal, _cea_bold = default_fonts_for_text(sample_text)
            font_normal = font_normal or _c_normal
            font_bold = font_bold or _c_bold
            font_ea_normal = font_ea_normal or _cea_normal
            font_ea_bold = font_ea_bold or _cea_bold
        report(
            "export", 0, n,
            f"字体: latin={font_normal}/{font_bold} ea={font_ea_normal}/{font_ea_bold}",
        )

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

        qa = {
            "tool": "images-2-ppt",
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "input": str(input_path),
            "output": str(output_path),
            "ocr_engine": selected_engine,
            "fonts": {
                "latin_normal": font_normal,
                "latin_bold": font_bold,
                "ea_normal": font_ea_normal,
                "ea_bold": font_ea_bold,
            },
            "page_count": n,
            "slide_size_mode": slide_size_mode,
            "slide_size_px": [slide_width_px, slide_height_px],
            "expected_pages": expected_pages,
            "page_mapping": [
                {"page": i + 1, "label": labels[i] if i < len(labels) else f"page-{i + 1}"}
                for i in range(n)
            ],
            "naming_warnings": naming_warnings(labels) if n >= 2 else [],
            "low_confidence_threshold": qa_low_conf_threshold,
            "pages": qa_pages,
            "summary": {
                "text_blocks": sum(p["text_blocks"] for p in qa_pages),
                "rendered_blocks": sum(p["rendered_blocks"] for p in qa_pages),
                "kept_in_background_blocks": sum(p["kept_in_background_blocks"] for p in qa_pages),
                "low_confidence_blocks": sum(len(p["low_confidence_blocks"]) for p in qa_pages),
            },
        }
        for page, fit in zip(qa["pages"], page_fit):
            page["fit"] = fit
        if qa_report:
            qa_path = _write_qa_report(qa, output_path)
            if qa_path is not None:
                report("export", n, n, f"QA 报告: {qa_path}")
        return qa
    finally:
        if temp_download is not None and temp_download.exists():
            try:
                temp_download.unlink()
            except OSError:
                pass
