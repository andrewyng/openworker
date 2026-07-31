#!/usr/bin/env python3
"""PCG 3D 预览生成器 - 输出自包含 Three.js HTML

在 Gamer Worker 右侧面板直接打开，鼠标旋转/缩放/平移查看 3D 地形。
"""

import argparse
import json
import os
import csv
import math

try:
    import numpy as np
except ImportError:
    raise SystemExit("需要 numpy: pip install numpy")


def heightmap_to_mesh_data(h, material_rgb=None, max_vertices=65536):
    """高度图转网格数据"""
    size = h.shape[0]
    step = max(1, size // int(math.sqrt(max_vertices)))
    sampled = h[::step, ::step]
    s_size = sampled.shape[0]

    vertices = []
    colors = []
    for y in range(s_size):
        for x in range(s_size):
            vx = (x / (s_size - 1) - 0.5) * 100
            vy = float(sampled[y, x]) * 30
            vz = (y / (s_size - 1) - 0.5) * 100
            vertices.append([round(vx, 2), round(vy, 2), round(vz, 2)])

            if material_rgb is not None:
                mr = material_rgb[::step, ::step]
                r = int(mr[y, x, 0]) / 255.0
                g = int(mr[y, x, 1]) / 255.0
                b = int(mr[y, x, 2]) / 255.0
                colors.append([round(r, 3), round(g, 3), round(b, 3)])
            else:
                h_val = float(sampled[y, x])
                colors.append([round(h_val * 0.8, 3), round(h_val * 0.6, 3), round(h_val * 0.3, 3)])

    indices = []
    for y in range(s_size - 1):
        for x in range(s_size - 1):
            i00 = y * s_size + x
            i10 = y * s_size + x + 1
            i01 = (y + 1) * s_size + x
            i11 = (y + 1) * s_size + x + 1
            indices.append([i00, i01, i10])
            indices.append([i10, i01, i11])

    return vertices, colors, indices


def load_vegetation(csv_path, heightmap, max_points=5000):
    """加载植被点云"""
    if not csv_path or not os.path.exists(csv_path):
        return []

    size = heightmap.shape[0]
    veg = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        step = max(1, len(rows) // max_points)
        for row in rows[::step]:
            x = float(row["x"])
            y = float(row["y"])
            z = float(row["z"])
            scale = float(row["scale"])
            sp_idx = int(row.get("species_idx", 0))
            veg.append({
                "x": round((x / size - 0.5) * 100, 2),
                "y": round(z * 30, 2),
                "z": round((y / size - 0.5) * 100, 2),
                "scale": round(scale, 2),
                "species": sp_idx,
            })
    return veg


def generate_html(vertices, colors, indices, vegetation, species_colors):
    """生成自包含 Three.js HTML（内嵌 Three.js，不依赖 CDN）"""
    veg_json = json.dumps(vegetation)
    species_json = json.dumps(species_colors)
    verts_json = json.dumps(vertices)
    colors_json = json.dumps(colors)
    indices_json = json.dumps(indices)

    # 内嵌 Three.js - 从同目录加载，避免 CDN 在 sandbox iframe 中被拦
    three_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "three.min.js")
    try:
        with open(three_path, "r") as f:
            three_js = f.read()
    except (OSError, FileNotFoundError):
        three_js = ""  # fallback: CDN
    three_tag = f"<script>{three_js}</script>" if three_js else '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PCG 地形 3D 预览</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ overflow: hidden; background: #1a1a2e; font-family: -apple-system, system-ui, sans-serif; }}
  #canvas {{ width: 100vw; height: 100vh; display: block; }}
  #info {{
    position: absolute; top: 12px; left: 12px; z-index: 10;
    background: rgba(0,0,0,0.6); color: #fff; padding: 10px 14px;
    border-radius: 8px; font-size: 12px; line-height: 1.6;
    backdrop-filter: blur(8px);
  }}
  #info b {{ color: #4FC3F7; }}
  #legend {{
    position: absolute; bottom: 12px; left: 12px; z-index: 10;
    background: rgba(0,0,0,0.6); padding: 8px 12px; border-radius: 8px;
    font-size: 11px; color: #ccc;
  }}
  #legend .item {{ display: flex; align-items: center; gap: 6px; margin: 2px 0; }}
  #legend .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .hint {{ position: absolute; bottom: 12px; right: 12px; color: #555; font-size: 11px; }}
</style>
</head>
<body>
<div id="info">
  <b>PCG 地形预览</b><br>
  顶点: {len(vertices)} | 三角面: {len(indices)}<br>
  植被: {len(vegetation)} 株<br>
  <span style="color:#888">鼠标拖拽旋转 | 滚轮缩放 | 右键平移</span>
</div>
<div id="legend"></div>
<div class="hint">Powered by Gamer Worker</div>
<canvas id="canvas"></canvas>
{three_tag}
<script>
const vertices = {verts_json};
const colors = {colors_json};
const indices = {indices_json};
const vegetation = {veg_json};
const speciesColors = {species_json};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a2e);
scene.fog = new THREE.Fog(0x1a1a2e, 80, 200);

const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 500);
camera.position.set(60, 50, 60);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({{canvas: document.getElementById('canvas'), antialias: true}});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);

