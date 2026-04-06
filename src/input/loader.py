"""输入适配：单图加载或 PDF 逐页转图，统一输出「图片列表」；支持 http/https 图片或 PDF 直链自动下载。"""
import tempfile
from pathlib import Path, PurePosixPath
from typing import List, Union
from urllib.parse import unquote, urlparse

import requests
from PIL import Image


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}

_PIL_FORMAT_TO_EXT = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "GIF": ".gif",
    "TIFF": ".tiff",
    "TIF": ".tiff",
}


def is_http_url(value: Union[str, Path]) -> bool:
    s = str(value).strip()
    return s.startswith(("http://", "https://"))


def suggest_output_pptx_path(raw_input: str) -> Path:
    """未指定 -o 时的默认 .pptx 路径：URL 用路径中的文件名 stem，否则用本地路径改后缀。"""
    raw = raw_input.strip()
    if is_http_url(raw):
        stem = PurePosixPath(unquote(urlparse(raw).path)).stem
        stem = stem or "remote_input"
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in stem)[:120]
        return Path(f"{safe}.pptx")
    return Path(raw).with_suffix(".pptx")


def _suffix_after_download_path(file_path: Path) -> str:
    with open(file_path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"%PDF"):
        return ".pdf"
    try:
        im = Image.open(file_path)
        im.load()
    except Exception as e:
        raise ValueError(
            "下载内容既不是 PDF 也不是可解码的图片。请使用直接指向图片或 PDF 文件的链接（非网页预览页）。"
        ) from e
    fmt = (im.format or "PNG").upper()
    ext = _PIL_FORMAT_TO_EXT.get(fmt)
    if not ext:
        raise ValueError(f"不支持的图片格式: {fmt or 'unknown'}")
    return ext


def download_url_to_temp(url: str) -> Path:
    """
    下载 URL 到临时文件并返回路径。仅支持 PDF 或常见位图；流式写入磁盘，不设体积上限（本机 CLI 场景）。
    """
    url = url.strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
        tmp_path = Path(tmp.name)
        with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    tmp.write(chunk)
    try:
        if tmp_path.stat().st_size == 0:
            tmp_path.unlink(missing_ok=True)
            raise ValueError("下载内容为空")
        suf = _suffix_after_download_path(tmp_path)
        final = tmp_path.with_suffix(suf)
        if final != tmp_path:
            tmp_path.rename(final)
            tmp_path = final
        return tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_single_image(path: Union[str, Path]) -> Image.Image:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    img = Image.open(path).convert("RGB")
    return img


def _load_images_from_dir(path: Union[str, Path]) -> List[Image.Image]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"目录不存在: {path}")
    if not path.is_dir():
        raise ValueError(f"不是目录: {path}")

    files = sorted(
        [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES],
        key=lambda p: p.name.lower(),
    )
    if not files:
        raise ValueError(f"目录中未找到图片文件（支持: {sorted(_IMAGE_SUFFIXES)}）: {path}")

    return [_load_single_image(p) for p in files]


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
    - 目录 → 读取目录下所有图片并按文件名排序，返回多张图的列表
    """
    path = Path(path)
    if path.is_dir():
        return _load_images_from_dir(path)
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _load_pdf_pages(path)
    if suf in _IMAGE_SUFFIXES:
        return [_load_single_image(path)]
    raise ValueError(f"不支持的格式: {suf}，支持 .pdf 或常见图片格式")
