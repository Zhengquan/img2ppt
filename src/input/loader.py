"""输入适配：单图加载或 PDF 逐页转图，统一输出「图片列表」。"""
from pathlib import Path
from typing import List, Union

from PIL import Image


def _load_single_image(path: Union[str, Path]) -> Image.Image:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    img = Image.open(path).convert("RGB")
    return img


def _load_pdf_pages(path: Union[str, Path]) -> List[Image.Image]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PDF 支持需要 PyMuPDF，请执行: pip install PyMuPDF")
    doc = fitz.open(path)
    images: List[Image.Image] = []
    for i in range(len(doc)):
        page = doc[i]
        pix = page.get_pixmap(dpi=150, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    doc.close()
    return images


def load_images(path: Union[str, Path]) -> List[Image.Image]:
    """
    根据路径类型加载为图片列表。
    - 单张图片（.png/.jpg/.jpeg/.bmp/.webp 等）→ 返回含一张图的列表
    - PDF（.pdf）→ 返回每页一张图的列表
    """
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _load_pdf_pages(path)
    if suf in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}:
        return [_load_single_image(path)]
    raise ValueError(f"不支持的格式: {suf}，支持 .pdf 或常见图片格式")
