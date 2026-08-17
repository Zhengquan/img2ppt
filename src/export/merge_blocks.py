"""文本块的保守合并（同行 + 跨行段落）。

两步合并：

Step 1 — 同行水平合并（`merge_inline_blocks`）：
仅合并同一行上、水平相邻、样式完全一致的短文本块，避免 OCR 把一整句切成多个
零碎文本框带来的"每个框单独折行"问题。

Step 2 — 跨行段落合并（`merge_vertical_paragraphs`）：
在同行合并之后，再把"x 大致对齐、y 紧邻一行内、样式一致"的多个相邻行合并为
一个多行文本框，保留换行。适合 OCR 把一个段落切成 2~3 行独立框的场景。

合并条件（全部满足）：
1. `font_size_pt` 差值 ≤ 0.5pt **且** 较大/较小字号 ≤ 1.10
2. `color` 完全相同（同行合并）或每通道差 ≤ 12（跨行段落）
3. `bold` 相同
4. y 基线（同行）：两框 y 中心差 ≤ `same_row_ratio × min(height_a, height_b)`
5. x 间隔（同行）：`gap ≤ max_gap_char_ratio × avg_height`（默认 0.8）
   **且** `gap ≤ max_gap_abs_px`（默认 ≈ 1.6 × 平均字高）
6. 合并后的 bbox 宽度未超过 `slide_width_px`（若提供）
7. 同行若被判为"表格行"（≥3 个同样式块、gap 均匀），该行禁止同行合并
8. 段落合并时，若两行之间跨越"区域分隔 y"，禁止合并

不合并不同样式。同行合并后 `precise_poly` 置为矩形；段落合并后同样置矩形。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


# —— 列表标号 / 项目符号识别 ——
# OCR 对装饰性的标号字符（01/02/1./①/•/●/■ 等）字号判定不稳，且这些字符
# 通常已经在背景图里以视觉形式呈现；如果再被 PPTXBuilder 重写为文本框，
# 大小不一致、字体不一致的问题就会暴露。我们对识别为"标号块"的 OCR 结果
# 做两件事：
# 1) 不参与同行/跨行段落合并（避免一个 "01" 把后面的标题拽歪）；
# 2) 在 PPT 导出阶段直接跳过渲染（保留背景图中的原始视觉）。

# 项目符号字符集合（无序列表）
_BULLET_CHARS = "•●■▶◆◇○◯◦·*‣⁃▪▫►▸▹◾◽-—–"

# 圆点类项目符号：这类符号会转为 PPT 原生 bullet（buChar + 悬挂缩进）渲染，
# 而不是保留在背景图。原生 bullet 与正文同字体同字号，永远不会错位/压字。
_DOT_BULLET_CHARS = "•●◦·‣⁃▪▫‧・"

# OCR 常把圆点误识别为普通标点（. , ， 、），行首命中时也按圆点 bullet 处理
_OCR_DOT_BULLET_CHARS = _DOT_BULLET_CHARS + ".,，、"

# 原生 bullet 统一渲染为该字符（原图中的 ●/·/◦ 等归一为 •）
_NATIVE_BULLET_CHAR = "•"

# 带圈数字（① ~ ⑳ 等）
_CIRCLED_DIGIT_RANGES = [
    ("\u2460", "\u2473"),  # ① ~ ⑳
    ("\u2776", "\u277f"),  # ❶ ~ ❾
    ("\u24eb", "\u24f4"),  # ⓫ ~ ⓴
]

# 半角/全角括号 + 句点 + 顿号 + 空白
_TRAILING_LIST_PUNCT = ".．。、)）]］>>》】"

# 罗马数字（小写 / 大写）
_ROMAN_RE = re.compile(r"^[ivxlcdmIVXLCDM]{1,4}$")

# 阿拉伯数字编号：1 / 1. / 1) / 01 / 02 / 03 / (1) 等。
# 不能把任意裸 2~3 位数当作序号：信息图中 144、579、150、1000 等业务数值
# 往往是独立 OCR 块；若跳过渲染会残留在底图，再与可编辑文本叠字。
_NUMERIC_LIST_RE = re.compile(
    r"^(?:"
    r"[\(（【\[]?\d{1,3}[\)）】\]\.．、]"  # 带明确尾标点/括号
    r"|0\d{1,2}"                              # 01 / 002 等前导零编号
    r"|\d"                                     # 单数字编号
    r")$"
)

# 信息图中的 SKU、价格、计量值通常独立成框，OCR 对这些短块常只识别出
# 数字或数字+单位；若把它们去字后再由 PPT 重排，最容易出现截断、错位。
# 默认保留这类视觉数值在底图中，优先保证版式保真。
_VISUAL_NUMERIC_RE = re.compile(
    r"^[¥￥]?\d[\d,]*(?:\.\d+)?[+＋]?(?:C|元|万元|亿元|亿|万|%|％|份|个|年|月|天|次|倍)?$",
    re.IGNORECASE,
)

# 中文数字编号：一 / 二 / 三 / 一、 / （一） 等
_CHINESE_NUM_RE = re.compile(
    r"^[\(（]?[一二三四五六七八九十百零〇]{1,4}[\)）、\.．]?$"
)


def _is_circled_digit(text: str) -> bool:
    if len(text) != 1:
        return False
    for lo, hi in _CIRCLED_DIGIT_RANGES:
        if lo <= text <= hi:
            return True
    return False


# 行首"标号前缀"识别：用于"标号 + 标题文字"这种 OCR 把整行识别成一个块的情况。
# 命中时剥离前缀，但不跳过渲染——保留标题文字。
#
# 两种命中形态：
# (A) 标号 + 空白(含全角) + 任意正文 -> 剥离前缀
#     例：'1 对接信随行' -> '对接信随行'，'01. 简介' -> '简介'，'• 要点A' -> '要点A'
# (B) 数字编号(必须 ≥ 2 位 或带尾标点) + 紧贴的中文/英文标题(无空格) -> 剥离前缀
#     例：'01对接信随行' -> '对接信随行'，'1.建设技能市场' -> '建设技能市场'
#     这样可以兼容 OCR 把数字+标题粘在一起的常见情况。
#     注意只匹配 "01"/"02"/.../"1."/"1)" 这种带显著编号特征的，避免把 "1月份"、"3D" 等正常文本误剥。
#
# 中文数字 + 标点（一、二、三、）也允许无空格直接剥离；项目符号同样允许无空格。

_LIST_PREFIX_PUNCT_RE = re.compile(
    r"^("
    r"\(?\d{1,3}[\)\.．、:：]"            # 数字 + 必须有尾部标点: 1. / 1) / 01: ...
    r"|\(\d{1,3}\)"                       # (1)
    r"|[\(（]?[一二三四五六七八九十百零〇]{1,4}[\)）、\.．:：]"  # 中文数字 + 标点
    r"|[" + re.escape(_BULLET_CHARS) + r"]+"  # 项目符号
    r")"
)

# 形态 A：标号 + 空白 + 任意正文
_LIST_PREFIX_WITH_SPACE_RE = re.compile(
    r"^("
    r"\(?\d{1,3}[\)\.．、:：]?"
    r"|[\(（]?[一二三四五六七八九十百零〇]{1,4}[\)）、\.．:：]?"
    r"|[" + re.escape(_BULLET_CHARS) + r"]+"
    r")[\s\u3000]+"
)

# 形态 B：纯数字标号 (如 01/02 两位以上 或 单数字 1) + 紧贴中文标题，无需空白
# 必须紧跟一个中文字符，避免把 "1A"、"3D"、"4G" 等英数缩写误伤
# 进一步：紧跟的中文不能是常见日期/量词单字（月年日次号期类种级等），
# 避免把 "1月份"、"2年后" 误剥。
_LIST_PREFIX_DIGIT_CN_RE = re.compile(
    r"^(\d{1,3})(?=[\u4e00-\u9fff])"
)
_DIGIT_FOLLOW_BLACKLIST = set("月年日次号期类种级楼层届届岁倍点分秒克斤吨升毫")

# OCR 常把无序列表的项目符号（• · ‧ ・ 等）误识别成行首的 "." / "," 等普通标点。
# 这里识别"行首一个项目符号/被误识别的标点 + 紧跟中文正文"的形态，剥离该前缀。
# 仅当后面紧跟中文字符时才剥离，避免误伤 ".NET" / ".5" / 英文缩写等正常文本。
_OCR_BULLET_PREFIX_RE = re.compile(
    r"^[.·•‧・,，、]+[\s\u3000]*(?=[\u4e00-\u9fff])"
)


def _split_list_prefix_text(text: str) -> Tuple[str, int, str]:
    """剥离行首列表前缀，返回 (剥离后的正文, 前缀结束下标, 前缀原文)。

    若未命中前缀，返回 (原文本, 0, "")。
    """
    if not text:
        return text, 0, ""
    # 0) OCR 误识别的项目符号（行首 "." / "·" / "," 等 + 中文正文）
    m = _OCR_BULLET_PREFIX_RE.match(text)
    if m:
        rest = text[m.end():].lstrip()
        if rest:
            return rest, m.end(), text[: m.end()]
    # 1) 标号 + 空白 + 正文
    m = _LIST_PREFIX_WITH_SPACE_RE.match(text)
    if m:
        rest = text[m.end():].lstrip()
        if rest:
            return rest, m.end(), text[: m.end()]
    # 2) 标号 + 显式标点 (1./1)/(1)/一、 等)，后面可有可无空白
    m = _LIST_PREFIX_PUNCT_RE.match(text)
    if m:
        rest = text[m.end():].lstrip()
        if rest:
            return rest, m.end(), text[: m.end()]
    # 3) 纯数字 + 紧贴中文标题：'01对接信随行' -> '对接信随行'
    m = _LIST_PREFIX_DIGIT_CN_RE.match(text)
    if m:
        rest = text[m.end():]
        # 黑名单保护：紧跟的首个中文是常见量词/日期单位时，不剥离
        # 例：'1月份的工作' / '2年后' / '3次会议' 都不被剥离
        if rest and rest[0] not in _DIGIT_FOLLOW_BLACKLIST:
            return rest.lstrip(), m.end(), text[: m.end()]
    return text, 0, ""


def _is_dot_bullet_prefix(prefix_text: str) -> bool:
    """判断剥离出的列表前缀是否为"圆点类"符号（含 OCR 误识别的 . , ， 、）。

    圆点类前缀会转为 PPT 原生 bullet 渲染；数字/中文数字/箭头等装饰性
    标号返回 False，保持"保留背景"的旧行为（它们通常是设计字体，重写会变形）。
    """
    s = (prefix_text or "").strip()
    if not s:
        return False
    return all(ch in _OCR_DOT_BULLET_CHARS for ch in s)


def _is_dot_bullet_marker(text: str) -> bool:
    """判断整段文本是否为孤立的圆点类项目符号（长度 ≤ 3，全为圆点字符）。"""
    s = (text or "").strip()
    if not s or len(s) > 3:
        return False
    return all(ch in _DOT_BULLET_CHARS for ch in s)


def _strip_list_prefix(text: str) -> str:
    """剥离行首的列表标号 / 项目符号前缀，返回剩余文本。

    若整行就是一个标号（无后续正文），由 `_is_list_marker` 单独处理跳过渲染。
    """
    return _split_list_prefix_text(text)[0]


def _is_list_marker(text: str) -> bool:
    """判断一段 OCR 文本是否为"列表标号 / 项目符号"——即整段文字本身就是个标号。

    命中的块在合并阶段会被剔除（不参与合并），并在 PPT 导出阶段跳过渲染。
    返回 True 时调用方应给该块打 `_skip_render=True`。

    覆盖：
    - 项目符号字符（• ● ■ ▶ ◆ ◦ · 等），单字符或多字符全为符号
    - 阿拉伯数字编号：1 / 1. / 1) / 01 / 02 / (1) 等（最多 3 位）
    - 带圈数字：① ~ ⑳、❶ ~ ❾、⓫ ~ ⓴
    - 中文数字编号：一、 / （二） / 三. 等

    注：罗马数字（i/ii/IV）容易和图标里的孤立字母（V/C/I）混淆，
    这里**不再**把任意 1~4 个 ivxlcdm 字母都当成标号；只匹配明确带尾部
    标号字符（点/括号/顿号）的情况，避免误伤标题中的单字母图标。
    """
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    # 太长就不可能是孤立的标号
    if len(s) > 6:
        return False

    # 1) 全部由项目符号字符构成（如 "•"、"●●"、"-" 等）
    if all(ch in _BULLET_CHARS for ch in s):
        return True

    # 2) 单字符的带圈数字
    if _is_circled_digit(s):
        return True

    # 3) 阿拉伯数字编号
    if _NUMERIC_LIST_RE.match(s):
        return True

    # 4) 罗马数字 + 必须带明确的尾部标号符号（避免把孤立大写字母 C/V/I 误判）
    if len(s) >= 2:
        s_no_punct = s.rstrip(_TRAILING_LIST_PUNCT)
        if (
            s_no_punct
            and len(s_no_punct) < len(s)  # 必须真的有尾部标点被剥离
            and _ROMAN_RE.match(s_no_punct)
        ):
            return True

    # 5) 中文数字编号
    if _CHINESE_NUM_RE.match(s):
        return True

    return False


# —— 图标内装饰字符识别 ——
# 信息图里的图标（盾牌/齿轮/代码块等）内部常带有孤立的字母 / 符号（如 C、Y、W、</>），
# OCR 会把它们识别成文本块。这些是图标的一部分，应保留在背景图上，不应重写为文本框
# （重写会出现字体/大小不一致、错位、与图标重影等问题）。
_ICON_SYMBOL_RE = re.compile(r"^[<>/{}\[\]()|\\~^`@#$%&*=+]+$")


def _is_icon_glyph(text: str) -> bool:
    """判断一段 OCR 文本是否为"图标内的装饰字符"。

    覆盖：
    - 纯符号组合（如 "</>"、"<>"、"{}"），长度 ≤ 4
    - 单个非数字字符（单字母 C/Y/W、单符号、单汉字如 "品" 等）——
      单字符极少作为可编辑正文出现，多为图标 logo 字符；即便偶有误判，
      也只是"保留原图"，视觉不丢失，仅损失该字符的可编辑性。

    注：单个数字已由 `_is_list_marker`（阿拉伯数字编号）覆盖，这里不重复处理。
    """
    s = (text or "").strip()
    if not s:
        return False
    if len(s) <= 4 and _ICON_SYMBOL_RE.match(s):
        return True
    if len(s) == 1 and not s.isdigit():
        return True
    return False


def _should_keep_in_background(text: str) -> bool:
    """判断整个块是否应只保留在背景图上、不重写为文本框。

    只覆盖"整块本身就是视觉元素"的场景：
    - 孤立列表标号 / 项目符号（"01"、"•"、"①"、"一、" 等）
    - 图标内装饰字符（"C"、"Y"、"</>"、"品" 等）
    - SKU、价格、数量等独立视觉数值（如 "1500C"、"75元"、"1,000+份"）

    注意："标号 + 正文"同块时，不应整块保留；应只保留标号区域，正文继续转为可编辑文字。
    """
    s = (text or "").strip()
    if not s:
        return False
    return (
        _is_list_marker(s)
        or _is_icon_glyph(s)
        or bool(_VISUAL_NUMERIC_RE.match(s))
    )


def _shift_block_left(blk: dict, prefix_len: int) -> dict:
    """把 bbox 左边界右移到列表正文附近，只抹除/渲染正文区域。

    OCR 经常把"项目符号/数字圆点 + 正文"识别成一个整体框。为了既保留原图上的
    项目符号，又让正文可编辑，需要把框左边界略向右移动。

    关键：右移量要"宁可偏左一点"。序号与正文之间通常有视觉间隙，因此在按字符宽
    估出的序号宽度基础上，再向左回退一个安全余量（落在间隙里）。这样能保证正文
    左缘被完整抹除/覆盖（不残留半个原字），又不会吃掉序号本体——修复"有序号时
    背景遮盖不完整"的问题。
    """
    box = blk.get("box")
    text = (blk.get("text") or "").strip()
    if not box or not text or prefix_len <= 0:
        return dict(blk)
    x0, y0, x1, y1 = _box_bounds(box)
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    char_w = w / max(1, len(text))
    # 序号宽度估计：字符宽 × 前缀字符数，并对窄字符给一个温和下限（不再用 0.9h 这种偏大的值）
    est_prefix = max(char_w * prefix_len, h * 0.5)
    # 向左回退安全余量：落在序号与正文之间的间隙，确保正文左缘被完整覆盖
    backoff = max(char_w * 0.6, h * 0.2)
    shift = max(0.0, est_prefix - backoff)
    # 留至少 1px 宽度，避免异常窄框
    new_x0 = min(x1 - 1.0, x0 + shift)
    nb = dict(blk)
    nb["box"] = _rect_to_box(new_x0, y0, x1, y1)
    nb["precise_poly"] = nb["box"]
    return nb


def _find_bullet_pair_target(
    marker_box: List[List[float]],
    candidates: List[dict],
) -> Optional[int]:
    """为孤立圆点块找右侧同行的正文块，返回 candidates 下标；找不到返回 None。

    配对条件：y 中心差 ≤ 0.6 × 较小高度（同行），正文块在圆点右侧，
    水平间隙 ≤ 1.2 × 圆点高度（圆点与正文之间的正常间隙）。
    """
    mx0, my0, mx1, my1 = _box_bounds(marker_box)
    mh = max(1.0, my1 - my0)
    mcy = (my0 + my1) / 2.0
    best_idx: Optional[int] = None
    best_gap: Optional[float] = None
    for idx, blk in enumerate(candidates):
        if blk.get("_skip_render") or blk.get("_bullet_char"):
            continue
        text = (blk.get("text") or "").strip()
        box = blk.get("box")
        if not text or not box:
            continue
        x0, y0, x1, y1 = _box_bounds(box)
        h = max(1.0, y1 - y0)
        cy = (y0 + y1) / 2.0
        if abs(cy - mcy) > 0.6 * min(h, mh):
            continue
        gap = x0 - mx1
        if gap < -0.5 * mh:  # 允许轻微重叠，但正文不能整体在圆点左侧
            continue
        if gap > 1.2 * mh:
            continue
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_idx = idx
    return best_idx


def mark_background_blocks(blocks: List[dict]) -> List[dict]:
    """在【去字重建之前】处理列表/图标视觉元素。

    圆点类项目符号（• · ● ◦ 等，含 OCR 误识别的 . , ， 、）→ **PPT 原生 bullet**：
    - "圆点 + 正文"同块：剥离圆点，整块抹除重写，正文块打 `_bullet_char`；
    - 孤立圆点块：与右侧同行正文块配对——正文块 bbox 左扩到圆点处、打 `_bullet_char`，
      圆点块打 `_skip_render + _erase_only`（从背景抹除但不渲染）；
      配对失败时退回旧行为（保留背景）。

    其它标号（数字 01、带圈数字、中文数字、箭头等装饰性标号）保持旧行为：
    - 孤立标号 / 图标字符：`_skip_render=True`，原样保留在背景图；
    - "标号 + 正文"同块：剥离前缀、右移 bbox，只抹除/重写正文区域。

    返回新列表（不修改入参）。
    """
    out: List[dict] = []
    dot_marker_idxs: List[int] = []  # out 中孤立圆点块的下标

    for blk in blocks:
        if blk.get("_skip_render"):
            out.append(blk)
            continue
        text = (blk.get("text") or "").strip()
        if not text:
            out.append(blk)
            continue
        # 孤立圆点符号：先登记，第二趟配对
        if _is_dot_bullet_marker(text) and blk.get("box"):
            nb = dict(blk)
            nb["_skip_render"] = True
            dot_marker_idxs.append(len(out))
            out.append(nb)
            continue
        if _should_keep_in_background(text):
            nb = dict(blk)
            nb["_skip_render"] = True
            out.append(nb)
            continue
        stripped, prefix_len, prefix_text = _split_list_prefix_text(text)
        if prefix_len > 0 and stripped and stripped != text:
            if _is_dot_bullet_prefix(prefix_text):
                # 圆点 + 正文同块 → 原生 bullet：整块抹除重写，不右移 bbox
                nb = dict(blk)
                nb["text"] = stripped
                nb["_bullet_char"] = _NATIVE_BULLET_CHAR
                nb["_no_merge"] = True
                out.append(nb)
            else:
                nb = _shift_block_left(blk, prefix_len)
                nb["text"] = stripped
                nb["_no_merge"] = True
                out.append(nb)
            continue
        out.append(blk)

    # 第二趟：孤立圆点与右侧同行正文配对
    for m_idx in dot_marker_idxs:
        marker = out[m_idx]
        target_idx = _find_bullet_pair_target(marker["box"], out)
        if target_idx is None:
            continue  # 配对失败：保留背景（旧行为）
        target = dict(out[target_idx])
        mx0, my0, mx1, my1 = _box_bounds(marker["box"])
        tx0, ty0, tx1, ty1 = _box_bounds(target["box"])
        # 渲染 bbox 左扩到圆点左缘（文本框含 bullet 悬挂缩进区），
        # precise_poly 保持原正文区域用于去字；圆点区域由 marker 块自己抹除
        target["box"] = _rect_to_box(min(mx0, tx0), ty0, tx1, ty1)
        target["_bullet_char"] = _NATIVE_BULLET_CHAR
        target["_no_merge"] = True
        out[target_idx] = target
        marker = dict(marker)
        marker["_erase_only"] = True  # 圆点从背景抹除（已由原生 bullet 接管渲染）
        out[m_idx] = marker

    return out


def _split_list_markers(blocks: List[dict]) -> Tuple[List[dict], List[dict]]:
    """把孤立视觉元素从合并候选里剔除，并兜底处理列表前缀。

    - `_skip_render=True` 或孤立标号/图标字符：剔除，不参与合并、不渲染。
    - "标号 + 正文"同块：剥离前缀、右移 bbox，正文保留渲染但打 `_no_merge=True`。

    返回 (剩余可合并/可渲染块, 被剔除的保留原图块)。
    """
    keep: List[dict] = []
    skipped: List[dict] = []
    for blk in blocks:
        # 已在去字前标记保留原图 -> 直接剔除
        if blk.get("_skip_render"):
            skipped.append(blk)
            continue
        text = (blk.get("text") or "").strip()
        if not text:
            keep.append(blk)
            continue
        if _should_keep_in_background(text):
            new_blk = dict(blk)
            new_blk["_skip_render"] = True
            skipped.append(new_blk)
            continue
        stripped, prefix_len, _prefix_text = _split_list_prefix_text(text)
        if prefix_len > 0 and stripped and stripped != text:
            new_blk = _shift_block_left(blk, prefix_len)
            new_blk["text"] = stripped
            new_blk["_no_merge"] = True
            keep.append(new_blk)
            continue
        keep.append(blk)
    return keep, skipped


def _box_bounds(box: List[List[float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _rect_to_box(x0: float, y0: float, x1: float, y1: float) -> List[List[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _color_tuple(c) -> Tuple[int, int, int]:
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return int(c[0]), int(c[1]), int(c[2])
    return 0, 0, 0


def _color_close(a, b, tol: int) -> bool:
    """每通道最大差 ≤ tol 则视为同色。tol=0 等价于完全相等。"""
    ca = _color_tuple(a)
    cb = _color_tuple(b)
    if tol <= 0:
        return ca == cb
    return max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]), abs(ca[2] - cb[2])) <= tol


def _same_style(
    a: dict,
    b: dict,
    *,
    size_tol_pt: float = 1.0,
    color_tol: int = 0,
    size_ratio_max: float = 1.18,
) -> bool:
    """样式一致判定。

    字号差使用"绝对差 + 比值"双门槛：
      - 绝对差 ≤ `size_tol_pt`（默认 1.0pt，允许字号估计有小波动）
      - 较大字号 / 较小字号 ≤ `size_ratio_max`（默认 1.18，约 18%）

    这两道门槛共同起作用：比值门槛在大字号时防跨档（17vs20 会触发比值 >1.17
    被挡），绝对差在小字号时防噪声（8pt 估算 ±1 就很常见，比值但不绝对差可能
    过松）。

    注：比值 1.18 是在"字号估计本身带较大噪声"下的折衷：过严（<1.10）会把
    同段正文两行误判为不同字号；过松（>1.25）会把 h3 小标题并进正文。
    若上游 `infer_styles` 后续换成更稳的字号估计，可把该阈值收到 1.10。
    """
    if bool(a.get("bold", False)) != bool(b.get("bold", False)):
        return False
    if not _color_close(a.get("color"), b.get("color"), color_tol):
        return False
    sa = float(a.get("font_size_pt", 0) or 0)
    sb = float(b.get("font_size_pt", 0) or 0)
    if abs(sa - sb) > size_tol_pt:
        return False
    # 字号比值门槛：仅当两者都有效时生效
    if sa > 0 and sb > 0:
        ratio = max(sa, sb) / max(1e-6, min(sa, sb))
        if ratio > size_ratio_max:
            return False
    return True


def merge_inline_blocks(
    styled_blocks: List[dict],
    *,
    same_row_ratio: float = 0.4,
    max_gap_char_ratio: float = 0.8,
    max_gap_abs_px: Optional[float] = None,
    slide_width_px: Optional[int] = None,
    table_row_min_count: int = 3,
    table_row_gap_var_ratio: float = 0.35,
) -> List[dict]:
    """按上文规则合并 styled_blocks。原列表不会被修改。

    参数:
        same_row_ratio: 同行判定阈值（与两框较小高度的比例）
        max_gap_char_ratio: 相邻判定阈值（与平均高度的比例，默认 0.8，约 1 个字宽以内）
        max_gap_abs_px: gap 的绝对像素硬上限。None 时按 `1.6 × 候选平均高度` 动态取
            （保守：再宽不会超过约 2 个中文字符宽度）
        slide_width_px: 合并后宽度不得超过此像素上限；None 表示不约束
        table_row_min_count: 当同一行里存在 ≥ N 个"样式一致、gap 近似均匀"的候选时，
            判为表格行并禁止在该行做合并（防止表头被连成一条）
        table_row_gap_var_ratio: 判定"gap 均匀"的相对方差阈值。

    返回: 合并后的新 list；输入中缺少 box 或 text 的条目原样透传。
    被识别为"列表标号 / 项目符号"的块会被打上 `_skip_render=True` 透传，
    既不参与合并，也会在 PPT 导出阶段被跳过渲染。
    """
    if not styled_blocks:
        return list(styled_blocks)

    # 先把"列表标号 / 项目符号"剔除：不参与合并，标记 _skip_render
    remaining, list_marker_skipped = _split_list_markers(list(styled_blocks))

    # 拆成可合并候选 + 不可合并原样保留
    candidates: List[dict] = []
    passthrough: List[Tuple[int, dict]] = []
    for idx, blk in enumerate(remaining):
        if (
            not blk.get("box")
            or not (blk.get("text") or "").strip()
            or blk.get("_no_merge")  # 列表项：保留渲染但不参与合并
        ):
            passthrough.append((idx, blk))
            continue
        candidates.append(blk)

    if not candidates:
        # 注意把列表标号块也带回，保证调用方能感知 _skip_render 标记
        return list(remaining) + list_marker_skipped

    # 预计算每个候选的 bounds + 高度
    enriched = []
    for blk in candidates:
        x0, y0, x1, y1 = _box_bounds(blk["box"])
        enriched.append({
            "blk": blk,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "h": max(1.0, y1 - y0),
            "cy": (y0 + y1) / 2.0,
        })

    # 按 y 中心粗分桶（按平均高度分），桶内再按 x0 排序
    avg_h = sum(e["h"] for e in enriched) / len(enriched)
    bucket_size = max(1.0, avg_h)
    for e in enriched:
        e["row_key"] = int(round(e["cy"] / bucket_size))

    # 同一 row_key 再结合 y 中心差动态判定
    enriched.sort(key=lambda e: (e["row_key"], e["x0"]))

    # —— 预扫描：标记"疑似表格行"的 row_key。
    # 规则：同一 row_key 下，样式完全一致的相邻块 ≥ N 个，且相邻 gap 相对方差较小 → 判为表格。
    table_row_keys = _detect_table_rows(
        enriched,
        min_count=table_row_min_count,
        gap_var_ratio=table_row_gap_var_ratio,
    )

    merged: List[dict] = []
    i = 0
    n = len(enriched)
    while i < n:
        group = [enriched[i]]
        j = i + 1
        # 表格行里任何块都不参与同行合并（直接输出原块）
        in_table_row = enriched[i]["row_key"] in table_row_keys
        while j < n and not in_table_row:
            prev = group[-1]
            cur = enriched[j]
            # 必须同一 row_key（先粗筛）
            if cur["row_key"] != prev["row_key"]:
                break
            # 若后续进入了表格行，也停
            if cur["row_key"] in table_row_keys:
                break
            # y 中心差 ≤ same_row_ratio × min(h)
            if abs(cur["cy"] - prev["cy"]) > same_row_ratio * min(prev["h"], cur["h"]):
                break
            # 样式一致
            if not _same_style(prev["blk"], cur["blk"]):
                break
            # 水平相邻（比例 + 绝对值双重约束）
            avg_height = (prev["h"] + cur["h"]) / 2.0
            gap = cur["x0"] - prev["x1"]
            gap_abs_cap = (
                max_gap_abs_px
                if max_gap_abs_px is not None
                else 1.6 * avg_height
            )
            if gap < 0:
                # 有重叠，直接视为相邻
                pass
            elif gap > max_gap_char_ratio * avg_height or gap > gap_abs_cap:
                break
            # 合并后宽度不超过页面
            if slide_width_px is not None:
                new_x1 = max(prev["x1"], cur["x1"])
                new_x0 = min(prev["x0"], cur["x0"])
                if new_x1 - new_x0 > slide_width_px:
                    break
            group.append(cur)
            j += 1

        if len(group) == 1:
            merged.append(group[0]["blk"])
        else:
            merged.append(_merge_group([g["blk"] for g in group], group))
        i = j if j > i else i + 1

    # 把 passthrough 保持相对原序并入（按原 idx 返回接近原来的顺序）
    # 简化处理：把合并结果先放前，再把 passthrough 追加在末尾；
    # PPT 绘制顺序只影响同页图层栈，保序对视觉基本无影响。
    result = list(merged)
    for _, blk in passthrough:
        result.append(blk)
    # 列表标号块追加在末尾（带 _skip_render=True，下游会跳过）
    result.extend(list_marker_skipped)
    return result


def _detect_table_rows(
    enriched: List[dict],
    *,
    min_count: int,
    gap_var_ratio: float,
) -> set:
    """识别"疑似表格行"的 row_key 集合。

    启发式：对每个 row_key，取该行内样式完全一致（严格同字号 + 同色 + 同粗细）
    的相邻块序列；若同行里存在 ≥ `min_count` 个这样的块，且相邻 gap 的方差 /
    均值 < `gap_var_ratio`（gap 近似均匀），则判为表格行。
    """
    from collections import defaultdict

    by_row: dict = defaultdict(list)
    for e in enriched:
        by_row[e["row_key"]].append(e)

    table_rows = set()
    for rk, items in by_row.items():
        if len(items) < min_count:
            continue
        # 按 x0 排
        items_sorted = sorted(items, key=lambda x: x["x0"])
        # 在该行内找"连续同样式"的最长段
        best_run: List[dict] = []
        run: List[dict] = [items_sorted[0]]
        for k in range(1, len(items_sorted)):
            if _same_style(run[-1]["blk"], items_sorted[k]["blk"]):
                run.append(items_sorted[k])
            else:
                if len(run) > len(best_run):
                    best_run = run
                run = [items_sorted[k]]
        if len(run) > len(best_run):
            best_run = run
        if len(best_run) < min_count:
            continue
        # 计算相邻 gap 的方差 / 均值
        gaps = [
            best_run[k + 1]["x0"] - best_run[k]["x1"]
            for k in range(len(best_run) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < min_count - 1:
            continue
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap <= 0:
            continue
        var = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
        std = var ** 0.5
        if std / mean_gap < gap_var_ratio:
            table_rows.add(rk)
    return table_rows


def _merge_group(blks: List[dict], enriched_group: List[dict]) -> dict:
    texts = [(b.get("text") or "").strip() for b in blks]
    merged_text = " ".join(t for t in texts if t)
    x0 = min(e["x0"] for e in enriched_group)
    y0 = min(e["y0"] for e in enriched_group)
    x1 = max(e["x1"] for e in enriched_group)
    y1 = max(e["y1"] for e in enriched_group)
    base = dict(blks[0])  # 继承第一条的样式
    base["text"] = merged_text
    base["box"] = _rect_to_box(x0, y0, x1, y1)
    # precise_poly 合并后不再精确，置为矩形外轮廓，便于下游去字仍可工作
    base["precise_poly"] = _rect_to_box(x0, y0, x1, y1)
    return base


def merge_vertical_paragraphs(
    styled_blocks: List[dict],
    *,
    x_align_ratio: float = 1.2,
    line_gap_ratio: float = 0.9,
    size_tol_pt: float = 1.0,
    color_tol: int = 12,
    width_overlap_ratio: float = 0.3,
    slide_width_px: Optional[int] = None,
    slide_height_px: Optional[int] = None,
    section_gap_ratio: float = 2.0,
) -> List[dict]:
    """把同样式、x 左端对齐、y 垂直相邻的多行块合并为一个多行段落。

    条件（全部满足才合并）：
    1. bold 相同、color 每通道差 ≤ `color_tol`、font_size 差 ≤ `size_tol_pt`
       且较大/较小字号 ≤ 1.10（见 `_same_style`）
    2. 左端 x0 相差 ≤ `x_align_ratio × avg_height`（左对齐段落）
    3. 下一行 y0 - 上一行 y1 ≤ `line_gap_ratio × avg_height`（行距 ≤ ~1 个字高）
    4. 两框水平区间有明显重叠（`重叠/较小宽度 ≥ width_overlap_ratio`），避免
       把同列两段不相关的段落并起来
    5. 合并后的外接矩形不超过页面尺寸（若提供）
    6. **视觉分隔保护**：按页面所有候选的相邻 y-gap 分布估出"区域分隔线"，
       分隔线两侧不允许跨段合并（参数 `section_gap_ratio` = 分隔判据 / 中位 gap）

    输出的多行块 text 用 "\n" 连接，保留换行；`precise_poly` 用矩形外轮廓。
    被识别为"列表标号 / 项目符号"的块会被打上 `_skip_render=True` 透传，
    既不参与跨行段落合并，也会在 PPT 导出阶段被跳过渲染。
    """
    if not styled_blocks:
        return list(styled_blocks)

    # 在剔除列表标号之前，先记录它们的 y 位置。项目符号本身会保留在
    # 背景图中，因此不能参与文本合并；但它恰好也是列表项之间最可靠的
    # 视觉边界。若直接剔除，连续的同样式列表正文会被误合并成一个多行
    # 文本框，继而出现跨项目换行、挤压或叠字。
    list_marker_dividers = _list_marker_dividers(styled_blocks)

    # 再剔除列表标号 / 项目符号，不参与段落合并
    remaining, list_marker_skipped = _split_list_markers(list(styled_blocks))

    # 拆分可合并 vs 透传
    candidates: List[dict] = []
    passthrough: List[dict] = []
    for blk in remaining:
        if (
            not blk.get("box")
            or not (blk.get("text") or "").strip()
            or blk.get("_no_merge")  # 列表项：保留渲染但不参与合并
        ):
            passthrough.append(blk)
            continue
        candidates.append(blk)
    if not candidates:
        return list(remaining) + list_marker_skipped

    enriched = []
    for blk in candidates:
        x0, y0, x1, y1 = _box_bounds(blk["box"])
        enriched.append({
            "blk": blk,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "h": max(1.0, y1 - y0),
            "w": max(1.0, x1 - x0),
        })

    # —— 预扫描：识别"区域分隔 y 坐标"。
    # 取所有候选按 y0 排序后的相邻行间 gap（仅正值），若某个 gap >
    # `section_gap_ratio × median(gap)`，则在这两行之间建立一条硬分隔线。
    section_dividers = _detect_section_dividers(
        enriched,
        section_gap_ratio=section_gap_ratio,
    )
    # 列表标号与通用区域分隔线共同构成硬边界。去重、排序可避免同一
    # 标号被 OCR 拆成多个小块时反复扫描。
    section_dividers = sorted(set(section_dividers + list_marker_dividers))

    # 按 x0 粗聚类 + y0 升序；左对齐聚类后再按垂直顺序扫描
    enriched.sort(key=lambda e: (round(e["x0"] / 4), e["y0"]))

    used = [False] * len(enriched)
    merged: List[dict] = []

    def _overlap_ratio(a, b) -> float:
        left = max(a["x0"], b["x0"])
        right = min(a["x1"], b["x1"])
        if right <= left:
            return 0.0
        return (right - left) / min(a["w"], b["w"])

    def _crosses_divider(y_top: float, y_bot: float) -> bool:
        for dy in section_dividers:
            # 列表标号顶部通常与下一项正文 y0 重合，故下边界使用
            # <=；否则会在“刚好齐平”时漏掉分隔，继续跨项目合并。
            if y_top < dy <= y_bot:
                return True
        return False

    for i in range(len(enriched)):
        if used[i]:
            continue
        group = [enriched[i]]
        used[i] = True
        changed = True
        while changed:
            changed = False
            last = group[-1]
            # 在剩余未用候选里，挑一个最合适的"下一行"
            best_j = -1
            best_gap = None
            for j in range(len(enriched)):
                if used[j]:
                    continue
                cand = enriched[j]
                # 必须在当前段之下
                if cand["y0"] < last["y1"] - 1:
                    continue
                # 视觉分隔保护：不允许跨分隔线
                if _crosses_divider(last["y1"], cand["y0"]):
                    continue
                # 样式一致
                if not _same_style(
                    last["blk"],
                    cand["blk"],
                    size_tol_pt=size_tol_pt,
                    color_tol=color_tol,
                ):
                    continue
                avg_h = (last["h"] + cand["h"]) / 2.0
                # 左端对齐
                if abs(cand["x0"] - group[0]["x0"]) > x_align_ratio * avg_h:
                    continue
                # 行距
                gap = cand["y0"] - last["y1"]
                if gap > line_gap_ratio * avg_h:
                    continue
                # 水平区间有重叠
                if _overlap_ratio(last, cand) < width_overlap_ratio:
                    continue
                # 合并后不超页面
                new_x0 = min(group[0]["x0"], cand["x0"])
                new_y0 = min(group[0]["y0"], cand["y0"])
                new_x1 = max(max(e["x1"] for e in group), cand["x1"])
                new_y1 = max(max(e["y1"] for e in group), cand["y1"])
                if slide_width_px is not None and new_x1 - new_x0 > slide_width_px:
                    continue
                if slide_height_px is not None and new_y1 - new_y0 > slide_height_px:
                    continue
                # 选 y 最近的
                if best_gap is None or gap < best_gap:
                    best_gap = gap
                    best_j = j
            if best_j >= 0:
                group.append(enriched[best_j])
                used[best_j] = True
                changed = True

        if len(group) == 1:
            merged.append(group[0]["blk"])
        else:
            merged.append(_merge_paragraph_group([g["blk"] for g in group], group))

    # 透传块追加在末尾，保序；列表标号块带 _skip_render=True 一并附加
    return merged + passthrough + list_marker_skipped


def _list_marker_dividers(styled_blocks: List[dict]) -> List[float]:
    """返回孤立列表标号的顶部 y 坐标，供段落合并作为硬分隔。

    仅使用真正的列表标号，避免图标、SKU 等同样被保留在背景中的元素
    误把无关正文切断。标号顶部通常与其所属正文首行齐平，因此它不会
    阻碍该项目内部的后续续行；但会阻止上一个项目跨越此处合并到本项。
    """
    dividers: List[float] = []
    for blk in styled_blocks:
        text = (blk.get("text") or "").strip()
        box = blk.get("box")
        if not box or not _is_list_marker(text):
            continue
        _, y0, _, _ = _box_bounds(box)
        dividers.append(y0)
    return dividers


def _detect_section_dividers(
    enriched: List[dict],
    *,
    section_gap_ratio: float,
) -> List[float]:
    """基于所有候选按 y0 排序的相邻 gap 分布，找出"区域分隔" y 坐标。

    返回的列表是若干"分隔 y"，任何跨越该 y 的合并都会被禁止。
    """
    if len(enriched) < 3:
        return []
    items = sorted(enriched, key=lambda e: e["y0"])
    gaps = []
    for k in range(1, len(items)):
        g = items[k]["y0"] - items[k - 1]["y1"]
        if g > 0:
            gaps.append(g)
    if not gaps:
        return []
    gaps_sorted = sorted(gaps)
    median = gaps_sorted[len(gaps_sorted) // 2]
    if median <= 0:
        return []
    threshold = section_gap_ratio * median
    dividers: List[float] = []
    for k in range(1, len(items)):
        g = items[k]["y0"] - items[k - 1]["y1"]
        if g > threshold:
            dividers.append((items[k - 1]["y1"] + items[k]["y0"]) / 2.0)
    return dividers


def _merge_paragraph_group(blks: List[dict], enriched_group: List[dict]) -> dict:
    """多行段落合并：text 用 "\n" 连接，bbox 取外接矩形。"""
    lines = [(b.get("text") or "").strip() for b in blks]
    merged_text = "\n".join(t for t in lines if t)
    x0 = min(e["x0"] for e in enriched_group)
    y0 = min(e["y0"] for e in enriched_group)
    x1 = max(e["x1"] for e in enriched_group)
    y1 = max(e["y1"] for e in enriched_group)
    base = dict(blks[0])
    base["text"] = merged_text
    base["box"] = _rect_to_box(x0, y0, x1, y1)
    base["precise_poly"] = _rect_to_box(x0, y0, x1, y1)
    return base
