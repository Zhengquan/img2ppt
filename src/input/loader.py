"""输入适配：单图加载或 PDF 逐页转图，统一输出「图片列表」；支持 http/https 图片或 PDF 直链自动下载。"""
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

import requests
from PIL import Image


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}

_NATURAL_SORT_RE = re.compile(r"(\d+)")

# 文件名尾部编号：slide-001 / page12 / 003 等
_TRAILING_NUM_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")

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


def natural_sort_key(name: str) -> Tuple:
    """数字感知的自然排序键：slide-2 < slide-10；非数字段按小写文本比较。"""
    parts = _NATURAL_SORT_RE.split(name.lower())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def list_dir_image_files(path: Union[str, Path]) -> List[Path]:
    """列出目录下所有图片文件，按自然排序（数字感知）返回。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"目录不存在: {path}")
    if not path.is_dir():
        raise ValueError(f"不是目录: {path}")

    files = sorted(
        [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES],
        key=lambda p: natural_sort_key(p.name),
    )
    if not files:
        raise ValueError(f"目录中未找到图片文件（支持: {sorted(_IMAGE_SUFFIXES)}）: {path}")
    return files


def naming_warnings(labels: List[str]) -> List[str]:
    """目录图片命名健康检查，返回警告列表（空列表 = 无异常）。

    检测三类常见事故形态：
    1. 混合命名：部分文件带尾部编号、部分不带（如 slide-001.png 与 ChatGPT Image xxx.png 混放）；
    2. 多种编号前缀：slide-001 与 page-002 混放，排序结果大概率不符合预期页序；
    3. 编号缺口 / 重复：slide-001..014 中缺 005 或同一编号出现两次。
    """
    warnings: List[str] = []
    if len(labels) < 2:
        return warnings

    stems = [Path(label).stem for label in labels]
    with_num: List[Tuple[str, str, int]] = []  # (stem, prefix, num)
    without_num: List[str] = []
    for stem in stems:
        m = _TRAILING_NUM_RE.match(stem)
        if m:
            with_num.append((stem, m.group("prefix"), int(m.group("num"))))
        else:
            without_num.append(stem)

    if without_num and with_num:
        warnings.append(
            f"命名不统一：{len(with_num)} 个文件带尾部编号，{len(without_num)} 个不带编号"
            f"（如 {without_num[0]!r}）。不带编号的文件会按文件名排进页序，"
            "大概率导致页序错乱；请统一改为连续编号（如 slide-001.png …）。"
        )
    elif without_num and not with_num:
        # 全部无编号：无法校验顺序，仅提示
        warnings.append(
            "所有文件名均不带尾部数字编号，页序完全依赖文件名字典序，"
            "请确认排序结果与预期页序一致（建议改为 slide-001.png 形式的连续编号）。"
        )

    if len(with_num) >= 2:
        prefixes = {p for _, p, _ in with_num}
        if len(prefixes) > 1:
            warnings.append(
                f"存在 {len(prefixes)} 种编号前缀（{sorted(prefixes)}），"
                "不同前缀的文件会分别聚在一起，页序很可能错乱。"
            )
        nums = sorted(n for _, _, n in with_num)
        seen = set()
        dups = sorted({n for n in nums if n in seen or seen.add(n)})
        if dups:
            warnings.append(f"编号重复：{dups}（同一编号出现多次，可能存在重复页）。")
        if not dups and len(prefixes) == 1 and not without_num:
            lo, hi = nums[0], nums[-1]
            missing = [n for n in range(lo, hi + 1) if n not in set(nums)]
            if missing:
                warnings.append(
                    f"编号不连续：{lo}~{hi} 之间缺 {missing}（可能有漏页）。"
                )
    return warnings


def _load_images_from_dir(path: Union[str, Path]) -> List[Image.Image]:
    return [_load_single_image(p) for p in list_dir_image_files(path)]


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


def list_input_labels(path: Union[str, Path]) -> List[str]:
    """不解码图片，仅列出输入对应的页标签列表（用于 --dry-run 与页序预览）。

    - 目录 → 自然排序后的文件名列表
    - PDF → ["<stem>#p1", "<stem>#p2", ...]（需要 PyMuPDF 数页数）
    - 单张图片 → [文件名]
    """
    path = Path(path)
    if path.is_dir():
        return [p.name for p in list_dir_image_files(path)]
    suf = path.suffix.lower()
    if suf == ".pdf":
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PDF 支持需要 PyMuPDF，请执行: pip install PyMuPDF")
        doc = fitz.open(path)
        n = len(doc)
        doc.close()
        return [f"{path.stem}#p{i + 1}" for i in range(n)]
    if suf in _IMAGE_SUFFIXES:
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return [path.name]
    raise ValueError(f"不支持的格式: {suf}，支持 .pdf 或常见图片格式")


def load_image_entries(path: Union[str, Path]) -> Tuple[List[str], List[Image.Image]]:
    """加载输入，返回 (页标签列表, 图片列表)。标签用于页序映射与 QA 报告。"""
    labels = list_input_labels(path)
    images = load_images(path)
    return labels, images


def load_images(path: Union[str, Path]) -> List[Image.Image]:
    """
    根据路径类型加载为图片列表。
    - 单张图片（.png/.jpg/.jpeg/.bmp/.webp 等）→ 返回含一张图的列表
    - PDF（.pdf）→ 返回每页一张图的列表
    - 目录 → 读取目录下所有图片并按自然排序（数字感知），返回多张图的列表
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
