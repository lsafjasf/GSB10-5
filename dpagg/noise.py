"""差分隐私噪声机制（仅标准库）。

- Laplace 机制：纯 epsilon-DP，噪声尺度 b = 敏感度 / epsilon
- Gaussian 机制：(epsilon, delta)-DP，
  sigma = 敏感度 * sqrt(2 * ln(1.25 / delta)) / epsilon
  （Dwork & Roth 2014, Theorem A.1，要求 epsilon < 1 的经典标定）
"""

import math
import random


def laplace_scale(sensitivity: float, epsilon: float) -> float:
    """Laplace 噪声尺度 b = 敏感度 / epsilon。"""
    if epsilon <= 0:
        raise ValueError("epsilon 必须为正")
    if sensitivity < 0:
        raise ValueError("敏感度不能为负")
    return sensitivity / epsilon


def gaussian_sigma(sensitivity: float, epsilon: float, delta: float) -> float:
    """Gaussian 噪声标准差 sigma = 敏感度 * sqrt(2 ln(1.25/delta)) / epsilon。"""
    if epsilon <= 0:
        raise ValueError("epsilon 必须为正")
    if not 0 < delta < 1:
        raise ValueError("delta 必须在 (0, 1) 内")
    if sensitivity < 0:
        raise ValueError("敏感度不能为负")
    return sensitivity * math.sqrt(2.0 * math.log(1.25 / delta)) / epsilon


def sample_laplace(scale: float, rng: random.Random) -> float:
    """从 Laplace(0, scale) 采样（逆变换法）。"""
    u = rng.random() - 0.5
    return -scale * math.copysign(math.log1p(-2.0 * abs(u)), u)


def sample_gaussian(sigma: float, rng: random.Random) -> float:
    """从 N(0, sigma^2) 采样（Box-Muller，经 random.gauss）。"""
    return rng.gauss(0.0, sigma)
