"""
设计语义分层重建（Design-aware Reconstruction）

核心思想：把图片当成"设计结果"，而不是"像素集合"
- 纯色区域 → 直接填充
- 渐变区域 → 拟合渐变参数重绘
- 复杂纹理 → 用采样得到的近似色填充（不加载任何 inpainting 模型）
"""
from typing import List, Tuple, Union
import numpy as np
from PIL import Image
import cv2


def _analyze_region_type(
    image: np.ndarray,
    poly: List[List[float]],
    color_std_threshold: float = 15.0,
    gradient_r2_threshold: float = 0.85,
) -> Tuple[str, dict]:
    """
    分析文字区域的背景类型。

    返回:
        (type, params)
        type: "solid" | "gradient" | "complex"
        params: 重建所需的参数
    """
    h, w = image.shape[:2]
    pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)

    # 获取边界框（稍微扩展以采样周围背景）
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    pad = 5
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)

    if x1 <= x0 or y1 <= y0:
        return "solid", {"color": (255, 255, 255)}

    # 创建区域 mask（文字区域外的部分用于采样背景）
    region_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(region_mask, [pts], 255)

    # 扩展区域用于采样
    expanded_mask = np.zeros((h, w), dtype=np.uint8)
    expanded_pts = pts.copy()
    cv2.fillPoly(expanded_mask, [expanded_pts], 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pad * 2 + 1, pad * 2 + 1))
    expanded_mask = cv2.dilate(expanded_mask, kernel)

    # 采样区域：扩展区域 - 原区域（即文字周围的背景）
    sample_mask = cv2.subtract(expanded_mask, region_mask)
    roi_sample = sample_mask[y0:y1, x0:x1]
    roi_image = image[y0:y1, x0:x1]

    # 提取采样点
    sample_points = roi_image[roi_sample > 0]

    if len(sample_points) < 10:
        # 采样点太少，直接用区域内的中位数
        roi_full = image[y0:y1, x0:x1]
        median_color = np.median(roi_full.reshape(-1, 3), axis=0).astype(np.uint8)
        return "solid", {"color": tuple(median_color.tolist())}

    # 计算颜色统计
    color_std = np.std(sample_points, axis=0).mean()

    if color_std < color_std_threshold:
        # 纯色背景
        median_color = np.median(sample_points, axis=0).astype(np.uint8)
        return "solid", {"color": tuple(median_color.tolist())}

    # 检测是否为渐变
    sample_coords = np.argwhere(roi_sample > 0)
    if len(sample_coords) >= 20:
        from sklearn.linear_model import LinearRegression

        # 尝试水平渐变
        x_coords = sample_coords[:, 1].reshape(-1, 1)
        colors = sample_points.astype(np.float32)
        r2_scores = []
        models = []

        for channel in range(3):
            model = LinearRegression()
            model.fit(x_coords, colors[:, channel])
            pred = model.predict(x_coords)
            ss_res = np.sum((colors[:, channel] - pred) ** 2)
            ss_tot = np.sum((colors[:, channel] - np.mean(colors[:, channel])) ** 2)
            r2 = 1 - (ss_res / (ss_tot + 1e-6))
            r2_scores.append(r2)
            models.append(model)

        avg_r2 = np.mean(r2_scores)

        if avg_r2 > gradient_r2_threshold:
            start_color = [int(m.predict([[0]])[0]) for m in models]
            end_color = [int(m.predict([[x1 - x0]])[0]) for m in models]
            start_color = [max(0, min(255, c)) for c in start_color]
            end_color = [max(0, min(255, c)) for c in end_color]
            return "gradient", {
                "direction": "horizontal",
                "start_color": tuple(start_color),
                "end_color": tuple(end_color),
            }

        y_coords = sample_coords[:, 0].reshape(-1, 1)
        r2_scores_v = []
        models_v = []

        for channel in range(3):
            model = LinearRegression()
            model.fit(y_coords, colors[:, channel])
            pred = model.predict(y_coords)
            ss_res = np.sum((colors[:, channel] - pred) ** 2)
            ss_tot = np.sum((colors[:, channel] - np.mean(colors[:, channel])) ** 2)
            r2 = 1 - (ss_res / (ss_tot + 1e-6))
            r2_scores_v.append(r2)
            models_v.append(model)

        avg_r2_v = np.mean(r2_scores_v)

        if avg_r2_v > gradient_r2_threshold:
            start_color = [int(m.predict([[0]])[0]) for m in models_v]
            end_color = [int(m.predict([[y1 - y0]])[0]) for m in models_v]
            start_color = [max(0, min(255, c)) for c in start_color]
            end_color = [max(0, min(255, c)) for c in end_color]
            return "gradient", {
                "direction": "vertical",
                "start_color": tuple(start_color),
                "end_color": tuple(end_color),
            }

    median_color = np.median(sample_points, axis=0).astype(np.uint8)
    return "complex", {"fallback_color": tuple(median_color.tolist())}


