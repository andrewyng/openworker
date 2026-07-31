#!/usr/bin/env python3
"""PCG 地形生成 - 噪声地形高度图生成器

支持多种地形风格：山脉、丘陵、平原、峡谷、群岛
输出 16-bit 灰度 PNG 高度图 + .npy 数据文件
"""

import argparse
import json
import os
import struct
import math
import random

try:
    import numpy as np
except ImportError:
    raise SystemExit("需要 numpy: pip install numpy")

try:
    from PIL import Image
except ImportError:
    raise SystemExit("需要 Pillow: pip install Pillow")


def smooth_noise_1d(x, seed=0):
    """简单 1D 伪随机平滑噪声"""
    i = int(x) & 255
    f = x - int(x)
    s = random.Random(seed + i)
    a = s.random()
    s2 = random.Random(seed + i + 1)
    b = s2.random()
    t = f * f * (3 - 2 * f)
    return a * (1 - t) + b * t


def value_noise_2d(x, y, seed=0):
    """2D 值噪声"""
    xi = int(x)
    yi = int(y)
    xf = x - xi
    yf = y - yi
    def rand(ix, iy):
        return random.Random(seed + ix * 374761393 + iy * 668265263).random()
    v00 = rand(xi, yi)
    v10 = rand(xi + 1, yi)
    v01 = rand(xi, yi + 1)
    v11 = rand(xi + 1, yi + 1)
    sx = xf * xf * (3 - 2 * xf)
    sy = yf * yf * (3 - 2 * yf)
    return (v00 * (1 - sx) + v10 * sx) * (1 - sy) + (v01 * (1 - sx) + v11 * sx) * sy


def fbm(x, y, octaves=6, persistence=0.5, lacunarity=2.0, seed=0):
    """分形布朗运动 - 多层噪声叠加"""
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    max_value = 0.0
    for _ in range(octaves):
        total += value_noise_2d(x * frequency, y * frequency, seed) * amplitude
        max_value += amplitude
        amplitude *= persistence
        frequency *= lacunarity
    return total / max_value


def generate_mountains(size, seed):
    """山脉：大振幅低频 + 中频细节"""
    h = np.zeros((size, size), dtype=np.float32)
    for y in range(size):
        for x in range(size):
            nx = x / size * 4.0
            ny = y / size * 4.0
            base = fbm(nx, ny, octaves=7, persistence=0.55, seed=seed)
            ridge = 1.0 - abs(base * 2 - 1)
            detail = fbm(nx * 3, ny * 3, octaves=4, persistence=0.4, seed=seed + 100)
            h[y, x] = ridge * 0.7 + detail * 0.3
    return h


def generate_hills(size, seed):
    """丘陵：中振幅中频"""
    h = np.zeros((size, size), dtype=np.float32)
    for y in range(size):
        for x in range(size):
            nx = x / size * 6.0
            ny = y / size * 6.0
            h[y, x] = fbm(nx, ny, octaves=5, persistence=0.5, seed=seed)
    return h * 0.5


def generate_plains(size, seed):
    """平原：小振幅高频"""
    h = np.zeros((size, size), dtype=np.float32)
    for y in range(size):
        for x in range(size):
            nx = x / size * 10.0
            ny = y / size * 10.0
            h[y, x] = fbm(nx, ny, octaves=4, persistence=0.3, seed=seed) * 0.15
    return h


def generate_canyon(size, seed):
    """峡谷：噪声 + 河流下切"""
    h = generate_mountains(size, seed) * 0.6
    river_y = size // 2 + int(fbm(0, seed, octaves=3, seed=seed + 999) * size * 0.3)
    for x in range(size):
        for dy in range(-20, 21):
            yy = river_y + dy + int(fbm(x / size * 5, 0, octaves=3, seed=seed + 500) * 30)
            if 0 <= yy < size:
                dist = abs(dy) / 20.0
                cut = (1.0 - dist) ** 2 * 0.5
                h[yy, x] -= cut
    h = np.clip(h, 0, 1)
    return h


def generate_archipelago(size, seed):
    """群岛：阈值截断 + 海平面"""
    h = np.zeros((size, size), dtype=np.float32)
    for y in range(size):
        for x in range(size):
            nx = x / size * 5.0
            ny = y / size * 5.0
            h[y, x] = fbm(nx, ny, octaves=6, persistence=0.5, seed=seed)
    sea_level = np.percentile(h, 55)
    h = np.where(h < sea_level, h * 0.1, (h - sea_level) / (1 - sea_level) * 0.9 + 0.1)
    return h


STYLES = {
    "mountains": generate_mountains,
    "hills": generate_hills,
    "plains": generate_plains,
    "canyon": generate_canyon,
    "archipelago": generate_archipelago,
}


def normalize(h):
    """归一化到 0-1"""
    lo, hi = h.min(), h.max()
    if hi - lo < 1e-9:
        return np.zeros_like(h)
    return (h - lo) / (hi - lo)


def save_heightmap_png(h, path):
    """保存 16-bit 灰度 PNG"""
    h16 = (h * 65535).astype(np.uint16)
    Image.fromarray(h16, mode="I;16").save(path)


def save_preview(h, path, title="Heightmap"):
    """保存彩色预览图"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        axes[0].imshow(h, cmap="terrain", origin="lower")
        axes[0].set_title(f"{title} - 俯视")
        axes[1].imshow(h, cmap="gray", origin="lower")
        axes[1].set_title(f"{title} - 高度图")
        plt.tight_layout()
        plt.savefig(path, dpi=100)
        plt.close()
    except ImportError:
        pass


def main():
    parser = argparse.ArgumentParser(description="PCG 地形生成器")
    parser.add_argument("--output", "-o", default="heightmap.png", help="输出高度图 PNG 路径")
    parser.add_argument("--size", "-s", type=int, default=512, help="分辨率 (默认 512)")
    parser.add_argument("--style", type=str, default="mountains",
                        choices=list(STYLES.keys()), help="地形风格 (默认 mountains)")
    parser.add_argument("--seed", type=int, default=42, help="随机种子 (默认 42)")
    args = parser.parse_args()

    print(f"生成地形: {args.style}, 分辨率 {args.size}x{args.size}, seed={args.seed}")

    gen = STYLES[args.style]
    h = gen(args.size, args.seed)
    h = normalize(h)

    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    npy_path = os.path.join(out_dir, "heightmap.npy")
    np.save(npy_path, h)
    print(f"  高度图数据: {npy_path}")

    save_heightmap_png(h, args.output)
    print(f"  高度图 PNG: {args.output}")

    preview_path = os.path.join(out_dir, "heightmap_preview.png")
    save_preview(h, preview_path, f"{args.style} heightmap")
    if os.path.exists(preview_path):
        print(f"  预览图: {preview_path}")

    stats = {"min": float(h.min()), "max": float(h.max()), "mean": float(h.mean()),
             "style": args.style, "size": args.size, "seed": args.seed}
    print(f"  统计: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
