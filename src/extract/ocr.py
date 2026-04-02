"""OCR 统一入口：支持腾讯/百度云端识别，输出标准化文本框格式。"""
import base64
import hashlib
import hmac
import io
import json
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

# 腾讯 OCR 接口
TENCENT_OCR_ENDPOINT = "https://ocr.tencentcloudapi.com"
TENCENT_OCR_HOST = "ocr.tencentcloudapi.com"
TENCENT_OCR_ACTION = "GeneralAccurateOCR"
TENCENT_OCR_VERSION = "2018-11-19"
TENCENT_OCR_SERVICE = "ocr"

# 内存缓存 access_token，避免每次请求都拉取
_cached_token: str = ""
_cached_token_expires_at: float = 0
TOKEN_CACHE_BUFFER_SEC = 300  # 提前 5 分钟刷新

# 与 .env.example 中占位符一致；视为「未配置」，避免误用示例值调用云端
_OCR_PLACEHOLDER_VALUES = frozenset(
    {
        "your-api-key",
        "your-secret-key",
        "your-secret-id",
        "changeme",
        "placeholder",
        "xxx",
        "todo",
    }
)


def _env_value_is_configured(value: str) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    low = s.lower()
    if low in _OCR_PLACEHOLDER_VALUES:
        return False
    return True


def ocr_env_setup_help() -> str:
    """缺少有效 OCR 配置时给终端用户 / Agent 的说明文本。"""
    return (
        "未检测到有效的 OCR 密钥配置（.env 缺失、为空或为 .env.example 中的占位符）。\n"
        "请先向用户索取至少一种云端 OCR 凭据，在仓库根目录执行：cp .env.example .env\n"
        "再编辑 .env 填入真实值（勿提交 .env 到版本库）：\n"
        "  腾讯（推荐）：TENCENT_OCR_SECRET_ID、TENCENT_OCR_SECRET_KEY；可选 TENCENT_OCR_REGION\n"
        "  百度：BAIDU_OCR_API_KEY、BAIDU_OCR_SECRET_KEY\n"
        "配置完成后再运行：python cli.py -i <输入> -o <输出.pptx>\n"
        "文档：README.md、.env.example"
    )


