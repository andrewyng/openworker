#!/usr/bin/env python3
"""PCG 侵蚀模拟 - 水力侵蚀 + 热力侵蚀

水力侵蚀：粒子模拟水流，携带沉积物，陡坡侵蚀、低洼沉积
热力侵蚀：陡坡物质崩落至休止角
输出：侵蚀后高度图 + 沉积物图 + 流量累积图
"""

import argparse
import json
import os
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


def hydraulic_erosion(heightmap, num_particles=50000, rain_amount=0.01,
                      evaporation=0.02, sediment_capacity=0.1, erosion_rate=0.3,
                      deposition_rate=0.3, seed=42):
    """水力侵蚀 - 粒子模拟

    每个粒子从随机位置开始，沿梯度向下流动。
    携带沉积物：速度快时侵蚀（带走物质），速度慢时沉积（留下物质）。
    """
    h = heightmap.copy()
    size = h.shape[0]
    sediment = np.zeros_like(h)
    flow = np.zeros_like(h)

    rng = random.Random(seed)

    for _ in range(num_particles):
        x = rng.uniform(1, size - 2)
        y = rng.uniform(1, size - 2)
        water = rain_amount
        sediment_carry = 0.0
        vel = 0.0
        old_h = h[int(y), int(x)]

        steps = 0
        max_steps = 128
        while steps < max_steps and water > 0.001:
            ix, iy = int(x), int(y)
            if ix < 1 or ix >= size - 1 or iy < 1 or iy >= size - 1:
                break

            flow[iy, ix] += water

            gx = (h[iy, ix + 1] - h[iy, ix - 1]) * 0.5
            gy = (h[iy + 1, ix] - h[iy - 1, ix]) * 0.5

            len_g = math.sqrt(gx * gx + gy * gy)
            if len_g < 1e-6:
                break

            x -= gx / len_g
            y -= gy / len_g

            ix2, iy2 = int(x), int(y)
            if ix2 < 1 or ix2 >= size - 1 or iy2 < 1 or iy2 >= size - 1:
                break

            new_h = h[iy2, ix2]
            dh = old_h - new_h

            vel = vel * 0.9 + dh * 0.1
            capacity = max(0, vel * water * sediment_capacity)

            if sediment_carry < capacity:
                amount = min((capacity - sediment_carry) * erosion_rate, dh * 0.5)
                if amount > 0:
                    h[iy, ix] -= amount
                    sediment[iy, ix] += amount
                    sediment_carry += amount
            else:
                amount = (sediment_carry - capacity) * deposition_rate
                if amount > 0:
                    h[iy2, ix2] += amount
                    sediment[iy2, ix2] += amount
                    sediment_carry -= amount

            water *= (1.0 - evaporation)
            old_h = new_h
            steps += 1

    return h, sediment, flow


def thermal_erosion(heightmap, iterations=30, talus_angle=45, erosion_strength=0.5):
    """热力侵蚀 - 陡坡物质崩落

    每次迭代检查每个点的坡度，超过休止角的物质向低处转移。
    """
    h = heightmap.copy()
    size = h.shape[0]
    talus = math.tan(math.radians(talus_angle)) * (2.0 / size)

    for _ in range(iterations):
        new_h = h.copy()
        gx = np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)
        gy = np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)

        neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]

        for dy, dx in neighbors:
            diff = h - np.roll(h, (dy, dx), axis=(0, 1))
            mask = diff > talus
            transfer = np.zeros_like(h)
            transfer[mask] = diff[mask] * erosion_strength * 0.125
            new_h -= transfer
            new_h = new_h + np.roll(transfer, (-dy, -dx), axis=(0, 1))

        h = new_h

    return h


def save_png(arr, path, mode="gray", cmap=None):
    """保存数组为 PNG"""
    arr_norm = (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)
    if cmap:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.imsave(path, arr_norm, cmap=cmap)
            return
        except ImportError:
            pass
    arr8 = (arr_norm * 255).astype(np.uint8)
    Image.fromarray(arr8, mode="L").save(path)


def main():
    parser = argparse.ArgumentParser(description="PCG 侵蚀模拟")
    parser.add_argument("--input", "-i", required=True, help="输入高度图 .npy")
    parser.add_argument("--output", "-o", default="eroded.npy", help="输出侵蚀后高度图")
    parser.add_argument("--hydraulic-iterations", type=int, default=50000, help="水力侵蚀粒子数")
    parser.add_argument("--thermal-iterations", type=int, default=30, help="热力侵蚀迭代次数")
    parser.add_argument("--rain-amount", type=float, default=0.01, help="降雨量")
    parser.add_argument("--evaporation", type=float, default=0.02, help="蒸发率")
    parser.add_argument("--sediment-capacity", type=float, default=0.1, help="携沙能力")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    h = np.load(args.input)
    print(f"加载高度图: {h.shape}, 范围 [{h.min():.4f}, {h.max():.4f}]")

    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    original = h.copy()

    if args.thermal_iterations > 0:
        print(f"热力侵蚀: {args.thermal_iterations} 次迭代...")
        h = thermal_erosion(h, iterations=args.thermal_iterations)

    if args.hydraulic_iterations > 0:
        print(f"水力侵蚀: {args.hydraulic_iterations} 个粒子...")
        h, sediment, flow = hydraulic_erosion(
            h, num_particles=args.hydraulic_iterations,
            rain_amount=args.rain_amount, evaporation=args.evaporation,
            sediment_capacity=args.sediment_capacity, seed=args.seed)
    else:
        sediment = np.zeros_like(h)
        flow = np.zeros_like(h)

    np.save(args.output, h)
    print(f"  侵蚀后高度图: {args.output}")

    sed_path = os.path.join(out_dir, "sediment.npy")
    np.save(sed_path, sediment)
    print(f"  沉积物图: {sed_path}")

    flow_path = os.path.join(out_dir, "flow.npy")
    np.save(flow_path, flow)
    print(f"  流量累积图: {flow_path}")

    save_png(h, os.path.join(out_dir, "eroded_heightmap.png"), cmap="terrain")
    save_png(sediment, os.path.join(out_dir, "sediment_map.png"), cmap="YlOrBr")
    save_png(flow, os.path.join(out_dir, "flow_map.png"), cmap="Blues")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(18, 5))
        axes[0].imshow(original, cmap="terrain", origin="lower")
        axes[0].set_title("侵蚀前")
        axes[1].imshow(h, cmap="terrain", origin="lower")
        axes[1].set_title("侵蚀后")
        diff = h - original
        axes[2].imshow(diff, cmap="RdBu_r", origin="lower", vmin=-0.05, vmax=0.05)
        axes[2].set_title("变化量 (红=侵蚀, 蓝=沉积)")
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, "erosion_comparison.png"), dpi=100)
        print(f"  对比图: {os.path.join(out_dir, 'erosion_comparison.png')}")
    except ImportError:
        pass

    stats = {
        "erosion_total": float(np.sum(original - h)),
        "deposition_total": float(np.sum(h - original)),
        "sediment_mean": float(sediment.mean()),
        "flow_max": float(flow.max()),
    }
    print(f"  统计: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