def _fill_solid(
    image: np.ndarray,
    poly: List[List[float]],
    color: Tuple[int, int, int],
    dilate: int = 2,
) -> None:
    """用纯色填充文字区域"""
    pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)

    h, w = image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        mask = cv2.dilate(mask, kernel)

    image[mask > 0] = color


def _fill_gradient(
    image: np.ndarray,
    poly: List[List[float]],
    direction: str,
    start_color: Tuple[int, int, int],
    end_color: Tuple[int, int, int],
    dilate: int = 2,
) -> None:
    """用渐变填充文字区域"""
    h, w = image.shape[:2]
    pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)

    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        mask = cv2.dilate(mask, kernel)

    if direction == "horizontal":
        gradient = np.zeros((y1 - y0, x1 - x0, 3), dtype=np.float32)
        for i in range(3):
            gradient[:, :, i] = np.linspace(start_color[i], end_color[i], x1 - x0)
    else:
        gradient = np.zeros((y1 - y0, x1 - x0, 3), dtype=np.float32)
        for i in range(3):
            gradient[:, :, i] = np.linspace(start_color[i], end_color[i], y1 - y0).reshape(-1, 1)

    gradient = np.clip(gradient, 0, 255).astype(np.uint8)

    roi_mask = mask[y0:y1, x0:x1]
    roi_image = image[y0:y1, x0:x1]
    roi_image[roi_mask > 0] = gradient[roi_mask > 0]


def reconstruct_background(
    image: Union[Image.Image, np.ndarray],
    styled_blocks: List[dict],
    dilate: int = 3,
) -> Image.Image:
    """
    设计语义分层重建：根据背景类型选择重建方法。

    - 纯色 → 直接填充
    - 渐变 → 拟合渐变重绘
    - 复杂纹理 → 近似色填充（不调用任何深度学习 inpainting）
    """
    if isinstance(image, Image.Image):
        arr = np.array(image.convert("RGB")).copy()
    else:
        arr = image.copy()

    h, w = arr.shape[:2]

    polys = []
    for b in styled_blocks:
        if b.get("precise_poly"):
            polys.append(b["precise_poly"])
        elif b.get("box"):
            polys.append(b["box"])

    if not polys:
        return Image.fromarray(arr)

    complex_polys = []

    for poly in polys:
        region_type, params = _analyze_region_type(arr, poly)

        if region_type == "solid":
            _fill_solid(arr, poly, params["color"], dilate=dilate)
        elif region_type == "gradient":
            _fill_gradient(
                arr,
                poly,
                params["direction"],
                params["start_color"],
                params["end_color"],
                dilate=dilate,
            )
        else:
            complex_polys.append((poly, params.get("fallback_color", (128, 128, 128))))

    for poly, fallback_color in complex_polys:
        _fill_solid(arr, poly, fallback_color, dilate=dilate)

    return Image.fromarray(arr)
