---
name: 地形生成
description: PCG地形生成 - 噪声地形、水力侵蚀、热力侵蚀、材质分布、植被散布、Three.js 3D预览
allowed-tools: read_file, write_file, replace_in_file, apply_patch, run_shell, grep, todo_write
---

# 地形生成 Skill

本 Skill 提供完整的程序化地形生成能力，包含噪声地形、侵蚀模拟、材质分布、植被散布和 Three.js 3D 预览。

## 工作流程

### 第一步：理解需求
1. 确认地形参数：分辨率、尺寸范围、地形风格（山脉/丘陵/平原/峡谷）
2. 确认侵蚀需求：水力侵蚀强度、热力侵蚀、河流数量
3. 确认材质需求：需要哪些材质层（裸岩/碎石/土壤/草地/沙地/雪地等）
4. 确认植被需求：植被类型、密度规则、生态群落
5. 确认输出格式：Three.js 预览 + 高度图 PNG + 数据文件

### 第二步：生成基础地形
使用 `lib/terrain_gen.py` 生成基础高度图：
```bash
python3 lib/terrain_gen.py --output heightmap.png --size 512 --style mountains --seed 42
```
- 支持的 style: mountains, hills, plains, canyon, archipelago
- 输出 16-bit 灰度 PNG 高度图
- 同时输出 heightmap.npy 供后续步骤使用

### 第三步：侵蚀模拟
使用 `lib/erosion.py` 对高度图施加侵蚀：
```bash
python3 lib/erosion.py --input heightmap.npy --output eroded.npy \
  --hydraulic-iterations 50000 --thermal-iterations 30 \
  --rain-amount 0.01 --evaporation 0.02 --sediment-capacity 0.1
```
- 水力侵蚀：粒子模拟水流，携带沉积物，陡坡侵蚀、低洼沉积
- 热力侵蚀：陡坡物质崩落至休止角
- 输出侵蚀后高度图 + 沉积物图 + 流量累积图
- 生成侵蚀前后对比预览图

### 第四步：材质分布
使用 `lib/material_map.py` 根据侵蚀结果分配材质：
```bash
python3 lib/material_map.py --heightmap eroded.npy --sediment sediment.npy \
  --flow flow.npy --output material_id.png --materials config/materials.json
```
- 按高度/坡度/沉积物/流量自动分配材质
- 输出彩色材质 ID 图（PNG）+ 各材质覆盖率统计
- 材质规则可通过 JSON 配置自定义

### 第五步：植被散布
使用 `lib/vegetation.py` 按生态规则散布植被：
```bash
python3 lib/vegetation.py --heightmap eroded.npy --material material_id.npy \
  --output vegetation.csv --config config/vegetation.json
```
- Poisson disk 采样，避免重叠
- 按坡度/高度/材质/湿度决定植被类型和密度
- 输出 CSV（x, y, z, type, scale, rotation）
- 生成密度分布预览图

### 第六步：Three.js 3D 预览
使用 `lib/preview.py` 生成交互式 3D 预览：
```bash
python3 lib/preview.py --heightmap eroded.npy --material material_id.npy \
  --vegetation vegetation.csv --output preview.html
```
- 生成自包含 HTML 文件（内嵌 Three.js）
- 地形 mesh 顶点着色按材质
- 植被渲染为 instanced mesh
- 鼠标旋转/缩放/平移
- 在 Gamer Worker 右侧面板直接打开

## 参数调优指南

### 地形风格
- mountains: 大振幅低频噪声 + 中频细节，适合高山
- hills: 中振幅中频噪声，适合丘陵
- plains: 小振幅高频噪声，适合平原
- canyon: 噪声 + 河流下切，适合峡谷
- archipelago: 阈值截断 + 海平面，适合群岛

### 侵蚀参数
- rain_amount (0.001-0.05): 降雨量，越大侵蚀越强
- evaporation (0.01-0.05): 蒸发率，影响水流距离
- sediment_capacity (0.01-0.5): 携沙能力，影响沉积分布
- thermal_iterations (10-50): 热力侵蚀迭代次数，越大坡面越平滑
- hydraulic_iterations (10000-100000): 水力侵蚀粒子数，越大效果越明显

### 迭代建议
- 先用 256x256 低分辨率快速调参
- 方向对了再跑 512 或 1024 高分辨率
- 每次调整只改一个参数，观察预览图变化
- 用 --seed 保持可复现性

## 自定义配置

### 材质规则（materials.json）
```json
{
  "layers": [
    {"name": "snow",   "color": "#FFFFFF", "min_height": 0.8, "max_slope": 20},
    {"name": "rock",   "color": "#6B6B6B", "min_slope": 35},
    {"name": "gravel", "color": "#8B7355", "min_slope": 25, "max_slope": 35},
    {"name": "soil",   "color": "#5B3A1A", "min_sediment": 0.3},
    {"name": "sand",   "color": "#C2B280", "min_flow": 0.5},
    {"name": "grass",  "color": "#4A7C3A", "min_height": 0.2, "max_height": 0.7, "max_slope": 25},
    {"name": "dirt",   "color": "#3D2B1F", "default": true}
  ]
}
```

### 植被规则（vegetation.json）
```json
{
  "species": [
    {
      "name": "pine", "color": "#2D5A27", "min_size": 0.8, "max_size": 1.5,
      "rules": {"min_height": 0.3, "max_height": 0.75, "max_slope": 30, "materials": ["soil", "grass"]},
      "density": 0.7
    },
    {
      "name": "birch", "color": "#5A8A3A", "min_size": 0.5, "max_size": 1.0,
      "rules": {"min_height": 0.2, "max_height": 0.5, "max_slope": 20, "materials": ["grass"]},
      "density": 0.4
    },
    {
      "name": "rock_brush", "color": "#6B6B5A", "min_size": 0.2, "max_size": 0.5,
      "rules": {"min_slope": 35, "materials": ["rock", "gravel"]},
      "density": 0.3
    }
  ]
}
```

## 注意事项
- 所有脚本依赖 numpy 和 Pillow，用前确认已安装：`pip install numpy Pillow matplotlib`
- Three.js 预览从 CDN 加载，需要网络（或可改为本地内嵌）
- 大分辨率（>1024）侵蚀模拟可能需要 30 秒以上
- 输出文件默认放在工作区的 pcg_output/ 目录下
