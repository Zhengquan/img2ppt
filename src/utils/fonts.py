"""默认字体挑选。

策略：
- `default_fonts_for_text`（优先）：按 OCR 识别出的文本内容语言选择——
  中文内容 → `腾讯字体 W3/W7`；英文内容 → `TencentSans W3/W7`。
  不受运行 shell 的 locale 影响（Agent/CI 常无中文 locale）。
- `default_fonts`（回退）：无文本可判定时按系统语言环境选择。

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


def default_fonts_for_text(text: str) -> Tuple[str, str, str, str]:
    """按识别出的文本内容选择默认字体族（比系统语言更贴近幻灯片实际内容）。

    - CJK 字符占主导 → `腾讯字体 W3/W7`（中文幻灯片，即使进程 LANG 非中文也正确）
    - 否则 → `TencentSans W3/W7`
    - 文本为空 → 回退到系统语言判定 `default_fonts()`

    背景：Agent / CI 等非交互 shell 常无中文 locale（LANG=C/en_US），
    纯按系统语言会把中文 deck 的东亚字体错配成 TencentSans。
    """
    if not (text or "").strip():
        return default_fonts()
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    is_chinese = cjk >= 8 and cjk * 2 >= latin
    if is_chinese:
        return "腾讯字体 W3", "腾讯字体 W7", "腾讯字体 W3", "腾讯字体 W7"
    return "TencentSans W3", "TencentSans W7", "TencentSans W3", "TencentSans W7"
