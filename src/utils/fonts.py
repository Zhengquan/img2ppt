"""根据系统语言环境挑选默认字体。

策略：
- 系统语言为中文（zh*） → latin + ea 均使用 `腾讯字体 W3/W7`
- 其他（英文等）        → latin + ea 均使用 `TencentSans W3/W7`

调用方在 CLI/API 层若用户未显式指定字体，才使用这里的默认值。
"""
from __future__ import annotations

import locale
import os
from typing import Tuple


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

    - 中文环境：latin + ea 都用 `腾讯字体 W3/W7`
    - 英文/其他环境：latin + ea 都用 `TencentSans W3/W7`
    """
    lang = _detect_system_lang()
    is_chinese = lang.startswith("zh")
    if is_chinese:
        normal = "腾讯字体 W3"
        bold = "腾讯字体 W7"
    else:
        normal = "TencentSans W3"
        bold = "TencentSans W7"
    return normal, bold, normal, bold
