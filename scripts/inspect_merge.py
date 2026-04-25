"""
合并决策的逐条诊断脚本（只读，不影响主流程）。

对单张图走一遍 OCR + style 抽取，然后模拟 `merge_inline_blocks` 和
`merge_vertical_paragraphs` 的判据，为每对候选打印：

  [INLINE]   accept/reject, row_key, gap, avg_h, gap/avg_h, style_ok, reason
  [SECTION]  y-divider 列表
  [PARA]     accept/reject, gap, avg_h, x_align, style_ok, reason

目的：调阈值前后都跑一遍，比对看哪些"错合"被拦住了、哪些"正确合并"被误杀。

用法：
  python3 scripts/inspect_merge.py tmp/pics/slide-004.png
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src.extract.ocr import run_ocr  # noqa: E402
from src.extract.style import infer_styles  # noqa: E402
from src.export.merge_blocks import (  # noqa: E402
    _box_bounds,
    _same_style,
    _detect_table_rows,
    _detect_section_dividers,
)


def _short(t: str, n: int = 18) -> str:
    t = (t or "").replace("\n", " ").strip()
    return (t[:n] + "…") if len(t) > n else t


def _enrich(blocks: List[dict]) -> List[dict]:
    out = []
    for blk in blocks:
        if not blk.get("box") or not (blk.get("text") or "").strip():
            continue
        x0, y0, x1, y1 = _box_bounds(blk["box"])
        out.append({
            "blk": blk,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "h": max(1.0, y1 - y0),
            "w": max(1.0, x1 - x0),
            "cy": (y0 + y1) / 2.0,
            "text": blk.get("text", ""),
        })
    return out


def inspect_inline(enriched: List[dict]) -> None:
    if not enriched:
        return
    avg_h = sum(e["h"] for e in enriched) / len(enriched)
    bucket = max(1.0, avg_h)
    for e in enriched:
        e["row_key"] = int(round(e["cy"] / bucket))
    enriched.sort(key=lambda e: (e["row_key"], e["x0"]))

    table_rows = _detect_table_rows(enriched, min_count=3, gap_var_ratio=0.35)
    print(f"\n=== [INLINE] avg_h={avg_h:.1f}  table_row_keys={sorted(table_rows)}")

    # 按 row_key 分组，两两相邻判定
    from collections import defaultdict
    by_row = defaultdict(list)
    for e in enriched:
        by_row[e["row_key"]].append(e)

    print(f"{'rk':>4} {'gap':>5} {'avg':>5} {'g/h':>5} {'style':>5} {'tbl':>3}  text_a  →  text_b")
    print("-" * 90)
    for rk in sorted(by_row.keys()):
        items = sorted(by_row[rk], key=lambda x: x["x0"])
        if len(items) == 1:
            continue
        for k in range(1, len(items)):
            prev, cur = items[k - 1], items[k]
            gap = cur["x0"] - prev["x1"]
            avg_height = (prev["h"] + cur["h"]) / 2.0
            ratio = gap / avg_height if avg_height > 0 else 0
            style_ok = _same_style(prev["blk"], cur["blk"])
            is_tbl = rk in table_rows
            gap_abs_cap = 1.6 * avg_height
            accept = (
                style_ok
                and not is_tbl
                and (gap < 0 or (gap <= 0.8 * avg_height and gap <= gap_abs_cap))
            )
            mark = "✓" if accept else "✗"
            reason = []
            if is_tbl:
                reason.append("table-row")
            if not style_ok:
                sa = prev["blk"].get("font_size_pt", 0)
                sb = cur["blk"].get("font_size_pt", 0)
                reason.append(f"style(sa={sa},sb={sb},ba={prev['blk'].get('bold')},bb={cur['blk'].get('bold')})")
            if gap >= 0 and gap > 0.8 * avg_height:
                reason.append(f"gap>{0.8:.1f}h")
            if gap > gap_abs_cap:
                reason.append(f"gap>{gap_abs_cap:.0f}px")
            print(
                f"{rk:>4} {gap:>5.1f} {avg_height:>5.1f} {ratio:>5.2f} "
                f"{'Y' if style_ok else 'N':>5} {'Y' if is_tbl else 'N':>3} "
                f"{mark}  {_short(prev['text']):<20} → {_short(cur['text']):<20}  "
                f"{','.join(reason)}"
            )


def inspect_paragraph(enriched: List[dict]) -> None:
    if not enriched:
        return
    dividers = _detect_section_dividers(enriched, section_gap_ratio=2.0)
    print(f"\n=== [PARA] section_dividers (y)={[round(d,1) for d in dividers]}")
    # 只查相邻 y 的块两两判定（不做聚类循环，简化以便肉眼看）
    items = sorted(enriched, key=lambda e: e["y0"])
    print(f"{'gap':>5} {'avg':>5} {'g/h':>5} {'xalign':>6} {'style':>5} {'div':>3}  text_a  →  text_b")
    print("-" * 100)
    for k in range(1, len(items)):
        prev, cur = items[k - 1], items[k]
        if cur["y0"] < prev["y1"] - 1:
            continue
        gap = cur["y0"] - prev["y1"]
        avg_h = (prev["h"] + cur["h"]) / 2.0
        ratio = gap / avg_h if avg_h > 0 else 0
        x_align = abs(cur["x0"] - prev["x0"])
        style_ok = _same_style(
            prev["blk"], cur["blk"], size_tol_pt=1.0, color_tol=12,
        )
        crosses = any(prev["y1"] < d < cur["y0"] for d in dividers)
        accept = (
            style_ok
            and not crosses
            and gap <= 0.9 * avg_h
            and x_align <= 1.2 * avg_h
        )
        mark = "✓" if accept else "✗"
        reasons = []
        if crosses:
            reasons.append("crosses-divider")
        if not style_ok:
            reasons.append("style-diff")
        if gap > 0.9 * avg_h:
            reasons.append(f"gap>{0.9}h")
        if x_align > 1.2 * avg_h:
            reasons.append(f"x-align>{1.2}h")
        print(
            f"{gap:>5.1f} {avg_h:>5.1f} {ratio:>5.2f} {x_align:>6.1f} "
            f"{'Y' if style_ok else 'N':>5} {'Y' if crosses else 'N':>3} "
            f"{mark}  {_short(prev['text']):<22} → {_short(cur['text']):<22}  "
            f"{','.join(reasons)}"
        )


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 scripts/inspect_merge.py <image-path>")
        return 2
    img_path = Path(sys.argv[1]).resolve()
    if not img_path.exists():
        print(f"图片不存在: {img_path}")
        return 2

    img = Image.open(img_path).convert("RGB")
    arr = np.array(img)
    print(f"[info] image={img_path.name} size={arr.shape[1]}x{arr.shape[0]}")

    ocr_result = run_ocr(img_path)
    styled_blocks = infer_styles(arr, ocr_result)
    print(f"[info] blocks after style = {len(styled_blocks)}")

    enriched = _enrich(styled_blocks)
    inspect_inline(enriched)
    # 第二次 enrich 用于段落扫描（inline 会污染 row_key 字段）
    enriched2 = _enrich(styled_blocks)
    inspect_paragraph(enriched2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
