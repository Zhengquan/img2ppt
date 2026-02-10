"""OCR：百度云端高精度文字识别（含位置），输出每个文本块的四点坐标与文本、置信度。"""
import base64
import io
import os
import time
from pathlib import Path
from typing import List, Tuple, Union

import requests
from PIL import Image

# 加载 .env（若存在）
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

# 百度 OCR 接口
BAIDU_OCR_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_OCR_ACCURATE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate"

# 内存缓存 access_token，避免每次请求都拉取
_cached_token: str = ""
_cached_token_expires_at: float = 0
TOKEN_CACHE_BUFFER_SEC = 300  # 提前 5 分钟刷新


def _get_access_token(api_key: str, secret_key: str) -> str:
    """获取百度 OCR access_token，带简单内存缓存。"""
    global _cached_token, _cached_token_expires_at
    now = time.time()
    if _cached_token and now < _cached_token_expires_at - TOKEN_CACHE_BUFFER_SEC:
        return _cached_token
    resp = requests.post(
        BAIDU_OCR_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"百度 Token 获取失败: {data.get('error_description', data)}")
    _cached_token = data["access_token"]
    _cached_token_expires_at = now + int(data.get("expires_in", 2592000))
    return _cached_token


def _parse_baidu_result(
    data: dict,
) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
    """
    将百度高精度含位置版 OCR 响应解析为 (box_4pts, text, score, precise_poly) 列表。
    百度返回 location: {left, top, width, height}，转为四点矩形。
    """
    out: List[Tuple[List[List[float]], str, float, List[List[float]]]] = []
    words_result = data.get("words_result") or []
    for item in words_result:
        text = (item.get("words") or "").strip()
        loc = item.get("location") or {}
        left = float(loc.get("left", 0))
        top = float(loc.get("top", 0))
        width = float(loc.get("width", 0))
        height = float(loc.get("height", 0))
        # 四点矩形：左上、右上、右下、左下
        box = [
            [left, top],
            [left + width, top],
            [left + width, top + height],
            [left, top + height],
        ]
        prob = item.get("probability", {})
        if isinstance(prob, dict) and "average" in prob:
            score = float(prob["average"])
        else:
            score = 1.0
        out.append((box, text, score, list(box)))
    return out


def run_ocr(
    image: Union[Image.Image, str, Path],
    api_key: str = None,
    secret_key: str = None,
) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
    """
    对单张图片调用百度云端「通用文字识别（高精度含位置版）」。
    返回 [(box_4pts, text, confidence, precise_poly), ...]，与下游 style/infer 兼容。

    鉴权从环境变量读取：BAIDU_OCR_API_KEY、BAIDU_OCR_SECRET_KEY（或在 .env 中配置）。
    """
    api_key = api_key or os.environ.get("BAIDU_OCR_API_KEY", "").strip()
    secret_key = secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise ValueError(
            "请配置百度 OCR：在 .env 或环境变量中设置 BAIDU_OCR_API_KEY 和 BAIDU_OCR_SECRET_KEY。"
            "在百度智能云控制台创建应用并开通「通用文字识别（高精度含位置版）」后获取。"
        )

    if isinstance(image, (str, Path)):
        with open(image, "rb") as f:
            raw = f.read()
    elif isinstance(image, Image.Image):
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        raw = buf.getvalue()
    else:
        raise TypeError("image 须为 PIL.Image、文件路径或 Path")

    image_b64 = base64.b64encode(raw).decode("ascii")
    token = _get_access_token(api_key, secret_key)
    resp = requests.post(
        f"{BAIDU_OCR_ACCURATE_URL}?access_token={token}",
        data={"image": image_b64},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error_code" in body:
        raise RuntimeError(
            f"百度 OCR 调用失败: {body.get('error_msg', body)} (error_code={body.get('error_code')})"
        )
    return _parse_baidu_result(body)
