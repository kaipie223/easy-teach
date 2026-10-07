"""从参考图里取主色，产出可直接用的主题令牌（不靠肉眼猜）。

用法：
  python scripts/sample_theme_colors.py <参考图1> <参考图2> ...

做法：缩图后用中位切分量化到 12 色，再按像素占比排序输出十六进制值。
量化是为了抵消 JPEG 压缩与抗锯齿产生的杂色，同时保留版面的大色块结构。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 环境缺依赖时给出可执行提示
    print("需要 Pillow：.\\.venv\\Scripts\\python.exe -m pip install Pillow")
    raise SystemExit(1)


def dominant_colors(path: Path, top: int = 8) -> list[tuple[str, float]]:
    image = Image.open(path).convert("RGB")
    image.thumbnail((240, 240))
    quantized = image.quantize(colors=12, method=Image.MEDIANCUT).convert("RGB")
    pixels = list(quantized.getdata())
    counter = Counter(pixels)
    total = len(pixels)
    return [
        ("#%02X%02X%02X" % rgb, count / total * 100)
        for rgb, count in counter.most_common(top)
    ]


def blue_family(path: Path) -> list[tuple[str, str]]:
    """自动找出这一页里的"主蓝"：最饱和的蓝与最深的蓝。

    按坐标取样很容易被版面的白边带偏；直接在全图里找"蓝色分量占优且饱和度高"的
    像素更稳——主色条、编号数字、章节标题都是这种像素。
    """
    image = Image.open(path).convert("RGB")
    image.thumbnail((400, 400))
    most_saturated = None
    darkest = None
    for red, green, blue in list(image.getdata()):
        if blue <= red + 20 or blue < 90:
            continue
        saturation = blue - min(red, green)
        if most_saturated is None or saturation > most_saturated[0]:
            most_saturated = (saturation, (red, green, blue))
        if darkest is None or blue < darkest[0]:
            darkest = (blue, (red, green, blue))
    results = []
    if most_saturated:
        results.append(("最饱和蓝", "#%02X%02X%02X" % most_saturated[1]))
    if darkest:
        results.append(("最深蓝", "#%02X%02X%02X" % darkest[1]))
    return results


def sample_at(path: Path, points: list[tuple[str, float, float]]) -> list[tuple[str, str]]:
    """按归一化坐标取具体位置的颜色（色条、标题、底色这种"指定位置"更可靠）。"""
    image = Image.open(path).convert("RGB")
    width, height = image.size
    results = []
    for label, x_ratio, y_ratio in points:
        x = min(width - 1, max(0, int(width * x_ratio)))
        y = min(height - 1, max(0, int(height * y_ratio)))
        results.append((label, "#%02X%02X%02X" % image.getpixel((x, y))))
    return results


for target in sys.argv[1:]:
    path = Path(target)
    if not path.exists():
        print(f"找不到文件：{path}")
        continue
    print(f"\n=== {path.name} 主色 ===")
    for hex_value, share in dominant_colors(path):
        bar = "█" * max(1, int(share / 2))
        print(f"  {hex_value}  {share:5.1f}%  {bar}")
    for label, hex_value in blue_family(path):
        print(f"  {label}：{hex_value}")
