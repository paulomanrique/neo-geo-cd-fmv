"""Deterministic weighted palette fitting on the Neo Geo's 5-bit color grid."""

from functools import lru_cache

import numpy as np

from perceptual import oklab, rgb_from_oklab, squared_error


@lru_cache(maxsize=1)
def color_grid():
    keys = np.arange(32768)
    channels = np.stack((keys >> 10, (keys >> 5) & 31, keys & 31), axis=-1)
    rgb = np.rint(channels * 255 / 31).astype(np.uint8)
    rgb[:, 2] = (channels[:, 2] * 255 // 31).astype(np.uint8)
    return rgb, oklab(rgb)


def color_keys(rgb):
    channels = np.rint(np.asarray(rgb, dtype=np.float32) * 31 / 255).astype(np.uint16)
    return (channels[..., 0] << 10) | (channels[..., 1] << 5) | channels[..., 2]


def nearest(points, centers):
    distance = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=-1)
    return distance.argmin(axis=1), distance.min(axis=1)


def fit(rgb, seed, iterations=8):
    colors, grid = color_grid()
    keys, counts = np.unique(color_keys(rgb), return_counts=True)
    points = grid[keys]
    seed_keys = color_keys(np.asarray(seed, dtype=np.uint8))
    centers = grid[seed_keys].copy()
    best_keys = seed_keys.copy()
    best_error = float("inf")
    for _ in range(iterations + 1):
        labels, errors = nearest(points, centers)
        error = float(errors @ counts)
        if error >= best_error - 1e-12:
            break
        best_error = error
        best_keys = seed_keys.copy()
        updated = centers.copy()
        for index in range(15):
            members = labels == index
            if members.any():
                updated[index] = np.average(points[members], weights=counts[members], axis=0)
        # Project centroids to representable colors, not arbitrary RGB means.
        center_rgb = rgb_from_oklab(updated)
        channel = np.rint(center_rgb * 31 / 255).astype(int)
        offsets = np.array([(r, g, b) for r in (-1, 0, 1)
                            for g in (-1, 0, 1) for b in (-1, 0, 1)])
        neighbors = np.clip(channel[:, None, :] + offsets, 0, 31)
        choices = (neighbors[..., 0] << 10) | (neighbors[..., 1] << 5) | neighbors[..., 2]
        distance = ((updated[:, None, :] - grid[choices]) ** 2).sum(axis=-1)
        seed_keys = choices[np.arange(15), distance.argmin(axis=1)]
        centers = grid[seed_keys]
    palette = colors[best_keys]
    original = colors[color_keys(np.asarray(seed))]
    # Fitting on the bounded 5-bit histogram must also improve actual source RGB.
    proposed = palette[assign(rgb, palette)]
    baseline = original[assign(rgb, original)]
    if squared_error(rgb, proposed).mean() > squared_error(rgb, baseline).mean():
        palette = original
    return [tuple(int(c) for c in row) for row in palette]


def assign(rgb, palette):
    keys, inverse = np.unique(color_keys(rgb), return_inverse=True)
    _, grid = color_grid()
    # Palette colors already lie on the hardware grid.
    centers = oklab(np.asarray(palette, dtype=np.uint8))
    labels, _ = nearest(grid[keys], centers)
    return labels[inverse].reshape(rgb.shape[:2]).astype(np.uint8)
