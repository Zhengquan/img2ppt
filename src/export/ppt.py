"""PPT 导出：每张图对应一页，背景图 + 文本框。复用 banana-slides 的 PPTXBuilder。"""
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Union, Tuple

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from PIL import Image

from ..utils.pptx_builder import PPTXBuilder


# 默认幻灯片尺寸（英寸，16:9 宽屏），与 banana-slides 一致
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
# 幻灯片逻辑像素（96 DPI），用于 PPTXBuilder
SLIDE_WIDTH_PX = int(SLIDE_WIDTH_IN * 96)
SLIDE_HEIGHT_PX = int(SLIDE_HEIGHT_IN * 96)


def _styled_block_to_text_style(blk: dict) -> object:
    """将本项目的 styled_block 转为 PPTXBuilder 可用的 text_style 对象（兼容 banana-slides TextStyleResult）。"""
    color = blk.get("color", (0, 0, 0))
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        font_color_rgb = (int(color[0]), int(color[1]), int(color[2]))
    else:
        font_color_rgb = (0, 0, 0)
    return type(
        "TextStyle",
        (),
        {
            "font_color_rgb": font_color_rgb,
            "is_bold": bool(blk.get("bold", False)),
            "is_italic": False,
            "is_underline": False,
            "text_alignment": None,
            "colored_segments": None,
        },
    )()


def _px_to_inches(x_px: float, y_px: float, img_w: int, img_h: int, slide_w_in: float, slide_h_in: float) -> tuple:
    """将像素坐标按「图→幻灯片」比例换算为英寸。"""
    scale_x = slide_w_in / max(1, img_w)
    scale_y = slide_h_in / max(1, img_h)
    return (x_px * scale_x, y_px * scale_y)


def _box_bounds(box: List[List[float]]) -> tuple:
    """返回 (x_min, y_min, x_max, y_max)。"""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return (min(xs), min(ys), max(xs), max(ys))


def build_editable_pptx(
    slides_data: List[Tuple[Image.Image, List[dict], int, int]],
    output_path: Union[str, Path],
    dpi: int = 96,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
) -> None:
    """
    使用 banana-slides 的 PPTXBuilder 生成可编辑 PPTX。
    每页：干净背景图 + 按 bbox 放置的文本框（可编辑）。
    slides_data: [(background_image, styled_blocks, img_w, img_h), ...]
    progress_callback: 可选，(phase, current, total, message) -> None。
    """
    builder = PPTXBuilder()
    builder.setup_presentation_size(SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX, dpi=dpi)
    builder.create_presentation()
    total = len(slides_data)

    for idx, (background_image, styled_blocks, img_w, img_h) in enumerate(slides_data):
        if progress_callback:
            progress_callback("export", idx + 1, total, "写入幻灯片")
        scale_x = SLIDE_WIDTH_PX / max(1, img_w)
        scale_y = SLIDE_HEIGHT_PX / max(1, img_h)
        slide = builder.add_blank_slide()

        # 背景图：临时保存后插入并移到底层
        if isinstance(background_image, (str, Path)):
            bg_path = str(background_image)
            need_unlink = False
        else:
            fd = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            background_image.save(fd.name, format="PNG")
            bg_path = fd.name
            need_unlink = True
        try:
            pic = slide.shapes.add_picture(
                bg_path, Inches(0), Inches(0),
                width=Inches(builder.slide_width_inches),
                height=Inches(builder.slide_height_inches),
            )
            spTree = slide.shapes._spTree
            spTree.remove(pic._element)
            spTree.insert(2, pic._element)
        finally:
            if need_unlink:
                Path(bg_path).unlink(missing_ok=True)

        for blk in styled_blocks:
            box = blk.get("box")
            text = (blk.get("text") or "").strip()
            if not box or not text:
                continue
            x0, y0, x1, y1 = _box_bounds(box)
            bbox_slide = [
                int(x0 * scale_x),
                int(y0 * scale_y),
                int(x1 * scale_x),
                int(y1 * scale_y),
            ]
            text_style = _styled_block_to_text_style(blk)
            text_level = "title" if blk.get("bold") else "default"
            try:
                builder.add_text_element(
                    slide=slide,
                    text=text,
                    bbox=bbox_slide,
                    text_level=text_level,
                    dpi=dpi,
                    align="left",
                    text_style=text_style,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Skip text element %s: %s", text[:30], e)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    builder.save(str(output_path))


def add_slide_from_image_and_blocks(
    prs: Presentation,
    background_image: Union[Image.Image, str, Path],
    styled_blocks: List[dict],
    img_w: int,
    img_h: int,
    slide_width_in: float = SLIDE_WIDTH_IN,
    slide_height_in: float = SLIDE_HEIGHT_IN,
    font_normal: str = "Tencent Sans W3",
    font_bold: str = "Tencent Sans W7",
) -> None:
    """
    向 prs 追加一页：以 background_image 为全页底图，再按 styled_blocks 在对应位置画文本框。
    styled_blocks 每项: {"box", "text", "bold", "color", "font_size_pt"}。
    """
    blank = prs.slide_layouts[6]  # 空白
    slide = prs.slides.add_slide(blank)

    if isinstance(background_image, (str, Path)):
        img_path = str(background_image)
    else:
        fp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        background_image.save(fp.name, format="PNG")
        img_path = fp.name
    try:
        pic = slide.shapes.add_picture(
            img_path, Inches(0), Inches(0),
            width=Inches(slide_width_in),
            height=Inches(slide_height_in),
        )
        # 将背景图片移到最底层（通过操作 XML 元素）
        spTree = slide.shapes._spTree
        spTree.remove(pic._element)
        spTree.insert(2, pic._element)  # 索引 2：在 nvGrpSpPr 和 grpSpPr 之后
    finally:
        if isinstance(background_image, Image.Image):
            Path(img_path).unlink(missing_ok=True)

    for blk in styled_blocks:
        box = blk.get("box")
        text = blk.get("text", "").strip()
        if not box or not text:
            continue
        bold = bool(blk.get("bold", False))
        color = blk.get("color", (0, 0, 0))
        font_size_pt = float(blk.get("font_size_pt", 18))
        x0, y0, x1, y1 = _box_bounds(box)
        # 文本框左上角与宽高（英寸）
        left_in, top_in = _px_to_inches(x0, y0, img_w, img_h, slide_width_in, slide_height_in)
        w_in = max(0.1, (x1 - x0) / max(1, img_w) * slide_width_in)
        h_in = max(0.1, (y1 - y0) / max(1, img_h) * slide_height_in)
        tb = slide.shapes.add_textbox(
            Inches(left_in), Inches(top_in),
            Inches(w_in), Inches(h_in),
        )
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.clear()
        run = p.add_run()
        run.text = text
        run.font.size = Pt(font_size_pt)
        run.font.bold = bold
        run.font.name = font_bold if bold else font_normal
        if isinstance(color, (list, tuple)) and len(color) >= 3:
            run.font.color.rgb = RGBColor(color[0], color[1], color[2])
