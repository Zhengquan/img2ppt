"""样式推断：加粗、颜色、字号。

字号估计说明
-----------
OCR 给出的 bbox 是外接矩形，会在上下各留若干像素的空白；如果直接拿 bbox 高度
折算磅数，系统性偏大 ~30%（经 `scripts/verify_glyph_fontsize.py` 多张样本
实测，glyph/bbox 中位数 ≈ 0.68）。这里在 bbox 内用文字颜色做前景分割，取连通
分量高度中位数作为字形真实像素高度 `glyph_h`，再按「图高 ↔ 幻灯片逻辑高」
折算磅数，加粗判断也基于 glyph_h（度量空间自洽）。

依赖：scipy 可选；缺 scipy 时降级为行投影；再失败则回退到 bbox 高度。
"""
from typing import List, Tuple, Union

import numpy as np
from PIL import Image


def _sample_color(img: np.ndarray, box_4pts: List[List[float]]) -> Tuple[int, int, int]:
    """
    提取文本框内文字的颜色（而非背景）。
    使用 K-Means 聚类区分背景和文字，选择占比较少的颜色作为文字色。
    """
    from sklearn.cluster import KMeans
    
    h, w = img.shape[:2]
    xs = [int(round(p[0])) for p in box_4pts]
    ys = [int(round(p[1])) for p in box_4pts]
    x0 = max(0, min(xs))
    x1 = min(w, max(xs) + 1)
    y0 = max(0, min(ys))
    y1 = min(h, max(ys) + 1)
    if x0 >= x1 or y0 >= y1:
        return (0, 0, 0)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return (0, 0, 0)
    if roi.ndim != 3:
        v = int(np.median(roi))
        return (v, v, v)
    
    # 将 ROI 展平为像素列表
    pixels = roi.reshape(-1, 3).astype(np.float32)
    if len(pixels) < 10:
        return (int(np.median(roi[:, :, 0])), int(np.median(roi[:, :, 1])), int(np.median(roi[:, :, 2])))
    
    try:
        # K-Means 聚类为 2 类：背景和文字
        kmeans = KMeans(n_clusters=2, random_state=42, n_init=10)
        labels = kmeans.fit_predict(pixels)
        centers = kmeans.cluster_centers_
        
        # 统计每个簇的像素数量
        unique, counts = np.unique(labels, return_counts=True)
        cluster_counts = dict(zip(unique, counts))
        
        # 选择像素数较少的簇作为文字颜色（文字通常比背景占比少）
        if cluster_counts.get(0, 0) < cluster_counts.get(1, 0):
            text_color = centers[0]
        else:
            text_color = centers[1]
        
        return (int(text_color[0]), int(text_color[1]), int(text_color[2]))
    except Exception:
        # 回退到中位数
        return (int(np.median(roi[:, :, 0])), int(np.median(roi[:, :, 1])), int(np.median(roi[:, :, 2])))


def _box_height_px(box_4pts: List[List[float]]) -> float:
    ys = [p[1] for p in box_4pts]
    return float(max(ys) - min(ys))


def _glyph_height_px(
    img: np.ndarray,
    box_4pts: List[List[float]],
    text_color: Tuple[int, int, int],
    fallback_h: float,
    min_component_ratio: float = 0.25,
) -> float:
    """在 bbox ROI 内用文字颜色做前景分割，返回字形像素高度中位数。

    - 距离阈值：低于「中位距离 × 0.6」的像素视为与文字色相近 → 前景。
    - 优先用 scipy 的连通分量分析（更鲁棒，能过滤噪点/标点），
      取面积 >= 总前景 * 0.5% 的分量高度，再过滤掉极矮的分量（< 最高的 25%），
      返回剩余分量高度的中位数。
    - 缺 scipy 时降级为行投影：取前景占据的行跨度。
    - 任何异常或样本不足都回退到 `fallback_h`（通常就是 bbox 高度），保证永不抛。
    """
    try:
        h_img, w_img = img.shape[:2]
        xs = [int(round(p[0])) for p in box_4pts]
        ys = [int(round(p[1])) for p in box_4pts]
        x0 = max(0, min(xs)); x1 = min(w_img, max(xs) + 1)
        y0 = max(0, min(ys)); y1 = min(h_img, max(ys) + 1)
        if x0 >= x1 or y0 >= y1:
            return fallback_h
        roi = img[y0:y1, x0:x1]
        if roi.size == 0 or roi.ndim != 3:
            return fallback_h

        diff = np.linalg.norm(roi.astype(np.float32) - np.array(text_color, dtype=np.float32), axis=2)
        thr = float(np.median(diff)) * 0.6
        mask = diff < thr
        fg_count = int(mask.sum())
        if fg_count < 10:
            return fallback_h

        try:
            from scipy.ndimage import label  # type: ignore

            lbl, n = label(mask)
            if n == 0:
                return fallback_h
            min_area = max(4, int(fg_count * 0.005))
            heights: List[float] = []
            for k in range(1, n + 1):
                ys_k = np.where(lbl == k)[0]
                if ys_k.size < min_area:
                    continue
                heights.append(float(ys_k.max() - ys_k.min() + 1))
            if not heights:
                return fallback_h
            heights.sort()
            cutoff = heights[-1] * min_component_ratio
            kept = [h for h in heights if h >= cutoff]
            return float(np.median(kept)) if kept else fallback_h
        except ImportError:
            # 降级：行投影
            row_any = mask.any(axis=1)
            idx = np.where(row_any)[0]
            if idx.size == 0:
                return fallback_h
            return float(idx.max() - idx.min() + 1)
    except Exception:
        return fallback_h


