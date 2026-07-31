#!/usr/bin/env python3
"""PCG 植被散布 - Poisson disk 采样 + 生态规则

根据高度/坡度/材质/湿度决定植被类型和密度
输出：CSV (x, y, z, type, scale, rotation) + 密度分布预览图
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


DEFAULT_VEGETATION = {
    "species": [
        {
            "name": "pine", "color": [45, 90, 39], "min_size": 0.8, "max_size": 1.5,
            "rules": {"min_height": 0.25, "max_height": 0.75, "max_slope": 30, "materials": [5, 4]},
            "density": 0.6
        },
        {
            "name": "birch", "color": [90, 138, 58], "min_size": 0.5, "max_size": 1.0,
            "rules": {"min_height": 0.15, "max_height": 0.5, "max_slope": 20, "materials": [5]},
            "density": 0.4
        },
        {
            "name": "bush", "color": [107, 107, 90], "min_size": 0.2, "max_size": 0.5,
            "rules": {"min_slope": 30, "materials": [1, 2]},
            "density": 0.3
        }
    ]
}


def compute_slope(heightmap):
    gy, gx = np.gradient(heightmap)
    return np.degrees(np.arctan(np.sqrt(gx * gx + gy * gy)))


def poisson_disk_sampling(size, min_dist, max_attempts=30, seed=42):
    """Poisson disk 采样 - 生成均匀分布但不过密的点"""
    cell_size = min_dist / math.sqrt(2)
    grid_w = int(math.ceil(size / cell_size))
    grid = [None] * (grid_w * grid_w)
    points = []
    active = []

    rng = random.Random(seed)
    first = (rng.uniform(0, size), rng.uniform(0, size))
    points.append(first)
    active.append(first)
    grid[int(first[1] / cell_size) * grid_w + int(first[0] / cell_size)] = 0

    while active:
        idx = rng.randint(0, len(active) - 1)
        cx, cy = active[idx]
        found = False

        for _ in range(max_attempts):
            angle = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(min_dist, min_dist * 2)
            nx = cx + r * math.cos(angle)
            ny = cy + r * math.sin(angle)

            if nx < 0 or nx >= size or ny < 0 or ny >= size:
                continue

            gx_idx = int(nx / cell_size)
            gy_idx = int(ny / cell_size)
            gx0 = max(0, gx_idx - 2)
            gx1 = min(grid_w, gx_idx + 3)
            gy0 = max(0, gy_idx - 2)
            gy1 = min(grid_w, gy_idx + 3)

            ok = True
            for gy2 in range(gy0, gy1):
                for gx2 in range(gx0, gx1):
                    cell = grid[gy2 * grid_w + gx2]
                    if cell is not None:
                        px, py = points[cell]
                        if (nx - px) ** 2 + (ny - py) ** 2 < min_dist * min_dist:
                            ok = False
                            break
                if not ok:
                    break

            if ok:
                points.append((nx, ny))
                active.append((nx, ny))
                grid[gy_idx * grid_w + gx_idx] = len(points) - 1
                found = True
                break

        if not found:
            active.pop(idx)

    return points


def scatter_vegetation(heightmap, material_id, slope, config, size, seed=42):
    """按生态规则散布植被"""
    rng = random.Random(seed)
    species = config.get("species", DEFAULT_VEGETATION["species"])

    density_map = np.zeros((size, size), dtype=np.float32)
    vegetation = []

    for sp_idx, sp in enumerate(species):
        rules = sp.get("rules", {})
        density = sp.get("density", 0.5)
        min_size = sp.get("min_size", 0.5)
        max_size = sp.get("max_size", 1.5)
        name = sp.get("name", f"species_{sp_idx}")

        min_dist = 3.0 / density
        points = poisson_disk_sampling(size, min_dist, seed=seed + sp_idx)

        valid = 0
        for px, py in points:
            ix, iy = int(px), int(py)
            if ix < 0 or ix >= size or iy < 0 or iy >= size:
                continue

            h = heightmap[iy, ix]
            s = slope[iy, ix]
            mat = int(material_id[iy, ix])

            if "min_height" in rules and h < rules["min_height"]:
                continue
            if "max_height" in rules and h > rules["max_height"]:
                continue
            if "max_slope" in rules and s > rules["max_slope"]:
                continue
            if "min_slope" in rules and s < rules["min_slope"]:
                continue
            if "materials" in rules and mat not in rules["materials"]:
                continue

            scale = rng.uniform(min_size, max_size)
            rotation = rng.uniform(0, 360)
            z = h

            vegetation.append({
                "x": float(px), "y": float(py), "z": float(z),
                "type": name, "species_idx": sp_idx,
                "scale": round(scale, 3), "rotation": round(rotation, 1)
            })
            density_map[iy, ix] += 1
            valid += 1

        print(f"  {name}: {valid} 株 (密度 {density}, 间距 {min_dist:.1f})")

    return vegetation, density_map


def main():
    parser = argparse.ArgumentParser(description="PCG 植被散布")
    parser.add_argument("--heightmap", "-i", required=True, help="输入高度图 .npy")
    parser.add_argument("--material", default=None, help="材质 ID 图 .npy")
    parser.add_argument("--output", "-o", default="vegetation.csv", help="输出 CSV")
    parser.add_argument("--config", default=None, help="植被规则 JSON 配置")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    h = np.load(args.heightmap)
    size = h.shape[0]

    if args.material and os.path.exists(args.material):
        mat_id = np.load(args.material)
    else:
        mat_id = np.zeros_like(h, dtype=np.uint8)

    config = DEFAULT_VEGETATION
    if args.config and os.path.exists(args.config):
        with open(args.config) as f:
            config = json.load(f)

    slope = compute_slope(h)

    print("散布植被...")
    veg, density_map = scatter_vegetation(h, mat_id, slope, config, size, args.seed)

    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    import csv
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["x", "y", "z", "type", "species_idx", "scale", "rotation"])
        writer.writeheader()
        writer.writerows(veg)
    print(f"  植被数据: {args.output} ({len(veg)} 株)")

    density_path = os.path.join(out_dir, "vegetation_density.png")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.imsave(density_path, density_map, cmap="YlGn")
        print(f"  密度图: {density_path}")
    except ImportError:
        d_norm = (density_map - density_map.min()) / (density_map.max() + 1e-9)
        d8 = (d_norm * 255).astype(np.uint8)
        Image.fromarray(d8, mode="L").save(density_path)
        print(f"  密度图: {density_path}")

    by_type = {}
    for v in veg:
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1
    print(f"  统计: {json.dumps(by_type, indent=2)}")


if __name__ == "__main__":
    main()
