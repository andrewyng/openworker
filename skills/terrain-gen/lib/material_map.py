#!/usr/bin/env python3
"""PCG 材质分布 - 根据高度/坡度/沉积物/流量自动分配地表材质

输出：彩色材质 ID 图 (PNG) + 各材质覆盖率统计
"""

import argparse
import json
import os

try:
    import numpy as np
except ImportError:
    raise SystemExit("需要 numpy: pip install numpy")

try:
    from PIL import Image
except ImportError:
    raise SystemExit("需要 Pillow: pip install Pillow")


DEFAULT_MATERIALS = {
    "layers": [
        {"name": "snow",   "color": [255, 255, 255], "min_height": 0.80, "max_slope": 25},
        {"name": "rock",   "color": [107, 107, 107], "min_slope": 35},
        {"name": "gravel", "color": [139, 115, 85],  "min_slope": 25, "max_slope": 35},
        {"name": "sand",   "color": [194, 178, 128], "min_flow": 0.3},
        {"name": "soil",   "color": [91, 58, 26],    "min_sediment": 0.2},
        {"name": "grass",  "color": [74, 124, 58],   "min_height": 0.15, "max_height": 0.75, "max_slope": 25},
        {"name": "dirt",   "color": [61, 43, 31],    "default": True},
    ]
}


def compute_slope(heightmap):
    """计算坡度图（度）"""
    gy, gx = np.gradient(heightmap)
    slope_rad = np.arctan(np.sqrt(gx * gx + gy * gy))
    return np.degrees(slope_rad)


def assign_materials(heightmap, sediment, flow, config):
    """根据规则分配材质

    规则按顺序匹配，第一个满足条件的材质胜出。
    每个像素的材质由高度、坡度、沉积物、流量共同决定。
    """
    size = heightmap.shape[0]
    slope = compute_slope(heightmap)

    sed_norm = (sediment - sediment.min()) / (sediment.max() - sediment.min() + 1e-9)
    flow_norm = (flow - flow.min()) / (flow.max() - flow.min() + 1e-9)

    material_id = np.zeros((size, size), dtype=np.uint8)
    material_rgb = np.zeros((size, size, 3), dtype=np.uint8)

    layers = config.get("layers", DEFAULT_MATERIALS["layers"])
    default_layer = None
    for i, layer in enumerate(layers):
        if layer.get("default"):
            default_layer = i

    for y in range(size):
        for x in range(size):
            assigned = False
            for i, layer in enumerate(layers):
                if layer.get("default"):
                    continue
                h = heightmap[y, x]
                s = slope[y, x]
                sed = sed_norm[y, x]
                flw = flow_norm[y, x]

                if "min_height" in layer and h < layer["min_height"]:
                    continue
                if "max_height" in layer and h > layer["max_height"]:
                    continue
                if "min_slope" in layer and s < layer["min_slope"]:
                    continue
                if "max_slope" in layer and s > layer["max_slope"]:
                    continue
                if "min_sediment" in layer and sed < layer["min_sediment"]:
                    continue
                if "min_flow" in layer and flw < layer["min_flow"]:
                    continue

                material_id[y, x] = i
                material_rgb[y, x] = layer["color"]
                assigned = True
                break

            if not assigned:
                idx = default_layer if default_layer is not None else len(layers) - 1
                material_id[y, x] = idx
                material_rgb[y, x] = layers[idx]["color"]

    return material_id, material_rgb, slope


def main():
    parser = argparse.ArgumentParser(description="PCG 材质分布")
    parser.add_argument("--heightmap", "-i", required=True, help="输入高度图 .npy")
    parser.add_argument("--sediment", default=None, help="沉积物图 .npy")
    parser.add_argument("--flow", default=None, help="流量累积图 .npy")
    parser.add_argument("--output", "-o", default="material_id.png", help="输出材质 ID 图")
    parser.add_argument("--materials", default=None, help="材质规则 JSON 配置")
    args = parser.parse_args()

    h = np.load(args.heightmap)
    sediment = np.load(args.sediment) if args.sediment and os.path.exists(args.sediment) else np.zeros_like(h)
    flow = np.load(args.flow) if args.flow and os.path.exists(args.flow) else np.zeros_like(h)

    config = DEFAULT_MATERIALS
    if args.materials and os.path.exists(args.materials):
        with open(args.materials) as f:
            config = json.load(f)

    print("计算材质分布...")
    mat_id, mat_rgb, slope = assign_materials(h, sediment, flow, config)

    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    Image.fromarray(mat_rgb).save(args.output)
    print(f"  材质 ID 图: {args.output}")

    id_npy = os.path.join(out_dir, "material_id.npy")
    np.save(id_npy, mat_id)
    print(f"  材质 ID 数据: {id_npy}")

    slope_path = os.path.join(out_dir, "slope_map.png")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.imsave(slope_path, slope, cmap="YlOrRd")
        print(f"  坡度图: {slope_path}")
    except ImportError:
        pass

    layers = config.get("layers", DEFAULT_MATERIALS["layers"])
    stats = {}
    total = mat_id.size
    for i, layer in enumerate(layers):
        count = int(np.sum(mat_id == i))
        pct = count / total * 100
        stats[layer["name"]] = {"pixels": count, "coverage_pct": round(pct, 2)}
        if count > 0:
            print(f"  {layer['name']:12s}: {pct:6.2f}%  ({count} pixels)")

    stats_path = os.path.join(out_dir, "material_stats.json")
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  统计: {stats_path}")


if __name__ == "__main__":
    main()