const ambient = new THREE.AmbientLight(0x6688aa, 0.6);
scene.add(ambient);
const sun = new THREE.DirectionalLight(0xffffff, 0.8);
sun.position.set(50, 80, 30);
scene.add(sun);
const fill = new THREE.DirectionalLight(0x8899ff, 0.3);
fill.position.set(-40, 30, -40);
scene.add(fill);

const geometry = new THREE.BufferGeometry();
geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices.flat(), 3));
geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors.flat(), 3));
geometry.setIndex(indices.flat());
geometry.computeVertexNormals();

const material = new THREE.MeshStandardMaterial({{
  vertexColors: true, roughness: 0.9, metalness: 0.0,
  side: THREE.DoubleSide, flatShading: false
}});
const mesh = new THREE.Mesh(geometry, material);
scene.add(mesh);

if (vegetation.length > 0) {{
  const trunkGeo = new THREE.ConeGeometry(0.4, 2.5, 6);
  const vegGroup = new THREE.Group();
  const materials = {{}};
  speciesColors.forEach((c, i) => {{
    materials[i] = new THREE.MeshStandardMaterial({{ color: new THREE.Color(c[0]/255, c[1]/255, c[2]/255), roughness: 0.8 }});
  }});

  vegetation.forEach(v => {{
    const m = materials[v.species] || materials[0];
    const tree = new THREE.Mesh(trunkGeo, m);
    tree.position.set(v.x, v.y + v.scale, v.z);
    tree.scale.set(v.scale, v.scale, v.scale);
    tree.rotation.y = v.x + v.z;
    vegGroup.add(tree);
  }});
  scene.add(vegGroup);
}}

const legend = document.getElementById('legend');
const uniqueSpecies = [...new Set(vegetation.map(v => v.species))];
uniqueSpecies.forEach(idx => {{
  const c = speciesColors[idx] || [128, 128, 128];
  const names = ['松树', '白桦', '灌木'];
  const item = document.createElement('div');
  item.className = 'item';
  item.innerHTML = '<span class="dot" style="background:rgb(' + c.join(',') + ')"></span>' + (names[idx] || '物种' + idx);
  legend.appendChild(item);
}});

const controls = (() => {{
  let isDown = false, isPan = false;
  let px = 0, py = 0;
  let rotX = 0.6, rotY = 0.7;
  let dist = 110;
  let panX = 0, panY = 0;
  const canvas = document.getElementById('canvas');
  canvas.addEventListener('mousedown', e => {{ isDown = true; isPan = e.button === 2; px = e.clientX; py = e.clientY; }});
  canvas.addEventListener('mouseup', () => isDown = false);
  canvas.addEventListener('mouseleave', () => isDown = false);
  canvas.addEventListener('mousemove', e => {{
    if (!isDown) return;
    const dx = e.clientX - px, dy = e.clientY - py;
    if (isPan) {{ panX -= dx * 0.1; panY += dy * 0.1; }}
    else {{ rotY += dx * 0.005; rotX = Math.max(0.05, Math.min(1.5, rotX + dy * 0.005)); }}
    px = e.clientX; py = e.clientY;
  }});
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  canvas.addEventListener('wheel', e => {{ e.preventDefault(); dist = Math.max(30, Math.min(300, dist + e.deltaY * 0.1)); }});
  return {{
    update() {{
      const cx = Math.sin(rotY) * Math.cos(rotX) * dist + panX;
      const cy = Math.sin(rotX) * dist + panY;
      const cz = Math.cos(rotY) * Math.cos(rotX) * dist;
      camera.position.set(cx, cy, cz);
      camera.lookAt(panX, panY, 0);
    }}
  }};
}})();

window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description="PCG 3D 预览生成器")
    parser.add_argument("--heightmap", "-i", required=True, help="输入高度图 .npy")
    parser.add_argument("--material", default=None, help="材质 RGB 图 (彩色 PNG 或 .npy)")
    parser.add_argument("--vegetation", default=None, help="植被 CSV")
    parser.add_argument("--output", "-o", default="preview.html", help="输出 HTML")
    args = parser.parse_args()

    h = np.load(args.heightmap)
    print(f"加载高度图: {h.shape}")

    material_rgb = None
    if args.material and os.path.exists(args.material):
        if args.material.endswith(".png"):
            from PIL import Image
            material_rgb = np.array(Image.open(args.material).convert("RGB"))
            if material_rgb.shape[0] != h.shape[0]:
                from PIL import Image as Im
                material_rgb = np.array(Im.open(args.material).convert("RGB").resize((h.shape[1], h.shape[0])))
        elif args.material.endswith(".npy"):
            material_rgb = np.load(args.material)

    print("生成网格数据...")
    vertices, colors, indices = heightmap_to_mesh_data(h, material_rgb)
    print(f"  顶点: {len(vertices)}, 三角面: {len(indices)}")

    species_colors = [[45, 90, 39], [90, 138, 58], [107, 107, 90]]
    vegetation = load_vegetation(args.vegetation, h)
    print(f"  植被: {len(vegetation)} 株")

    html = generate_html(vertices, colors, indices, vegetation, species_colors)

    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  3D 预览: {args.output}")
    print(f"  在 Gamer Worker 右侧面板打开此文件即可查看 3D 效果")


if __name__ == "__main__":
    main()
