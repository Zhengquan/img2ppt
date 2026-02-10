"""从检测框生成笔画级 Mask，用 iopaint/LaMa 做 inpainting 去字。"""
from typing import List, Union

import numpy as np
from PIL import Image


def _extract_stroke_mask_in_region(
    gray: np.ndarray,
    poly: List[List[float]],
    full_mask: np.ndarray,
) -> None:
    """
    在指定多边形区域内提取文字笔画的精确 mask。
    使用自适应阈值 + 形态学操作获取笔画级 mask。
    """
    import cv2
    
    h, w = gray.shape[:2]
    pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)
    
    # 获取边界框
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    
    if x1 <= x0 or y1 <= y0:
        return
    
    # 提取 ROI
    roi = gray[y0:y1, x0:x1].copy()
    
    # 创建区域 mask
    region_mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(region_mask, [pts], 255)
    roi_region = region_mask[y0:y1, x0:x1]
    
    # 自适应阈值二值化 - 提取文字笔画
    # 使用 OTSU 自动确定阈值
    _, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # 如果 OTSU 效果不好（文字太少），尝试自适应阈值
    if np.sum(binary > 0) < 0.05 * binary.size or np.sum(binary > 0) > 0.8 * binary.size:
        binary = cv2.adaptiveThreshold(
            roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY_INV, 15, 5
        )
    
    # 只保留多边形区域内的笔画
    binary = cv2.bitwise_and(binary, roi_region)
    
    # 轻微膨胀以确保覆盖笔画边缘
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.dilate(binary, kernel, iterations=1)
    
    # 合并到完整 mask
    full_mask[y0:y1, x0:x1] = cv2.bitwise_or(full_mask[y0:y1, x0:x1], binary)


def _extract_stroke_mask(
    image: np.ndarray,
    polys: List[List[List[float]]],
    dilate: int = 2,
) -> np.ndarray:
    """
    生成笔画级别的精确 mask。
    使用颜色对比度在每个文字区域内提取实际的文字笔画像素。
    """
    import cv2
    
    h, w = image.shape[:2]
    
    # 转灰度
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    else:
        gray = image.copy()
    
    # 创建完整 mask
    full_mask = np.zeros((h, w), dtype=np.uint8)
    
    # 对每个文字区域提取笔画 mask
    for poly in polys:
        _extract_stroke_mask_in_region(gray, poly, full_mask)
    
    # 最终膨胀以确保完全覆盖
    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        full_mask = cv2.dilate(full_mask, kernel)
    
    return full_mask


def remove_text_regions(
    image: Union[Image.Image, np.ndarray],
    styled_blocks: List[dict],
    dilate: int = 3,
) -> Image.Image:
    """
    对原图做去字修补，返回去字后的 PIL Image。
    使用笔画级 mask 而非文字框，大幅提升修复效果。
    """
    import cv2
    
    if isinstance(image, np.ndarray):
        img = Image.fromarray(image).convert("RGB")
    else:
        img = image.convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]
    
    # 收集所有文字区域
    polys = []
    for b in styled_blocks:
        if b.get("precise_poly"):
            polys.append(b["precise_poly"])
        elif b.get("box"):
            polys.append(b["box"])
    
    if not polys:
        return img
    
    # 生成笔画级 mask
    mask = _extract_stroke_mask(arr, polys, dilate=dilate)
    
    # 尝试使用 iopaint
    try:
        from iopaint.model_manager import ModelManager
        from iopaint.schema import InpaintRequest, HDStrategy
        
        model_manager = ModelManager(name='lama', device='cpu')
        
        config = InpaintRequest(
            hd_strategy=HDStrategy.RESIZE,
            hd_strategy_resize_limit=2048,
        )
        result = model_manager(arr, mask, config)
        
        if isinstance(result, np.ndarray):
            return Image.fromarray(result).convert("RGB")
        return result.convert("RGB")
        
    except Exception as e:
        print(f"iopaint 失败，回退到 simple-lama: {e}")
        try:
            from simple_lama_inpainting import SimpleLama
            mask_pil = Image.fromarray(mask)
            simple_lama = SimpleLama()
            out = simple_lama(img, mask_pil)
            if isinstance(out, np.ndarray):
                return Image.fromarray(out).convert("RGB")
            return out.convert("RGB")
        except ImportError:
            raise ImportError("去字需要 iopaint 或 simple-lama-inpainting")


# 兼容旧接口
def _polys_to_mask(polys, height, width, dilate=5):
    """旧接口，保留兼容性"""
    import cv2
    mask = np.zeros((height, width), dtype=np.uint8)
    for poly in polys:
        pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in poly], dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
    if dilate > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        mask = cv2.dilate(mask, kernel)
    return mask