def _is_bold_heuristic(
    box_4pts: List[List[float]],
    text: str,
    index: int,
    heights: List[float],
    img_h: float,
) -> bool:
    """启发式加粗：首行/顶部，或字形高显著大于平均值则视为标题/强调。

    `heights` 为所有块的字形像素高（与 `_glyph_height_px` 同源），
    用同一度量空间对比阈值，避免 bbox 边距差异带来的噪声。
    """
    if not text or not text.strip():
        return False
    if heights and 0 <= index < len(heights):
        h = heights[index]
        avg_h = sum(heights) / len(heights)
        if avg_h > 0 and h >= avg_h * 1.2:
            return True
    # 顶部 15% 区域内视为标题
    y_center = sum(p[1] for p in box_4pts) / 4
    if y_center <= img_h * 0.15:
        return True
    return False


def _height_to_pt(
    height_px: float,
    img_h_px: float,
    slide_height_pt: float = 720.0,
    min_pt: float = 8.0,
    max_pt: float = 72.0,
) -> float:
    """由框高（像素）按「图高 ↔ 幻灯片逻辑高」换算为磅数。"""
    if img_h_px <= 0:
        return min_pt
    pt = height_px * (slide_height_pt / img_h_px)
    return float(np.clip(pt, min_pt, max_pt))


def infer_styles(
    img: Union[Image.Image, np.ndarray],
    ocr_result: List[Tuple],
    slide_height_pt: float = 720.0,
    font_pt_min: float = 8.0,
    font_pt_max: float = 72.0,
) -> List[dict]:
    """
    对 OCR 结果做样式推断，返回带样式的文本块列表。
    每项: {"box": [[x,y],...], "text": str, "bold": bool, "color": (r,g,b), "font_size_pt": float, "precise_poly": [...], "ocr_score": float}
    ocr_score 为 OCR 引擎给出的置信度（0~1），无置信度信息时默认为 1.0；供 QA 报告识别低置信文本。
    """
    if isinstance(img, Image.Image):
        arr = np.array(img.convert("RGB"))
    else:
        arr = np.asarray(img)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
    img_h, img_w = arr.shape[:2]

    # 第一遍：为每个块测颜色 + 字形高（为加粗阈值和字号提供同源度量）
    n = len(ocr_result)
    boxes: List[List[List[float]]] = [item[0] for item in ocr_result]
    texts: List[str] = [item[1] for item in ocr_result]
    precise_polys: List = []
    colors: List[Tuple[int, int, int]] = []
    heights_glyph: List[float] = []
    for item in ocr_result:
        box = item[0]
        if len(item) >= 4:
            precise_polys.append(item[3])
        else:
            precise_polys.append(box)
        color = _sample_color(arr, box)
        colors.append(color)
        h_bbox = _box_height_px(box)
        h_glyph = _glyph_height_px(arr, box, color, fallback_h=h_bbox)
        heights_glyph.append(h_glyph)

    # 第二遍：加粗 + 字号
    out: List[dict] = []
    for i in range(n):
        bold = _is_bold_heuristic(boxes[i], texts[i], i, heights_glyph, float(img_h))
        font_size_pt = _height_to_pt(
            heights_glyph[i], float(img_h), slide_height_pt, font_pt_min, font_pt_max
        )
        score = 1.0
        if len(ocr_result[i]) >= 3:
            try:
                score = float(ocr_result[i][2])
            except (TypeError, ValueError):
                score = 1.0
        out.append({
            "box": boxes[i],
            "text": texts[i],
            "bold": bold,
            "color": colors[i],
            "font_size_pt": font_size_pt,
            "precise_poly": precise_polys[i],
            "ocr_score": score,
        })
    return out
