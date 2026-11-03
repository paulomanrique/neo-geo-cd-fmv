"""Oklab distances for offline palette and tile decisions."""

from __future__ import annotations

import numpy as np


def oklab(rgb: np.ndarray) -> np.ndarray:
    srgb = np.asarray(rgb, dtype=np.float32) / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92,
                      ((srgb + 0.055) / 1.055) ** 2.4)
    lms = linear @ np.array([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ], dtype=np.float32).T
    return np.cbrt(lms) @ np.array([
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ], dtype=np.float32).T


def squared_error(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.square(oklab(left) - oklab(right)).sum(axis=-1)


def rgb_from_oklab(lab: np.ndarray) -> np.ndarray:
    lms = np.asarray(lab) @ np.array([
        [1, .3963377774, .2158037573],
        [1, -.1055613458, -.0638541728],
        [1, -.0894841775, -1.2914855480],
    ]).T
    linear = (lms ** 3) @ np.array([
        [4.0767416621, -3.3077115913, .2309699292],
        [-1.2684380046, 2.6097574011, -.3413193965],
        [-.0041960863, -.7034186147, 1.7076147010],
    ]).T
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= .0031308, linear * 12.92,
                    1.055 * linear ** (1 / 2.4) - .055) * 255
