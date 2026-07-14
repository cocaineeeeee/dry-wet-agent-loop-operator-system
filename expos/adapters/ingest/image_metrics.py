"""晶体图像度量（PIL + scipy.ndimage，无 cv2 依赖）。

灰度 → 阈值（Otsu，自实现直方图版）→ 连通域标记 → {晶粒数, 覆盖率, 平均晶粒尺寸}
→ 质量指数。用于 bench_manual 回灌时把显微/相机图折算成标量测量。

确定性：同一输入两次调用逐位相同（纯 numpy/scipy，无随机）。
响亮失败（AdapterError）：文件不存在 / 不可解析 / 全同值图像（无法阈值化）。
红线：本模块只处理像素，禁读真值 sidecar。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage

from expos.adapters.base import AdapterError


def _load_gray(image: "str | Path | np.ndarray") -> np.ndarray:
    """载入图像并转为灰度 float ∈ [0, 1]。ndarray 直接归一化，路径用 PIL 解码。"""
    if isinstance(image, np.ndarray):
        arr = np.asarray(image, dtype=np.float64)
        if arr.ndim == 3:  # 多通道 → 亮度加权
            arr = arr[..., :3] @ np.array([0.2989, 0.5870, 0.1140])
        elif arr.ndim != 2:
            raise AdapterError(f"数组维度 {arr.ndim} 不支持（需 2D 灰度或 3D 通道）")
    else:
        path = Path(image)
        if not path.exists():
            raise AdapterError(f"图像文件不存在: {path}")
        try:
            from PIL import Image

            with Image.open(path) as im:
                arr = np.asarray(im.convert("L"), dtype=np.float64)
        except Exception as exc:  # 解码失败一律响亮失败
            raise AdapterError(f"图像不可解析: {path} ({exc})") from exc

    if arr.size == 0:
        raise AdapterError("空图像")
    lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        raise AdapterError("全同值图像（无灰度动态范围），无法阈值化")
    return (arr - lo) / (hi - lo)


def _otsu_threshold(gray: np.ndarray, bins: int = 256) -> float:
    """直方图版 Otsu：最大化类间方差的阈值（返回 [0,1] 上的分界灰度）。"""
    hist, edges = np.histogram(gray.ravel(), bins=bins, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2.0

    w0 = np.cumsum(hist)                      # 前景（≤t）累计权重
    w1 = total - w0                           # 背景累计权重
    sum_all = (hist * centers).cumsum()
    mean_total = (hist * centers).sum()

    best_var, best_t = -1.0, 0.5
    for i in range(bins):
        if w0[i] == 0 or w1[i] == 0:
            continue
        mu0 = sum_all[i] / w0[i]
        mu1 = (mean_total - sum_all[i]) / w1[i]
        var_between = w0[i] * w1[i] * (mu0 - mu1) ** 2
        if var_between > best_var:
            best_var, best_t = var_between, float(centers[i])
    return best_t


def crystal_metrics(
    image: "str | Path | np.ndarray",
    min_grain_px: int = 5,
    threshold: float | None = None,
) -> dict[str, float]:
    """晶粒统计：前景（亮）像素为晶体，连通域为晶粒。

    - threshold=None → 用 Otsu 自动阈值；否则用给定灰度阈值（[0,1]）。
    - min_grain_px：丢弃像素数 < 此值的连通域（去噪点）。
    返回 {"grain_count", "coverage", "mean_grain_size"}。
    """
    gray = _load_gray(image)
    thr = _otsu_threshold(gray) if threshold is None else float(threshold)

    foreground = gray > thr
    labels, n = ndimage.label(foreground)  # 4-连通默认结构

    grain_count = 0
    kept_fg_px = 0
    if n > 0:
        sizes = ndimage.sum_labels(np.ones_like(labels), labels, index=range(1, n + 1))
        sizes = np.asarray(sizes, dtype=np.int64)
        keep = sizes >= min_grain_px
        grain_count = int(keep.sum())
        kept_fg_px = int(sizes[keep].sum())

    total_px = gray.size
    coverage = kept_fg_px / total_px
    mean_grain_size = (kept_fg_px / grain_count) if grain_count > 0 else 0.0

    return {
        "grain_count": float(grain_count),
        "coverage": float(coverage),
        "mean_grain_size": float(mean_grain_size),
    }


def quality_index(metrics: dict[str, float]) -> float:
    """把晶粒度量折算成 [0,1] 质量指数（工程构造，非文献结论）。

    直觉：覆盖率高（成膜充分）且晶粒偏大（结晶良好、缺陷少）→ 质量高；
    这里用覆盖率与归一化平均晶粒尺寸的几何均值。**该权重纯属工程约定，
    不代表任何文献量化关系**，仅用于闭环内部的相对排序，需按域标定。
    """
    coverage = float(metrics.get("coverage", 0.0))
    mean_size = float(metrics.get("mean_grain_size", 0.0))
    # 平均晶粒尺寸用软饱和压到 [0,1]（100 px 为特征尺度的工程常数）
    size_norm = mean_size / (mean_size + 100.0)
    coverage = min(max(coverage, 0.0), 1.0)
    idx = float(np.sqrt(coverage * size_norm))
    return min(max(idx, 0.0), 1.0)
