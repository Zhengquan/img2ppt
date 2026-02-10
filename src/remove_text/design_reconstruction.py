"""
设计语义分层重建（Design-aware Reconstruction）

核心思想：把图片当成"设计结果"，而不是"像素集合"
- 纯色区域 → 直接填充
- 渐变区域 → 拟合渐变参数重绘
- 复杂纹理 → 才用 inpainting（PPT/UI 中极少）
"""
import os
from typing import List, Tuple, Union, Optional
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
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pad*2+1, pad*2+1))
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
    # 用线性回归拟合颜色变化
    sample_coords = np.argwhere(roi_sample > 0)
    if len(sample_coords) >= 20:
        # 尝试水平渐变
        x_coords = sample_coords[:, 1].reshape(-1, 1)
        colors = sample_points.astype(np.float32)
        
        from sklearn.linear_model import LinearRegression
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
            # 水平渐变
            start_color = [int(m.predict([[0]])[0]) for m in models]
            end_color = [int(m.predict([[x1-x0]])[0]) for m in models]
            start_color = [max(0, min(255, c)) for c in start_color]
            end_color = [max(0, min(255, c)) for c in end_color]
            return "gradient", {
                "direction": "horizontal",
                "start_color": tuple(start_color),
                "end_color": tuple(end_color),
            }
        
        # 尝试垂直渐变
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
            # 垂直渐变
            start_color = [int(m.predict([[0]])[0]) for m in models_v]
            end_color = [int(m.predict([[y1-y0]])[0]) for m in models_v]
            start_color = [max(0, min(255, c)) for c in start_color]
            end_color = [max(0, min(255, c)) for c in end_color]
            return "gradient", {
                "direction": "vertical",
                "start_color": tuple(start_color),
                "end_color": tuple(end_color),
            }
    
    # 复杂纹理（实际在 PPT/UI 中很少见）
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
    
    # 创建 mask
    h, w = image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    
    # 膨胀确保覆盖文字边缘
    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate*2+1, dilate*2+1))
        mask = cv2.dilate(mask, kernel)
    
    # 填充
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
    
    # 获取边界框
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    
    # 创建 mask
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    
    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate*2+1, dilate*2+1))
        mask = cv2.dilate(mask, kernel)
    
    # 生成渐变
    if direction == "horizontal":
        gradient = np.zeros((y1-y0, x1-x0, 3), dtype=np.float32)
        for i in range(3):
            gradient[:, :, i] = np.linspace(start_color[i], end_color[i], x1-x0)
    else:  # vertical
        gradient = np.zeros((y1-y0, x1-x0, 3), dtype=np.float32)
        for i in range(3):
            gradient[:, :, i] = np.linspace(start_color[i], end_color[i], y1-y0).reshape(-1, 1)
    
    gradient = np.clip(gradient, 0, 255).astype(np.uint8)
    
    # 应用渐变到 mask 区域
    roi_mask = mask[y0:y1, x0:x1]
    roi_image = image[y0:y1, x0:x1]
    roi_image[roi_mask > 0] = gradient[roi_mask > 0]


def reconstruct_background(
    image: Union[Image.Image, np.ndarray],
    styled_blocks: List[dict],
    dilate: int = 3,
    use_inpainting_fallback: bool = None,
) -> Image.Image:
    """
    设计语义分层重建：根据背景类型选择最优重建方法。

    - 纯色区域 → 直接填充（最干净）
    - 渐变区域 → 拟合渐变重绘（完美还原）
    - 复杂纹理 → 若 use_inpainting_fallback=True 则用 LaMa 修补，否则用近似色填充（默认不启用 LaMa，避免加载 big-lama）

    use_inpainting_fallback: 是否对复杂纹理使用 LaMa inpainting。默认从环境变量 USE_INPAINTING_FALLBACK 读取（1/true 启用），未设置时为 False（不加载 big-lama）。
    """
    if use_inpainting_fallback is None:
        use_inpainting_fallback = os.environ.get("USE_INPAINTING_FALLBACK", "").strip().lower() in ("1", "true", "yes")
    if isinstance(image, Image.Image):
        arr = np.array(image.convert("RGB")).copy()
    else:
        arr = image.copy()
    
    h, w = arr.shape[:2]
    
    # 收集所有文字区域
    polys = []
    for b in styled_blocks:
        if b.get("precise_poly"):
            polys.append(b["precise_poly"])
        elif b.get("box"):
            polys.append(b["box"])
    
    if not polys:
        return Image.fromarray(arr)
    
    # 需要 inpainting 的区域
    complex_polys = []
    
    # 逐区域分析并重建
    for poly in polys:
        region_type, params = _analyze_region_type(arr, poly)
        
        if region_type == "solid":
            _fill_solid(arr, poly, params["color"], dilate=dilate)
        elif region_type == "gradient":
            _fill_gradient(
                arr, poly, 
                params["direction"],
                params["start_color"],
                params["end_color"],
                dilate=dilate
            )
        else:
            # 复杂纹理，标记等待 inpainting
            complex_polys.append((poly, params.get("fallback_color", (128, 128, 128))))
    
    # 对复杂区域使用 inpainting（如果有的话）
    if complex_polys and use_inpainting_fallback:
        try:
            from iopaint.model_manager import ModelManager
            from iopaint.schema import InpaintRequest, HDStrategy
            
            # 创建复杂区域的 mask
            complex_mask = np.zeros((h, w), dtype=np.uint8)
            for poly, _ in complex_polys:
                pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)
                cv2.fillPoly(complex_mask, [pts], 255)
            
            if dilate > 0:
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate*2+1, dilate*2+1))
                complex_mask = cv2.dilate(complex_mask, kernel)
            
            if np.sum(complex_mask > 0) > 0:
                model_manager = ModelManager(name='lama', device='cpu')
                config = InpaintRequest(
                    hd_strategy=HDStrategy.RESIZE,
                    hd_strategy_resize_limit=2048,
                )
                arr = model_manager(arr, complex_mask, config)
                if isinstance(arr, np.ndarray):
                    arr = arr.astype(np.uint8)
        except Exception as e:
            # inpainting 失败，用 fallback 颜色填充
            print(f"Inpainting fallback 失败: {e}")
            for poly, fallback_color in complex_polys:
                _fill_solid(arr, poly, fallback_color, dilate=dilate)
    elif complex_polys:
        # 不使用 inpainting，直接用 fallback 颜色
        for poly, fallback_color in complex_polys:
            _fill_solid(arr, poly, fallback_color, dilate=dilate)
    
    return Image.fromarray(arr)
