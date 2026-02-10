"""样式推断：加粗、颜色、字号。"""
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


def _is_bold_heuristic(
    box_4pts: List[List[float]],
    text: str,
    index: int,
    all_boxes: List[List[List[float]]],
    img_h: float,
) -> bool:
    """启发式加粗：首行、或框高相对较大则视为标题/强调。"""
    if not text or not text.strip():
        return False
    h = _box_height_px(box_4pts)
    # 框高大于平均的 1.2 倍视为强调
    if all_boxes:
        avg_h = sum(_box_height_px(b) for b in all_boxes) / len(all_boxes)
        if h >= avg_h * 1.2:
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
    每项: {"box": [[x,y],...], "text": str, "bold": bool, "color": (r,g,b), "font_size_pt": float, "precise_poly": [...]}
    """
    if isinstance(img, Image.Image):
        arr = np.array(img.convert("RGB"))
    else:
        arr = np.asarray(img)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
    img_h, img_w = arr.shape[:2]
    all_boxes = [item[0] for item in ocr_result]

    out: List[dict] = []
    for i, item in enumerate(ocr_result):
        # 支持新格式 (box, text, score, precise_poly) 和旧格式 (box, text, score)
        if len(item) >= 4:
            box, text, _score, precise_poly = item[0], item[1], item[2], item[3]
        else:
            box, text, _score = item[0], item[1], item[2]
            precise_poly = box  # 旧格式没有精确轮廓
        
        color = _sample_color(arr, box)
        bold = _is_bold_heuristic(box, text, i, all_boxes, float(img_h))
        h_px = _box_height_px(box)
        font_size_pt = _height_to_pt(h_px, float(img_h), slide_height_pt, font_pt_min, font_pt_max)
        out.append({
            "box": box,
            "text": text,
            "bold": bold,
            "color": color,
            "font_size_pt": font_size_pt,
            "precise_poly": precise_poly,
        })
    return out
