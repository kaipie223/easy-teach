# -*- coding: utf-8 -*-
"""把 docs/VIDEO_PARSER_FIX_PLAN.md 拆成 docs/fix-steps/ 下的一步一文件。

每个文件都是自包含的：可以直接整份粘给执行 AI，不需要它回头查别的文档。
"""
from __future__ import annotations

import pathlib
import re

DOC = pathlib.Path("docs/VIDEO_PARSER_FIX_PLAN.md")
OUT = pathlib.Path("docs/fix-steps")

OUT.mkdir(parents=True, exist_ok=True)
_written: set[pathlib.Path] = set()

lines = DOC.read_text(encoding="utf-8").splitlines()
n = len(lines)


def emit(name: str, text: str) -> None:
    """写文件，并记下它 —— 最后据 _written 清理掉过期文件。"""
    p = OUT / name
    p.write_text(text, encoding="utf-8")
    _written.add(p)


def find(pattern: str, start: int = 0) -> int:
    rx = re.compile(pattern)
    for i in range(start, n):
        if rx.match(lines[i]):
            return i
    return -1


step_head = re.compile(r"^## (S\d+\.\d+)\b")
stage_head = re.compile(r"^# 阶段 (\d)")


def clean(block: list[str]) -> str:
    s = "\n".join(block).strip()
    s = re.sub(r"\n-{3,}\s*(\n-{3,}\s*)*$", "", s)  # 去掉结尾的分隔线
    s = re.sub(r"\n-{3,}\s*\n#\s*阶段", "\n# 阶段", s)
    return s.strip()


step_starts = [(i, m.group(1)) for i in range(n) if (m := step_head.match(lines[i]))]
stage_starts = [i for i in range(n) if stage_head.match(lines[i])]
bounds = sorted({i for i, _ in step_starts} | set(stage_starts) | {n})
stage_of = {}
cur = 1
for i in range(n):
    m = stage_head.match(lines[i])
    if m:
        cur = int(m.group(1))
    stage_of[i] = cur


def block_for(start: int) -> str:
    nxt = next(b for b in bounds if b > start)
    return clean(lines[start:nxt])


HAS_CHECK = {
    "S1.1", "S1.2", "S1.3", "S1.4", "S2.1", "S2.2", "S2.3", "S2.4", "S2.5", "S2.6",
    "S3.1", "S3.3", "S3.4", "S4.1", "S4.2", "S4.3", "S4.4", "S4.5", "S4.6",
    "S5.1", "S5.2", "S5.3", "S5.4", "S5.5", "S5.6",
}

REPORT = """
---

## 做完这一步后（**不要**继续下一步）

把上面「验证」里的两条命令跑完，按这个格式汇报，然后**停下等委托人确认**：

```
步骤：{SID}
改动文件：<file>:<行号范围>
diff 摘要：<每个文件改了什么，一句话>
针对性检查：改前 [BUG] / 改后 [OK]（贴原文）{nocheck}
回归输出：pytest -q 的通过数
是否触及边界：<新增依赖 / 数据库 schema / API 契约 / 新建文件，没有就写"无">
下一步请求确认：<下一步是什么>
```

**汇报里必须出现 `[OK]` 那一行原文。** 只写"已修复"不算数。
"""

NOCHECK = "   ← 这一步没有自动检查，请贴**手工可复现的证据**（命令 + 输出）"

index = []
order = 1

# ---------------- 00：开工前必读 ----------------
head = clean(lines[find(r"^## 0\. 执行守则"):find(r"^# 阶段 1")])
emit("00_开工前必读.md",
    "# 【第 0 步】开工前必读（先看这一份，再动任何代码）\n\n"
    "下面这几节是**贯穿全程的规则和背景**：执行守则、待拍板的三个决策点、背景事实、阶段总览。\n\n"
    "> **第一件事**：把 §0.5 决策页里三个 `☐` 填好（自己判断，或交给委托人拍板）。\n"
    "> 没填的话，执行到 S2.6 / S3.1 / S5.6 会停下来问，浪费一轮。\n\n"
    "---\n\n" + head + "\n",
)
index.append((order, "—", "—", "开工前必读（守则 / 决策页 / 背景事实）", "00_开工前必读.md"))
order += 1

# ---------------- 阶段 1-5：一步一文件 ----------------
for start, sid in step_starts:
    body = block_for(start)
    stage = stage_of[start]
    footer = REPORT.format(
        SID=sid,
        nocheck="" if sid in HAS_CHECK else "\n" + NOCHECK,
    )
    text = (
        f"# 第 {order} 步 / {sid}（阶段 {stage}）\n\n"
        f"> **只做这一步**，不要顺手改别的文件。\n\n"
        + body + "\n" + footer
    )
    fname = f"{order:02d}_{sid}.md"
    emit(fname, text)
    index.append((order, sid, str(stage), lines[start].lstrip("# ").strip(), fname))
    order += 1

# ---------------- 阶段 6 ----------------
s6 = find(r"^# 阶段 6")
s6_end = find(r"^## 附：明确排查过")
body6 = clean(lines[s6:s6_end])
emit(f"{order:02d}_阶段6_低危收尾.md",
    f"# 第 {order} 步 / 阶段 6（低危收尾，17 条）\n\n"
    "> 这 17 条属于**同一个阶段**，按表格逐条做，可以合并成较少的几次提交。\n"
    "> 每次改完仍要跑 harness + pytest，并在汇报里**逐条**给出证据。\n\n"
    + body6 + "\n" + REPORT.format(SID="阶段 6（逐条列出）", nocheck=""),
)
index.append((order, "阶段 6", "6", "低危收尾（17 条）", f"{order:02d}_阶段6_低危收尾.md"))
order += 1

# ---------------- 验收 ----------------
acc = find(r"^## 附：完成后的验收清单")
emit(f"{order:02d}_全部做完后的验收清单.md",
    "# 最后一步：全部做完后的验收\n\n"
    "每个步骤都拿到 `[OK]` 之后，跑这一遍总验收，把原文贴进最终汇报。\n\n"
    + clean(lines[acc:]) + "\n",
)
index.append((order, "验收", "—", "全部做完后的验收清单", f"{order:02d}_全部做完后的验收清单.md"))

# ---------------- 索引 ----------------
rows = "\n".join(
    f"| {o} | `{sid}` | {st} | {title} | `{fn}` |" for o, sid, st, title, fn in index
)
emit("README.md",
    "# 粘贴顺序\n\n"
    "一次只粘**一个文件**给执行 AI。粘完等它汇报、确认无误，再粘下一个。\n\n"
    f"| 顺序 | 步骤 | 阶段 | 标题 | 文件 |\n|---|---|---|---|---|\n{rows}\n\n"
    "每个文件都是自包含的：包含该步的全部上下文、要改成什么样、不要做什么、验证命令、汇报格式。\n\n"
    "`00_开工前必读.md` 里 **§0.5 决策页的三个 `☐` 必须先填**，否则执行到 S2.6 / S3.1 / S5.6 会停下来问。\n",
)

for stale in sorted(OUT.iterdir()):
    if stale.is_file() and stale not in _written:
        try:
            stale.unlink()
            print(f"  (删掉过期文件 {stale.name})")
        except PermissionError:
            print(f"  (跳过被占用的过期文件 {stale.name})")

print(f"OK  wrote {len(_written)} files")
for o, sid, st, title, fn in index:
    print(f"  {o:02d}  {sid:7s} {title}")