def _read_image_bytes(image: Union[Image.Image, str, Path]) -> bytes:
    if isinstance(image, (str, Path)):
        with open(image, "rb") as f:
            return f.read()
    if isinstance(image, Image.Image):
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    raise TypeError("image 须为 PIL.Image、文件路径或 Path")


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _is_baidu_configured(api_key: str = "", secret_key: str = "") -> bool:
    resolved_key = (api_key or os.environ.get("BAIDU_OCR_API_KEY", "")).strip()
    resolved_secret = (secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY", "")).strip()
    return _env_value_is_configured(resolved_key) and _env_value_is_configured(resolved_secret)


def _is_tencent_configured(secret_id: str = "", secret_key: str = "") -> bool:
    resolved_id = (secret_id or os.environ.get("TENCENT_OCR_SECRET_ID", "")).strip()
    resolved_key = (secret_key or os.environ.get("TENCENT_OCR_SECRET_KEY", "")).strip()
    return _env_value_is_configured(resolved_id) and _env_value_is_configured(resolved_key)


def _resolve_ocr_engine(
    ocr_engine: str,
    baidu_ready: bool,
    tencent_ready: bool,
) -> str:
    engine = (ocr_engine or "auto").strip().lower()
    if engine not in {"auto", "tencent", "baidu"}:
        raise ValueError("ocr_engine 仅支持 auto、tencent、baidu")

    if engine == "auto":
        if tencent_ready:
            return "tencent"
        if baidu_ready:
            return "baidu"
        raise ValueError(
            "未检测到可用 OCR 配置。请至少配置一种引擎："
            "腾讯(TENCENT_OCR_SECRET_ID/TENCENT_OCR_SECRET_KEY) 或 "
            "百度(BAIDU_OCR_API_KEY/BAIDU_OCR_SECRET_KEY)。"
        )

    if engine == "tencent" and not tencent_ready:
        raise ValueError(
            "已指定 tencent，但未配置腾讯 OCR。请设置 "
            "TENCENT_OCR_SECRET_ID 与 TENCENT_OCR_SECRET_KEY。"
        )
    if engine == "baidu" and not baidu_ready:
        raise ValueError(
            "已指定 baidu，但未配置百度 OCR。请设置 "
            "BAIDU_OCR_API_KEY 与 BAIDU_OCR_SECRET_KEY。"
        )
    return engine


def resolve_ocr_engine(
    ocr_engine: str = "auto",
    api_key: str = None,
    secret_key: str = None,
    tencent_secret_id: str = None,
    tencent_secret_key: str = None,
) -> str:
    """根据参数与环境变量解析本次实际使用的 OCR 引擎。"""
    baidu_api_key = (api_key or os.environ.get("BAIDU_OCR_API_KEY", "")).strip()
    baidu_secret_key = (secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY", "")).strip()
    tx_secret_id = (tencent_secret_id or os.environ.get("TENCENT_OCR_SECRET_ID", "")).strip()
    tx_secret_key = (tencent_secret_key or os.environ.get("TENCENT_OCR_SECRET_KEY", "")).strip()
    return _resolve_ocr_engine(
        ocr_engine=ocr_engine,
        baidu_ready=_is_baidu_configured(baidu_api_key, baidu_secret_key),
        tencent_ready=_is_tencent_configured(tx_secret_id, tx_secret_key),
    )


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
    """解析百度返回为统一格式。"""
    out: List[Tuple[List[List[float]], str, float, List[List[float]]]] = []
    words_result = data.get("words_result") or []
    for item in words_result:
        text = (item.get("words") or "").strip()
        loc = item.get("location") or {}
        left = float(loc.get("left", 0))
        top = float(loc.get("top", 0))
        width = float(loc.get("width", 0))
        height = float(loc.get("height", 0))
        box = [
            [left, top],
            [left + width, top],
            [left + width, top + height],
            [left, top + height],
        ]
        prob = item.get("probability", {})
        score = float(prob.get("average", 1.0)) if isinstance(prob, dict) else 1.0
        out.append((box, text, score, list(box)))
    return out


def _parse_tencent_result(
    data: dict,
) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
    """解析腾讯返回为统一格式。"""
    out: List[Tuple[List[List[float]], str, float, List[List[float]]]] = []
    text_detections = data.get("TextDetections") or []
    for item in text_detections:
        text = (item.get("DetectedText") or "").strip()
        polygon = item.get("Polygon") or []
        precise_poly: List[List[float]] = []
        for p in polygon:
            precise_poly.append([float(p.get("X", 0)), float(p.get("Y", 0))])

        if len(precise_poly) >= 4:
            box = precise_poly[:4]
        else:
            rect = item.get("ItemPolygon") or {}
            x = float(rect.get("X", 0))
            y = float(rect.get("Y", 0))
            w = float(rect.get("Width", 0))
            h = float(rect.get("Height", 0))
            box = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            precise_poly = list(box)

        score = float(item.get("Confidence", 100.0)) / 100.0
        out.append((box, text, score, precise_poly))
    return out


def _run_baidu_ocr(raw: bytes, api_key: str, secret_key: str) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
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


def _run_tencent_ocr(raw: bytes, secret_id: str, secret_key: str, region: str) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
    payload = {"ImageBase64": base64.b64encode(raw).decode("ascii")}
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    canonical_headers = "content-type:application/json; charset=utf-8\nhost:ocr.tencentcloudapi.com\n"
    signed_headers = "content-type;host"
    canonical_request = (
        "POST\n/\n\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{_sha256_hex(payload_json)}"
    )

    credential_scope = f"{date}/{TENCENT_OCR_SERVICE}/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{_sha256_hex(canonical_request)}"
    )

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = hmac.new(secret_date, TENCENT_OCR_SERVICE.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": TENCENT_OCR_HOST,
        "X-TC-Action": TENCENT_OCR_ACTION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": TENCENT_OCR_VERSION,
        "X-TC-Region": region,
    }
    resp = requests.post(TENCENT_OCR_ENDPOINT, data=payload_json.encode("utf-8"), headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    response = body.get("Response", {})
    if "Error" in response:
        err = response.get("Error", {})
        raise RuntimeError(
            f"腾讯 OCR 调用失败: {err.get('Message', err)} (code={err.get('Code', 'Unknown')})"
        )
    return _parse_tencent_result(response)


def run_ocr(
    image: Union[Image.Image, str, Path],
    api_key: str = None,
    secret_key: str = None,
    tencent_secret_id: str = None,
    tencent_secret_key: str = None,
    tencent_region: str = None,
    ocr_engine: str = "auto",
) -> List[Tuple[List[List[float]], str, float, List[List[float]]]]:
    """
    OCR 统一入口，支持腾讯/百度并输出统一格式。

    引擎选择:
    - auto: 优先腾讯，其次百度
    - tencent: 强制腾讯
    - baidu: 强制百度
    """
    baidu_api_key = (api_key or os.environ.get("BAIDU_OCR_API_KEY", "")).strip()
    baidu_secret_key = (secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY", "")).strip()
    tx_secret_id = (tencent_secret_id or os.environ.get("TENCENT_OCR_SECRET_ID", "")).strip()
    tx_secret_key = (tencent_secret_key or os.environ.get("TENCENT_OCR_SECRET_KEY", "")).strip()
    tx_region = (tencent_region or os.environ.get("TENCENT_OCR_REGION", "ap-guangzhou")).strip()

    selected_engine = resolve_ocr_engine(
        ocr_engine=ocr_engine,
        api_key=baidu_api_key,
        secret_key=baidu_secret_key,
        tencent_secret_id=tx_secret_id,
        tencent_secret_key=tx_secret_key,
    )

    raw = _read_image_bytes(image)
    if selected_engine == "tencent":
        return _run_tencent_ocr(raw, tx_secret_id, tx_secret_key, tx_region)
    return _run_baidu_ocr(raw, baidu_api_key, baidu_secret_key)
