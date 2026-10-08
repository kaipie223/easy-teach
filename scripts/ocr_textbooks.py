"""把扫描版教材 OCR 成"带页码标记的文本"，供知识库导入。

为什么需要它
    平台解析 PDF 用的是文本层（PyMuPDF）。纯图片扫描件一页都抽不出字，导入会
    "成功但 0 个块"，界面上一切正常、检索永远空手。这类书必须先 OCR。

为什么输出成文本而不是"可搜索 PDF"
    知识库只用到文本与定位。文本带 [[page N]] 标记后，引用照样显示"第 N 页"
    （解析器认这个标记，见 backend/services/knowledge.py::_split_pages），
    而且体积只有原油墨扫描件的 1/30。

用法（在仓库根目录执行）
    # 试跑一本，量出秒/页与识别质量
    .venv\\Scripts\\python.exe scripts/ocr_textbooks.py --match "算法导论" --limit 1

    # 全量（建议后台跑，日志重定向到文件）
    .venv\\Scripts\\python.exe scripts/ocr_textbooks.py --workers 8 --report data/logs/ocr_report.json

依赖（故意不写进 pyproject：这是"入库前的准备工具"，服务端运行时不需要）
    .venv\\Scripts\\python.exe -m pip install --no-deps -i https://mirrors.cloud.tencent.com/pypi/simple rapidocr-onnxruntime
    .venv\\Scripts\\python.exe -m pip install -i https://mirrors.cloud.tencent.com/pypi/simple pyclipper shapely
    --no-deps 是必须的：rapidocr 默认会再装一份 opencv-python（GUI 版），和项目里的
    opencv-python-headless 变成两份 cv2 互相覆盖；headless 已经提供它用到的全部 API。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF，项目已依赖
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "knowledge-base" / "教材PDF"
DEFAULT_OUTPUT = REPO_ROOT / "knowledge-base" / "教材OCR文本"
# 一页文本层超过这个字数就认为"本来就有文字"，直接用它、不 OCR
TEXT_LAYER_TRUST_CHARS = 200
PAGES_PER_TASK = 40

_ENGINE = None
_THREADS = 2


def _init_worker(threads: int) -> None:
    """把"每个进程用几个线程"传进子进程（spawn 出来的进程各有一份全局）。"""
    global _THREADS
    _THREADS = threads


def _engine():
    """每个进程一份引擎：RapidOCR 的 ONNX 会话不能跨进程共享。

    线程数必须显式压住：ONNX 默认按物理核数开 intra-op 线程，8 个 worker 各开 16 条
    会在 22 个逻辑核上互相抢（实测 CPU 时间大量消耗在线程自旋上）。每个 worker 2 条
    × 8 进程 = 16 条，刚好铺满。
    """
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR(intra_op_num_threads=_THREADS, inter_op_num_threads=1)
    return _ENGINE


def ocr_page_range(task: tuple[str, int, int, int]) -> tuple[str, int, list[tuple[int, str]], float]:
    """OCR 一个页区间，返回 (文件, 起始页, [(页码, 文本)], 耗时)。"""
    path, start, end, dpi = task
    began = time.perf_counter()
    pages: list[tuple[int, str]] = []
    with fitz.open(path) as document:
        for index in range(start, min(end, document.page_count)):
            page = document[index]
            existing = page.get_text("text").strip()
            if len(existing) >= TEXT_LAYER_TRUST_CHARS:
                # 少数扫描件夹着带文字层的页（封面、目录）；有就别浪费算力
                pages.append((index + 1, existing))
                continue
            pixmap = page.get_pixmap(dpi=dpi)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            result, _ = _engine()(image)
            if not result:
                pages.append((index + 1, ""))
                continue
            lines = [str(item[1]).strip() for item in result if len(item) > 1]
            pages.append((index + 1, "\n".join(line for line in lines if line)))
    return path, start, pages, time.perf_counter() - began


def human(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} 分 {rest} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def build_tasks(paths: list[Path], dpi: int) -> list[tuple[str, int, int, int]]:
    tasks: list[tuple[str, int, int, int]] = []
    for path in paths:
        with fitz.open(path) as document:
            total = document.page_count
        for start in range(0, total, PAGES_PER_TASK):
            tasks.append((str(path), start, start + PAGES_PER_TASK, dpi))
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="把扫描版教材 OCR 成带页码标记的文本")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--match", default=None, help="只处理文件名含该子串的书")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 本")
    parser.add_argument("--dpi", type=int, default=200, help="渲染精度，默认 200")
    parser.add_argument("--workers", type=int, default=0, help="并行进程数，默认 CPU-2（上限 8）")
    parser.add_argument(
        "--threads-per-worker",
        type=int,
        default=2,
        help="每个 worker 的 ONNX 线程数（默认 2；×workers 别超过逻辑核数）",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="已有同名输出（且大于 1 KB）就跳过，便于中断后续跑",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--only-books",
        action="store_true",
        help="只处理抽不出字的书：跳过本来就有文字层的 PDF",
    )
    args = parser.parse_args()

    paths = sorted(item for item in args.source.glob("*.pdf") if item.is_file())
    if args.match:
        paths = [item for item in paths if args.match.lower() in item.name.lower()]
    if args.only_books:
        kept = []
        for path in paths:
            with fitz.open(path) as document:
                probe = "".join(document[index].get_text("text") for index in range(min(3, document.page_count)))
            if len(probe.strip()) < TEXT_LAYER_TRUST_CHARS:
                kept.append(path)
            else:
                print(f"跳过（本来就有文字层）：{path.name}", flush=True)
        paths = kept
    if args.skip_existing:
        kept = []
        for path in paths:
            existing = args.output / f"{path.stem}.txt"
            if existing.exists() and existing.stat().st_size > 1024:
                print(f"跳过（已有输出）：{existing.name}", flush=True)
            else:
                kept.append(path)
        paths = kept
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit("没有匹配到 PDF")

    workers = args.workers or min(8, max(1, (mp.cpu_count() or 4) - 2))
    args.output.mkdir(parents=True, exist_ok=True)

    total_pages = 0
    for path in paths:
        with fitz.open(path) as document:
            total_pages += document.page_count
    print(
        f"OCR：{len(paths)} 本 / {total_pages} 页，{args.dpi} DPI，{workers} 进程，"
        f"输出到 {args.output}",
        flush=True,
    )
    print(f"源目录：{args.source}", flush=True)
    print("", flush=True)

    tasks = build_tasks(paths, args.dpi)
    gathered: dict[str, dict[int, str]] = {str(path): {} for path in paths}
    began = time.perf_counter()
    done_pages = 0

    with mp.Pool(
        processes=workers,
        initializer=_init_worker,
        initargs=(args.threads_per_worker,),
    ) as pool:
        for path_text, start, pages, elapsed in pool.imap_unordered(ocr_page_range, tasks):
            gathered[path_text].update({number: text for number, text in pages})
            done_pages += len(pages)
            rate = done_pages / max(time.perf_counter() - began, 0.001)
            remaining = (total_pages - done_pages) / max(rate, 0.001)
            print(
                f"  {done_pages}/{total_pages} 页  {rate:.1f} 页/秒  "
                f"{Path(path_text).name[:28]} 本批 {elapsed:.1f} 秒  预计还需 {human(remaining)}",
                flush=True,
            )

    elapsed_total = time.perf_counter() - began
    print("", flush=True)
    print("─" * 88, flush=True)

    rows = []
    for path in paths:
        pages = gathered[str(path)]
        total = len(pages)
        filled = sum(1 for text in pages.values() if text.strip())
        chars = sum(len(text.strip()) for text in pages.values())
        lines = []
        for number in sorted(pages):
            text = pages[number].strip()
            if not text:
                continue
            lines.append(f"[[page {number}]]\n{text}\n")
        if not lines:
            print(f"{path.name[:40]:<42} 没有识别到任何文字，跳过输出", flush=True)
            rows.append({"file": path.name, "pages": total, "text_pages": 0, "chars": 0})
            continue
        destination = args.output / f"{path.stem}.txt"
        destination.write_text("\n".join(lines), encoding="utf-8")
        rows.append(
            {
                "file": path.name,
                "output": destination.name,
                "pages": total,
                "text_pages": filled,
                "chars": chars,
                "chars_per_page": round(chars / total, 1) if total else 0,
                "size_kb": round(destination.stat().st_size / 1024, 1),
            }
        )
        print(
            f"{path.name[:40]:<42}{total:>5} 页 有字 {filled:>5} 页"
            f"（{filled / total * 100 if total else 0:.0f}%）"
            f"{chars / 10000:>7.1f} 万字  {destination.stat().st_size / 1024:>7.0f} KB",
            flush=True,
        )

    good = [row for row in rows if row.get("text_pages")]
    print("", flush=True)
    print(
        f"完成：{len(good)}/{len(paths)} 本输出成功，共 {sum(int(row['text_pages']) for row in good)} 页有字，"
        f"耗时 {human(elapsed_total)}（{total_pages / max(elapsed_total, 0.001):.1f} 页/秒）",
        flush=True,
    )
    print("下一步：", flush=True)
    print(
        f'  .venv\\Scripts\\python.exe scripts/import_knowledge.py import '
        f'--source "{args.output}" --owner-email <你的账号>',
        flush=True,
    )
    print(
        "  .venv\\Scripts\\python.exe scripts/import_knowledge.py index --owner-email <你的账号>",
        flush=True,
    )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "dpi": args.dpi,
                    "workers": workers,
                    "total_pages": total_pages,
                    "seconds": round(elapsed_total, 1),
                    "pages_per_second": round(total_pages / max(elapsed_total, 0.001), 2),
                    "books": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"报告已写入 {args.report}", flush=True)


if __name__ == "__main__":
    main()
