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

from typing import List, Optional, Tuple


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
    """
    if not styled_blocks:
        return list(styled_blocks)

    # 拆成可合并候选 + 不可合并原样保留
    candidates: List[dict] = []
    passthrough: List[Tuple[int, dict]] = []
    for idx, blk in enumerate(styled_blocks):
        if not blk.get("box") or not (blk.get("text") or "").strip():
            passthrough.append((idx, blk))
            continue
        candidates.append(blk)

    if not candidates:
        return list(styled_blocks)

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
    if not passthrough:
        return merged
    result = list(merged)
    for _, blk in passthrough:
        result.append(blk)
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
    """
    if not styled_blocks:
        return list(styled_blocks)

    # 拆分可合并 vs 透传
    candidates: List[dict] = []
    passthrough: List[dict] = []
    for blk in styled_blocks:
        if not blk.get("box") or not (blk.get("text") or "").strip():
            passthrough.append(blk)
            continue
        candidates.append(blk)
    if not candidates:
        return list(styled_blocks)

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
            if y_top < dy < y_bot:
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

    # 透传块追加在末尾，保序
    return merged + passthrough


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
