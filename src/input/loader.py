"""输入适配：单图加载或 PDF 逐页转图，统一输出「图片列表」；支持 http/https 图片或 PDF 直链自动下载。"""
import io
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import List, Union
from urllib.parse import unquote, urlparse

import requests
from PIL import Image


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}

# 远程下载与建议的单文件输入上限（与 SKILL 说明一致）；过大易导致 OCR/对话上下文溢出
MAX_INPUT_DOWNLOAD_BYTES = 4 * 1024 * 1024

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


def _suffix_after_download(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return ".pdf"
    try:
        im = Image.open(io.BytesIO(data))
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


def download_url_to_temp(url: str, max_bytes: int = MAX_INPUT_DOWNLOAD_BYTES) -> Path:
    """
    下载 URL 到临时文件并返回路径。仅支持 PDF 或常见位图；流式读取并限制总大小。
    """
    url = url.strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as r:
        r.raise_for_status()
        chunks: List[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                mb = max_bytes // (1024 * 1024)
                raise ValueError(
                    f"下载大小超过 {mb}MB 限制。请先压缩或换用较小文件；过大图片还会导致对话上下文溢出。"
                )
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("下载内容为空")
    suffix = _suffix_after_download(data)
    fd, path_str = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            os.unlink(path_str)
        except OSError:
            pass
        raise
    return Path(path_str)


def _load_single_image(path: Union[str, Path]) -> Image.Image:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    sz = path.stat().st_size
    if sz > MAX_INPUT_DOWNLOAD_BYTES:
        mb = MAX_INPUT_DOWNLOAD_BYTES // (1024 * 1024)
        raise ValueError(
            f"图片文件超过 {mb}MB 限制（当前约 {sz / (1024 * 1024):.2f}MB）。请先压缩；过大图片易导致上下文溢出。"
        )
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
