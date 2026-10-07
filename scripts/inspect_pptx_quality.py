"""临时：逐形状体检一份导出的 PPTX，找出真实的质量问题。

量的是"文件里实际写进去的东西"：字号、文本框容量与实际字数、是否溢出、
有无配图、讲稿长度。没有 LibreOffice 也能据此判断投影效果。
"""

import sys
from math import ceil
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

EMU_PER_PT = 12700


def text_width_em(text: str) -> float:
    """粗略字宽：中日韩全角按 1 em，其余按 0.55 em。"""
    width = 0.0
    for char in text:
        width += 1.0 if ord(char) > 0x2E80 else 0.55
    return width


def summarize(path: Path) -> None:
    """只统计"正文"（排除页眉页码），打印一行可比对的指标。"""
    presentation = Presentation(str(path))
    chrome: list[float] = []
    content: list[float] = []
    bullet_widths: list[float] = []
    graphics: list[int] = []
    for slide in presentation.slides:
        # 图形数：不承载文字的形状（色块、圆、箭头、面板）。
        # 注意 python-pptx 的自选图形默认就带 text_frame，所以不能只看 has_text_frame，
        # 要看它有没有真的写字。
        graphics.append(
            sum(
                1
                for shape in slide.shapes
                if not (shape.has_text_frame and shape.text_frame.text.strip())
            )
        )
        for shape in slide.shapes:
            width_in = shape.width / 914400 if shape.width is not None else 0
            top_in = shape.top / 914400 if shape.top is not None else 0
            if not shape.has_text_frame:
                # 无文字的圆角面板：正文底板，宽度直接反映"文字能占多宽"
                if 4.0 < width_in < 12.5 and 3.5 < shape.height / 914400 < 5.0:
                    panel_widths.append(width_in)
                continue
            sizes = []
            for paragraph in shape.text_frame.paragraphs:
                if paragraph.font.size is not None:
                    sizes.append(paragraph.font.size.pt)
                for run in paragraph.runs:
                    if run.font.size is not None and run.text.strip() not in {"●", "·", "▪"}:
                        sizes.append(run.font.size.pt)
            if not sizes:
                continue
            text = shape.text_frame.text
            if "●" in text and top_in < 6.8:
                bullet_widths.append(width_in)
            smallest = min(sizes)
            (chrome if top_in > 6.8 else content).append(smallest)
    small = [size for size in content if size < 14]
    print(
        f"{path.name[:30]:<30} 页 {len(presentation.slides):>3} | "
        f"页眉 {(min(chrome) if chrome else 0):>4.1f}pt | "
        f"正文最小 {(min(content) if content else 0):>4.1f}pt | "
        f"<14pt {(len(small)):>2}/{len(content)} | "
        f"正文栏宽 {(max(bullet_widths) if bullet_widths else 0):>5.2f}in | "
        f"图形/页 {(sum(graphics) / len(graphics) if graphics else 0):>4.1f}"
    )


def inspect(path: Path) -> None:
    presentation = Presentation(str(path))
    print("=" * 78)
    print(f"文件: {path.name}")
    print(f"页数: {len(presentation.slides)}  尺寸: "
          f"{presentation.slide_width / 914400:.2f}in x {presentation.slide_height / 914400:.2f}in")

    problems: list[str] = []
    small_items: list[tuple[float, int, str]] = []
    for index, slide in enumerate(presentation.slides, start=1):
        pictures = 0
        boxes = []
        for shape in slide.shapes:
            if shape.shape_type == 13 or shape.__class__.__name__ == "Picture":
                pictures += 1
                continue
            if not shape.has_text_frame:
                continue
            text = "\n".join(paragraph.text for paragraph in shape.text_frame.paragraphs)
            if not text.strip():
                continue
            # 字号可能设在 run 级，也可能设在段落级（生成器用的是段落级）；
            # 只读 run 级会拿到 None 并退化成默认值，那是假数据。
            sizes = []
            for paragraph in shape.text_frame.paragraphs:
                if paragraph.font.size is not None:
                    sizes.append(paragraph.font.size.pt)
                for run in paragraph.runs:
                    # 项目符号"●"是刻意的 0.6 倍小标记，不是正文字号
                    if run.font.size is not None and run.text.strip() not in {"●", "·", "▪"}:
                        sizes.append(run.font.size.pt)
            size = min(sizes) if sizes else 18.0
            box_width_pt = shape.width / EMU_PER_PT
            box_height_pt = shape.height / EMU_PER_PT
            capacity = max(box_width_pt / size, 1)
            lines = 0
            for paragraph in text.split("\n"):
                lines += max(1, ceil(text_width_em(paragraph) / capacity))
            needed_pt = lines * size * 1.2
            boxes.append(
                {
                    "size": size,
                    "lines": lines,
                    "needed": needed_pt,
                    "height": box_height_pt,
                    "text": text.replace("\n", " / ")[:46],
                }
            )
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()

        for box in boxes:
            small_items.append((box["size"], index, box["text"]))
        overflow = [box for box in boxes if box["needed"] > box["height"] * 1.02]
        tiny = [box for box in boxes if box["size"] < 14]
        min_size = min([box["size"] for box in boxes], default=0)
        layout_name = slide.slide_layout.name
        print(
            f"  第 {index:>2} 页 | {layout_name:<16} | 文本块 {len(boxes):>2} | 图 {pictures} | "
            f"最小字号 {min_size:>4.1f} | 讲稿 {len(notes):>4} 字"
            + (f" | ★ 溢出 {len(overflow)} 块" if overflow else "")
            + (f" | ★ 过小字号 {len(tiny)} 块" if tiny else "")
        )
        if overflow:
            for box in overflow[:2]:
                print(
                    f"        溢出：需 {box['needed']:.0f}pt / 框高 {box['height']:.0f}pt"
                    f"（字号 {box['size']:.0f}，{box['lines']} 行）「{box['text']}」"
                )
                problems.append(f"slide{index} overflow")
        if notes and len(notes) < 60:
            problems.append(f"slide{index} thin-notes")
        if not notes:
            problems.append(f"slide{index} no-notes")

    print(f"\n合计问题标记: {len(problems)} 处 -> {problems[:12]}")
    print("最小的 6 个文本块（字号 / 页 / 内容）：")
    for size, index, text in sorted(small_items)[:6]:
        print(f"  {size:>5.1f}pt  第{index:>2}页  「{text}」")

    # 字号分布：投影场景下低于 14pt 基本不可读（正文），装饰性信息低于 10pt 也不合适
    distribution: dict[float, int] = {}
    for size, _, _ in small_items:
        distribution[size] = distribution.get(size, 0) + 1
    print("字号分布（pt: 块数）：")
    for size in sorted(distribution):
        mark = "  ← 偏小" if size < 12 else ""
        print(f"  {size:>5.1f}: {distribution[size]:>3}{mark}")
    below = sum(count for size, count in distribution.items() if size < 14)
    print(f"低于 14pt 的文本块: {below} / {len(small_items)}")


if len(sys.argv) > 1 and sys.argv[1] == "--summary":
    for target in sys.argv[2:]:
        summarize(Path(target))
else:
    for target in sys.argv[1:]:
        inspect(Path(target))
