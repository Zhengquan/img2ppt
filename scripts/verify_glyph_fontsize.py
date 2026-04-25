"""
字号校准可行性验证脚本（只读，不落盘改动主流程）。

对单张图片跑一次 OCR，然后对每个文本块同时用两种方法估字号：
  - old: 直接用 OCR bbox 的像素高度换算（= 当前 src/extract/style.py 的行为）
  - new: 在 bbox 内按文字颜色做前景分割，取连通分量高度的中位数作为字形真实像素高度

输出一张对比表，人眼扫一下「标题/正文/小字」三档的新旧字号差异是否更接近视觉真值。

用法：
  python3 scripts/verify_glyph_fontsize.py tmp/pics/slide-004.png

仅依赖仓库已有的 numpy / scipy / sklearn / PIL / requests。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.extract.ocr import run_ocr  # noqa: E402
from src.extract.style import (  # noqa: E402
    _box_height_px,
    _height_to_pt,
    _sample_color,
)


def glyph_height_px(
    img: np.ndarray,
    box_4pts: List[List[float]],
    text_color: Tuple[int, int, int],
    fallback_h: float,
    min_component_ratio: float = 0.25,
) -> Tuple[float, str]:
    """在 bbox ROI 内用文字颜色做前景分割，返回字形像素高度中位数 + 采用的路径说明。"""
    h_img, w_img = img.shape[:2]
    xs = [int(round(p[0])) for p in box_4pts]
    ys = [int(round(p[1])) for p in box_4pts]
    x0 = max(0, min(xs)); x1 = min(w_img, max(xs) + 1)
    y0 = max(0, min(ys)); y1 = min(h_img, max(ys) + 1)
    if x0 >= x1 or y0 >= y1:
        return fallback_h, "fallback:empty-roi"
    roi = img[y0:y1, x0:x1]
    if roi.size == 0 or roi.ndim != 3:
        return fallback_h, "fallback:bad-roi"

    diff = np.linalg.norm(roi.astype(np.float32) - np.array(text_color, dtype=np.float32), axis=2)
    # 距离阈值：低于「中位距离 × 0.6」的像素视为与文字色相近 → 前景
    thr = float(np.median(diff)) * 0.6
    mask = diff < thr
    fg_count = int(mask.sum())
    if fg_count < 10:
        return fallback_h, f"fallback:too-few-fg({fg_count})"

    try:
        from scipy.ndimage import label

        lbl, n = label(mask)
        if n == 0:
            return fallback_h, "fallback:no-cc"
        min_area = max(4, int(fg_count * 0.005))
        heights: List[float] = []
        for k in range(1, n + 1):
            ys_k = np.where(lbl == k)[0]
            if ys_k.size < min_area:
                continue
            heights.append(float(ys_k.max() - ys_k.min() + 1))
        if not heights:
            return fallback_h, "fallback:no-valid-cc"
        heights.sort()
        cutoff = heights[-1] * min_component_ratio
        kept = [h for h in heights if h >= cutoff]
        return (float(np.median(kept)) if kept else fallback_h), f"glyph:cc={len(kept)}/{n}"
    except ImportError:
        row_any = mask.any(axis=1)
        idx = np.where(row_any)[0]
        if idx.size == 0:
            return fallback_h, "fallback:no-row"
        return float(idx.max() - idx.min() + 1), "glyph:row-projection"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 scripts/verify_glyph_fontsize.py <image-path>")
        return 2
    img_path = Path(sys.argv[1]).resolve()
    if not img_path.exists():
        print(f"图片不存在: {img_path}")
        return 2

    img = Image.open(img_path).convert("RGB")
    arr = np.array(img)
    img_h, img_w = arr.shape[:2]
    print(f"[info] image={img_path.name} size={img_w}x{img_h}")

    ocr_result = run_ocr(img_path)
    print(f"[info] ocr blocks = {len(ocr_result)}\n")

    rows = []
    for i, item in enumerate(ocr_result):
        box = item[0]
        text = item[1]
        if not text.strip():
            continue
        color = _sample_color(arr, box)
        h_bbox = _box_height_px(box)
        h_glyph, how = glyph_height_px(arr, box, color, fallback_h=h_bbox)
        pt_old = _height_to_pt(h_bbox, float(img_h))
        pt_new = _height_to_pt(h_glyph, float(img_h))
        ratio = (h_glyph / h_bbox) if h_bbox > 0 else 1.0
        rows.append({
            "i": i,
            "text": text[:28],
            "h_bbox": round(h_bbox, 1),
            "h_glyph": round(h_glyph, 1),
            "ratio": round(ratio, 3),
            "pt_old": round(pt_old, 1),
            "pt_new": round(pt_new, 1),
            "delta": round(pt_new - pt_old, 1),
            "how": how,
        })

    # 打印一张紧凑表
    hdr = f"{'i':>3} {'h_bbox':>7} {'h_glyph':>8} {'ratio':>6} {'pt_old':>7} {'pt_new':>7} {'Δpt':>6}  text"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['i']:>3} {r['h_bbox']:>7} {r['h_glyph']:>8} {r['ratio']:>6} "
            f"{r['pt_old']:>7} {r['pt_new']:>7} {r['delta']:>6}  {r['text']}"
        )

    # 汇总统计
    ratios = [r["ratio"] for r in rows if r["ratio"] > 0]
    deltas = [r["delta"] for r in rows]
    print("\n[summary]")
    print(f"  blocks          = {len(rows)}")
    if ratios:
        print(f"  ratio (glyph/bbox)  median={np.median(ratios):.3f}  "
              f"mean={np.mean(ratios):.3f}  min={min(ratios):.3f}  max={max(ratios):.3f}")
    if deltas:
        print(f"  Δpt (new - old)     median={np.median(deltas):+.2f}  "
              f"mean={np.mean(deltas):+.2f}  |Δ|≤1pt={sum(1 for d in deltas if abs(d)<=1)}/{len(deltas)}")

    # 顺便落一个 JSON，便于复盘
    out_json = img_path.with_suffix(".fontsize-audit.json")
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[info] 明细写入: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
