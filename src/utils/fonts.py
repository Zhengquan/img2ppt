"""默认字体挑选。

策略：
- 统一使用英文字体名 `TencentSans W3/W7` 作为 latin 与东亚字体名。
  腾讯字体在不同系统上注册名不一致（中文名「腾讯字体 W3」/「腾讯体 W3」/
  英文名「TencentSans W3」），而 python-pptx 写入的 typeface 必须与目标
  机器已安装的字体族名精确匹配才能命中。英文名 `TencentSans W3/W7` 在
  macOS / Windows / Linux 的 fontconfig 中均作为主名注册（fc-list 输出
  `TencentSans,腾讯体,TencentSans W3,腾讯体 W3`），兼容性最好。
- `default_fonts_for_text`（优先）：按 OCR 识别出的文本内容语言决定是否
  同时写入中文别名——但 latin/ea 字段统一用英文名，避免跨平台命中失败。
  目前简化为始终用 `TencentSans W3/W7`（该字体本身含 CJK 字形）。
- `default_fonts`（回退）：无文本可判定时同上。

调用方在 CLI/API 层若用户未显式指定字体，才使用这里的默认值。
"""
from __future__ import annotations

import locale
import os
from typing import Tuple


# 腾讯字体的统一英文名（fc-list 在 macOS/Linux 上注册的主名）。
# 该字体同时包含拉丁与 CJK 字形，latin 与 ea 字段均用此名即可。
_FONT_NORMAL = "TencentSans W3"
_FONT_BOLD = "TencentSans W7"


def _detect_system_lang() -> str:
    """返回系统首选语言的小写前缀（如 "zh"、"en"）。未知时返回 "" 。"""
    # 优先级：LC_ALL > LC_MESSAGES > LANG，其次用 locale.getlocale()
    for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(env, "")
        if v:
            # 形如 zh_CN.UTF-8 / en_US.UTF-8 / C / POSIX
            code = v.split(".")[0].split("_")[0].strip().lower()
            if code and code not in ("c", "posix"):
                return code
    try:
        code, _ = locale.getlocale()
        if code:
            return code.split("_")[0].strip().lower()
    except Exception:
        pass
    return ""


def default_fonts() -> Tuple[str, str, str, str]:
    """返回 (font_normal, font_bold, font_ea_normal, font_ea_bold)。

    统一使用英文字体名 `TencentSans W3/W7`（含 CJK 字形），
    确保 macOS / Windows / Linux 跨平台均能命中已安装的腾讯字体。
    """
    return _FONT_NORMAL, _FONT_BOLD, _FONT_NORMAL, _FONT_BOLD


def default_fonts_for_text(text: str) -> Tuple[str, str, str, str]:
    """按识别出的文本内容选择默认字体族。

    目前统一返回 `TencentSans W3/W7`（该字体同时含拉丁与 CJK 字形，
    英文名在跨平台 fontconfig 中注册最稳定）。

    保留 text 参数与函数签名，便于未来按内容语言切换不同字体族时扩展。
    """
    return _FONT_NORMAL, _FONT_BOLD, _FONT_NORMAL, _FONT_BOLD
