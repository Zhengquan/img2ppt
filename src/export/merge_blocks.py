"""文本块的保守合并（同行 + 跨行段落）。

两步合并：

Step 1 — 同行水平合并（`merge_inline_blocks`）：
仅合并同一行上、水平相邻、样式完全一致的短文本块，避免 OCR 把一整句切成多个
零碎文本框带来的"每个框单独折行"问题。

Step 2 — 跨行段落合并（`merge_vertical_paragraphs`）：
在同行合并之后，再把"x 大致对齐、y 紧邻一行内、样式一致"的多个相邻行合并为
一个多行文本框，保留换行。适合 OCR 把一个段落切成 2~3 行独立框的场景。

合并条件（全部满足）：
1. `font_size_pt` 差值 ≤ 0.5pt
2. `color` 完全相同（RGB 元组相等）
3. `bold` 相同
4. y 基线（同行）：两框 y 中心差 ≤ `same_row_ratio × min(height_a, height_b)`
5. x 间隔（同行）：`next.x0 - prev.x1 ≤ max_gap_char_ratio × avg_height`
6. 合并后的 bbox 宽度未超过 `slide_width_px`（若提供）

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
    size_tol_pt: float = 0.5,
    color_tol: int = 0,
) -> bool:
    if bool(a.get("bold", False)) != bool(b.get("bold", False)):
        return False
    if not _color_close(a.get("color"), b.get("color"), color_tol):
        return False
    sa = float(a.get("font_size_pt", 0) or 0)
    sb = float(b.get("font_size_pt", 0) or 0)
    if abs(sa - sb) > size_tol_pt:
        return False
    return True


def merge_inline_blocks(
    styled_blocks: List[dict],
    *,
    same_row_ratio: float = 0.4,
    max_gap_char_ratio: float = 1.5,
    slide_width_px: Optional[int] = None,
) -> List[dict]:
    """按上文规则合并 styled_blocks。原列表不会被修改。

    参数:
        same_row_ratio: 同行判定阈值（与两框较小高度的比例）
        max_gap_char_ratio: 相邻判定阈值（与平均高度的比例）
        slide_width_px: 合并后宽度不得超过此像素上限；None 表示不约束

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

    merged: List[dict] = []
    i = 0
    n = len(enriched)
    while i < n:
        group = [enriched[i]]
        j = i + 1
        while j < n:
            prev = group[-1]
            cur = enriched[j]
            # 必须同一 row_key（先粗筛）
            if cur["row_key"] != prev["row_key"]:
                break
            # y 中心差 ≤ same_row_ratio × min(h)
            if abs(cur["cy"] - prev["cy"]) > same_row_ratio * min(prev["h"], cur["h"]):
                break
            # 样式一致
            if not _same_style(prev["blk"], cur["blk"]):
                break
            # 水平相邻
            avg_height = (prev["h"] + cur["h"]) / 2.0
            gap = cur["x0"] - prev["x1"]
            if gap < 0:
                # 有重叠，直接视为相邻
                pass
            elif gap > max_gap_char_ratio * avg_height:
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
        i = j

    # 把 passthrough 保持相对原序并入（按原 idx 返回接近原来的顺序）
    # 简化处理：把合并结果先放前，再把 passthrough 追加在末尾；
    # PPT 绘制顺序只影响同页图层栈，保序对视觉基本无影响。
    if not passthrough:
        return merged
    result = list(merged)
    for _, blk in passthrough:
        result.append(blk)
    return result


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
    x_align_ratio: float = 2.0,
    line_gap_ratio: float = 1.5,
    size_tol_pt: float = 1.0,
    color_tol: int = 24,
    width_overlap_ratio: float = 0.1,
    slide_width_px: Optional[int] = None,
    slide_height_px: Optional[int] = None,
) -> List[dict]:
    """把同样式、x 左端对齐、y 垂直相邻的多行块合并为一个多行段落。

    条件（全部满足才合并）：
    1. bold 相同、color 每通道差 ≤ `color_tol`、font_size 差 ≤ `size_tol_pt`
       （OCR 对近黑文字常给出 #232629 / #272D34 这类肉眼等价的微差色，
       段落内行间字号也常被判成 15 / 16 的边界值，故适度放宽）
    2. 左端 x0 相差 ≤ `x_align_ratio × avg_height`（左对齐段落）
    3. 下一行 y0 - 上一行 y1 ≤ `line_gap_ratio × avg_height`（行距 ≤ 1 个字高）
    4. 两框水平区间有明显重叠（`重叠/较小宽度 ≥ width_overlap_ratio`），避免
       把同列两段不相关的段落并起来
    5. 合并后的外接矩形不超过页面尺寸（若提供）

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
