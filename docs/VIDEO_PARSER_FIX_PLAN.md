# video_parser 分阶段修复指令（交给执行 AI）

来源：2026-09-22 对 `video_parser/` 的逐层审查（分 5 层、4 个并行代理出报告 + 主审复核），
共 15 条主要问题 + 若干低危项。本文件把它们拆成**可逐步执行**的指令。

审查时的代码锚点行号保留在文中，但**每一步都先跑"先定位"命令**——行号可能漂移，以定位结果为准。

---

## 0. 执行守则（每一步都必须遵守）

1. **一次只做一步**。做完一步就停下，按下面的"报告格式"汇报，等确认后再做下一步。不要连续做两步。
2. 每步开始前先跑该步的 **先定位** 命令，确认锚点还在。
3. **每步结束必须跑两个检查，缺一不可**：

   ```bash
   # (a) 本步的针对性检查 —— 这一个才有诊断力
   ./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py <本步编号，如 s2.4>
   # (b) 全量回归
   ./.venv/Scripts/python.exe -m pytest -q
   ```

   - **(a) 是判决性证据**：动手前它打印 `[BUG]`（证明这个 bug 真实存在），
     改完后必须变成 `[OK]`。**仍然是 [BUG] 就是没改对，即使 pytest 全绿。**
     想看全部 33 项一起跑就省略编号；`--list` 可以列出全部编号。
   - **(b) 的基线是 `182 passed, 2 warnings`（2026-09-22 实测）**。
     低于 182 通过就是改错了，必须修回来，不允许继续下一步。
   - **⚠️ 为什么必须有 (a)**：`video_parser/` 目前**几乎没有测试覆盖**
     （全仓只有 `tests/test_m3_materials.py`、`tests/test_m6_queue.py` 提到它，
     且都不覆盖本计划涉及的任何代码路径）。**pytest 全绿不能证明 video_parser 没坏。**
4. **严禁**通过修改测试断言让测试变绿。测试失败说明实现错了。
5. 每步单独一次 commit，message 用 `fix(video_parser): <一句话>`；不要攒成一个大 commit。
6. 遇到下面这些情况**停下来问人**，不要自作主张：
   - 需要**新增第三方依赖**
   - 需要**改数据库 schema**
   - 需要**改 API 契约**（前端正在调用）
   - 需要**新增文件/新功能**（例如补一个渲染脚本）
   - 审查结论与当前代码对不上（说明锚点漂移或已被修过）

   > 例外：`scripts/verify_video_parser_fixes.py` **已经存在**（随本计划一起提供），
   > 不需要你再新建，也不要改动它的断言去迁就实现 —— 它是验收标准的一部分。
   > 若某一步的检查打印 `[ERR]`（探针自身报错），说明探针失效或代码结构变了，
   > **停下来报告**，不要自己改探针。

### 全局硬约束

- 不新增第三方依赖
- 不改数据库 schema
- 不改 API 契约
- 不做纯风格重构
- 不重构 `backend/services/generator.py`（在飞）

### 报告格式（每步结束时）

```
步骤：S<阶段>.<序号>
改动文件：<file>:<行号范围>
diff 摘要：<每个文件改了什么，一句话>
针对性检查：改前 [BUG] / 改后 [OK]（贴 harness 该步的输出原文）
回归输出：pytest -q 的通过数
是否触及边界：<是否碰到依赖/schema/契约/新文件，没有就写"无">
下一步请求确认：<下一歩是什么>
```

---

## 0.5 决策页（执行前请由人填写，执行方不要自己选）

以下三处**必须由人拍板**，否则执行方会中途停下来问。把结论填在方框里，执行方照办。

### 决策 1 → 对应 S3.1：artifact-tool 这条 PPTX 渲染路线怎么办

**背景**：这条路**从未存在过** —— `scripts/render_pptx_artifact.mjs` 全仓没有、所有分支的 git 历史
里也从未新增过任何 `.mjs`；路径算错一层（`parents[2]` 而不是 `parents[1]`）；默认 node/setup 指向
**另一个开发者的机器**（`C:\Users\jjjj\.codex\plugins\...`）。它现在恒定抛错、被静默吞掉后回落 legacy。
**所以：你现在拿到的 PPTX 一直就是 legacy 渲染的，`pptx_previews/` 从未产生过一张图。**

| 选项 | 做什么 | 代价 | 收益 |
|---|---|---|---|
| **A. 补脚本** | 修 `parents[2]`→`parents[1]`，并把 `.mjs` 脚本写/移植进 `scripts/`，再文档化两个环境变量 | **是新增功能**：要引入一个**依赖外部 Codex runtime 的组件**（那个 runtime 属于别人机器），还要解决 node 依赖与部署 | 可编辑 PPTX + 页面预览图 |
| **B. 删死路**（推荐） | 删掉 `_render_pptx_with_artifact_tool` 整条路线、`C:\Users\jjjj\...` 硬编码、两个无说明的环境变量、以及 `:95-106` 那段恒假的预览收集；同时放宽回落条件 | 放弃一个**本来就没有的**能力 | 消除他人机器路径；渲染行为可预期；不再有死代码 |

**推荐 B**：`pptx_previews/` 从未产生过，删掉**不损失任何实际产出**；而 A 要引入一个依赖外部
runtime 的新组件，成本和不确定性都高。**如果确实需要可编辑 PPTX，那是一个独立立项的需求，不该混在这次修复里。**

> 我的选择：☐ A 补脚本　☐ B 删死路（推荐）

### 决策 2 → 对应 S2.6：`--no-answer-key` 与学生版交互页的冲突

**背景**：交互页的判题是**客户端 JS**（`rendering.py:732` 拿 `question.correct_answer` 比对），
**答案必须在页面里** —— 只要生成交互页，学生看源码就能拿到答案。所以"学生版"与"交互式 HTML"本质冲突。
**触发条件**：只在 IR 里**存在 answer 块**时才发生，而 answer 块由 vision 模型产生、
`video_parser_vision` 默认 `False` → **当前默认配置下不会命中**（打开 vision 后才会）。

| 选项 | 做什么 | 代价 |
|---|---|---|
| **A. 不生成交互页**（推荐） | `include_answer_key=False` 时不产出交互式 HTML（或降级为不含题目的静态页） | 学生版没有交互页。但教师用这个开关的**意图**就是"给学生看"，给一个必然漏答案的交互页反而是骗人 |
| **B. 生成但不判题** | 去掉 `correct_answer`，学生自评 | 失去自动判题 |
| **C. 保持现状 + 明确标注** | 不改行为，在产物说明/manifest 里标"C 此 HTML 含答案，勿直接发给学生" | 零改动，但教师仍可能误发 |

**推荐 A**（也可 A+C 组合）。

> 我的选择：☐ A 不生成交互页（推荐）　☐ B 生成但不判题　☐ C 保持现状+标注

### 决策 3 → 对应 S5.6：视频体积上限

**背景**：base64 分支比对的是**原始文件字节**（漏了 4/3 编码系数）；
而 **file_url（`:537-546`）与 https_url（`:547-551`）两个分支完全没有体积检查** ——
默认 `input_mode="auto"` 走的正是 file_url，**默认配置下本地根本没有任何上限**。
官方限额数值（本地文件 100MB / base64 编码后 10MB / 公网 URL 2GB）来自审查时的文档摘要，
**未逐字核实**（WebFetch 被域名安全策略拦截）。

| 选项 | 做什么 |
|---|---|
| **A. 本轮就做**（推荐） | 补上 file_url 分支的体积检查 + base64 按 **4/3 编码后**体积判定；限额数值抽成**具名常量**并注明"来源待核"，核实后一处改 |
| **B. 跳过** | 本轮不动，等限额确认后单独做 |

**推荐 A**：补检查这个动作不依赖具体数值，而"默认配置下完全不检查"是实打实的风险。
**把数值抽成常量**，以后核实了只改一处。

> 我的选择：☐ A 本轮做（推荐）　☐ B 跳过

---

## 1. 背景事实（执行前必读）

- **视频能力当前默认关闭**：`backend/config.py:99` `video_parser_enabled: bool = False`，
  `backend/routers/materials.py:146` 在关闭时直接返回 `VIDEO_CAPABILITY_DISABLED`。
  所以本计划里的问题**当前不影响生产**，它们是**打开视频能力之前必须清掉的前置阻断项**。
  但 `python -m video_parser ...` 这个 CLI 可以绕过开关直接跑，所以问题本身是真实的。
- `backend/config.py:100-104` 的默认值：`video_parser_type="auto"`、`transcribe=False`、
  `ocr=False`、`vision=False`、`understanding=False`。全部为 False。
- **本机没有 ffmpeg/ffprobe**（不在 PATH），**本机 `backend/.venv` 没有 opencc**（仓库根 `.venv` 有）。
  涉及这两项的验证要用相应环境。
- **`video_parser/` 几乎没有测试覆盖**（2026-09-22 实测）：全仓只有 `tests/test_m3_materials.py`
  与 `tests/test_m6_queue.py` 提到 `video_parser`，且只覆盖"backend 如何调用它"，
  **本计划涉及的每一条代码路径都不在其中**。所以：
  - `pytest -q` 的 182 项**与 video_parser 的正确性无关**，它只能挡住你改坏 backend 的连带影响；
  - 每一步的判决性证据是 `scripts/verify_video_parser_fixes.py` 里对应的那条检查；
  - **当前进度（2026-09-23 实测：`33 项：7 通过，26 未通过`）**：阶段 1 与
    S2.1 / S2.2 / S2.3 已完成（对应 `git log --oneline -- video_parser/` 里的
    `fix(video_parser): ... (Sx.y)` 提交），**从 S2.4 接着往下做**。
  - 某条检查一开始就是 `[OK]` 时，**先 `git log -S` / `git log --oneline -- video_parser/`
    查它是不是已被提交修过**：
    - 有对应 `(Sx.y)` 提交 → 那一步已完成，记一句"已由 <commit> 完成"，跳去下一步，**不要重做**；
    - 没有任何提交动过它 → 锚点漂移或探针失效，**停下来报告**，不要自己改探针。

### 阶段总览

| 阶段 | 目标 | 步骤数 | 为什么这个顺序 |
|---|---|---|---|
| **1** | 让解析能真正跑通（缺模型/缺依赖/缺 ffmpeg 时降级而不是崩） | 4 | 不能跑通就没法验证后面任何修复 |
| **2** | 清掉伪造与无关内容（交付物污染） | 6 | 最严重的：假内容直接进交付物，无需条件即触发 |
| **3** | 渲染器与产物完整性 | 4 | 决定产物可不可信、失败是否可见 |
| **4** | 分片串号与对齐正确性 | 6 | 最重的逻辑修复，需要阶段 1 的可用解析来验证 |
| **5** | 静默失真与成本 | 6 | 不影响正确性但影响信任与账单 |
| **6** | 低危收尾 | 17 | 影响面小，可批量处理 |

### 核实状态（2026-09-22 二次核实）

审查结论已用 4 个只读代理逐条复核过一轮，**全部 CONFIRMED，没有一项虚假或已被改掉**。
复核同时修正了若干锚点、补出 11 处写死时间戳中的 4 处、订正了 2 处描述错误。
仍属**单次核实**（本轮未二次复核）的只有 3 条，执行到那几步时请自行先复核：

- `S4.6`（`ir_builder.py:935` block id 冲突）
- `S6.7`（`ir_builder.py:268` 死条件）
- `S6.9`（`cli.py:81,228` 的 `--style` / `outputs` / `language` 无效）

---

# 阶段 1：让解析能真正跑通（阻断项）

**阶段目标**：在缺 whisper 模型、缺 opencc、缺 ffmpeg 的环境下，`parse_video` 能**降级并产出带 warning 的部分结果**，而不是抛异常丢掉全部产出。

---

## S1.1 whisper 模型加载失败 → 整个解析任务崩溃

**锚点**：`video_parser/transcription.py:16-22`（`load_whisper_model`，`WhisperModel(...)` 在 `:22`）、
`:34-36`（`model = load_whisper_model(...)` 在 `try` **之外**，`try` 从 `:36` 开始）、
`video_parser/parser.py:157-170`（唯一调用点，`except (FFmpegError, TranscriptionError)` 在 `:168`）

**先定位**：
```bash
grep -n "def load_whisper_model" -A 10 video_parser/transcription.py
grep -n "def transcribe_audio" -A 30 video_parser/transcription.py | head -40
grep -n "transcribe_audio\|except (" -A 3 video_parser/parser.py
```

**现状**：`load_whisper_model` 只把 `ImportError` 包成 `TranscriptionError`；而 `WhisperModel(...)`
的模型下载/加载异常（`LocalEntryNotFoundError` / `OSError`）原样抛出。这个调用点又在
`transcribe_audio` 的 `try` **之外**，parser 侧只捕获 `(FFmpegError, TranscriptionError)` →
异常穿透，整个 `parse_video` 失败，产出全丢。
**关键放大因素**：`parse_video`（`parser.py:45-398`）**全程没有外层 try**，
所以这类异常会一路穿透到 `backend/services/materials.py:380` 的大 `except Exception`（标 failed），
表现为"整个材料分析失败"，而不是降级为 warning。

**要改成**：
1. 让模型加载失败也被包成 `TranscriptionError`（加宽 `load_whisper_model` 里的 `except`），
   或把调用点移进 `try`。二者选一即可，**不要两处都改**。
2. 让 transcribe 失败**降级为 warning**而不是整任务失败：`parser` 侧捕获后，
   把该条 warning 追加进结果 warnings，`Transcript.status` 用**已有的值**（`"failed"` 已存在），
   其余阶段（关键帧、OCR、视觉）继续跑。

**不要**：不要新增 `Transcript.status` 的取值（`ir_builder` 等处按已有集合判断）、
不要改 `Transcript` 模型的字段、不要吞掉异常而不记 warning。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s1.1
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：模型缺失时 `parse_video` 返回结果（含 warning），不抛异常；测试数 ≥ 182。

---

## S1.2 缺 opencc → 整个 ASR 结果被丢弃

**锚点**：`video_parser/text_normalization.py:6-11`（`_converter()`，`@lru_cache`，缺 opencc 抛 `RuntimeError`）、
`:15-19`（`to_simplified_chinese`，无 try/except 无 fallback）、
`video_parser/transcription.py:36-59`（`model.transcribe` + 循环 + `to_simplified_chinese` **全在同一个 try 内**，
`:59-61` 统一 `raise TranscriptionError`，局部分段列表被丢弃、`:68-85` 的落盘全部跳过）

**先定位**：
```bash
grep -n "def to_simplified_chinese" -A 10 video_parser/text_normalization.py
grep -n "simplified_chinese\|text_normalization" video_parser/transcription.py
grep -n "text_normalization" -A 3 video_parser/schemas.py | head -20
```

**现状**：`to_simplified_chinese` 缺 opencc 时抛 `RuntimeError` 无兜底；`transcribe_audio` 里
整个分段循环被一个 `try` 包住，第一个非空分段就抛错 → 已生成的全部分段被丢弃（置 `status="failed"`），
几分钟的 Whisper 计算白费，**且没有 partial 结果落盘路径**。本机 `backend/.venv` 实测
`import opencc` → ModuleNotFoundError。
两个补充事实：
- `@lru_cache` **只在成功时缓存**，失败会**重复抛**（每次调用都重新尝试 import）。
- 两个调用点的行为不同：`transcribe_audio:48` 抛出的 `RuntimeError` 会被 `:60` 的
  `except Exception` 吞成 `TranscriptionError`（即被 S1.1 那条链放大）；
  而 `transcript_from_manual_text:93` 调用时**无任何捕获**，会裸穿到调用方。

**要改成**：
1. `to_simplified_chinese` 在 opencc 不可用时**返回原文**（不清转简），不抛异常。
2. 调用方据此把 `Transcript.text_normalization` 写成 **已存在的字面量 `"none"`**
   （`schemas.py` 的 Literal 里已经有 `"none"`，但两个生产者恒写 `"simplified_chinese"`，从无读取方——
   正好用上，**不要新增字面量**）。
3. ASR 分段循环改为**逐段收集**：单段失败只跳过该段并记 warning，已成功的段保留。

**不要**：不要给 opencc 做 `pip install` 兜底、不要新增依赖、不要改 `Transcript` 字段。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s1.2
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：缺 opencc 时得到未简化的转写文本 + warning，其余分段保留；测试数 ≥ 182。

---

## S1.3 缺 ffmpeg/ffprobe 没有任何预检

**锚点**：`backend/services/materials.py:607-619`（`_parse_video`，`:618` 同步调用 `parse_video`），
调用链 `_parse_video` ← `parse_material`（`:247`）← `:371`，外层只有 `:380` 的大 `except Exception`
与 `backend/routers/materials.py:146`（已有的开关与错误返回写法）

**先定位**：
```bash
grep -n "parse_video\|video_parser_enabled" backend/services/materials.py backend/routers/materials.py
grep -n "ffmpeg\|ffprobe" -r backend/ --include=*.py    # 预期：无任何可用性检查
```

**现状**：`ffmpeg.py` 的 `_run_command` 会把 `FileNotFoundError` 转成 `FFmpegError`，
错误本身还算干净，但**没有任何预检**：用户能上传、能排队、直到解析阶段才失败。
核实要点：
- `backend/` 下（glob `*.py`）grep `ffmpeg|ffprobe|FFmpegError` **零匹配** —— backend 只通过
  `from video_parser.parser import parse_video`（`materials.py:33`）间接依赖外部二进制。
- 唯一的"守卫"是 upload 路由的配置开关（`routers/materials.py:146`），**属配置开关而非二进制探测**。
- 全仓含 "ffmpeg" 的文件只有 `video_parser/*.py`、`Dockerfile.backend:17`
  （`apt-get install ... ffmpeg`）和 `docs/DEPLOYMENT_M6.md`。即**非 Docker 部署下**，
  ffmpeg 缺失时错误在 `probe_video`（`parser.py:67`）以 `FFmpegError` 抛出，
  直接冒泡到 `materials.py:380` 使整个分析失败，没有任何"未安装 ffmpeg"的友好提示。

**要改成**：在视频解析**入口**（排队/落盘之前）做 `shutil.which("ffmpeg")` 与
`shutil.which("ffprobe")` 检查，缺失时**立即**返回明确错误。
复用 `backend/routers/materials.py:146-150` 那一段已有的错误返回写法，**只换 code 与 message**。

**不要**：不要改响应结构、不要新增前端未处理的错误 code（先看前端怎么消费 `code`，
拿不准就停下问人）、不要试图自动下载 ffmpeg。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s1.3
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
外加一次手工验证：在不含 ffmpeg 的 PATH 下走视频入口，应立刻拿到清晰错误，而不是排队后失败。

**完成判据**：缺 ffmpeg 时入口立即失败并有可操作的错误信息；测试数 ≥ 182。

---

## S1.4 ffmpeg/ffprobe 子进程没有 timeout

**锚点**：`video_parser/ffmpeg.py:285-299`（`_run_command`）—— 核实确认它是**所有** ffmpeg/ffprobe
调用的**唯一出口**，所以改这一处即可覆盖 `extract_audio` / `extract_frame` / `probe_video` / `extract_video_segment`

**先定位**：
```bash
grep -n "def _run_command" -A 20 video_parser/ffmpeg.py
grep -n "subprocess.run" video_parser/ffmpeg.py
```

**现状**：`subprocess.run` 未传 `timeout`。同模块对模型 HTTP 调用是有超时预算的
（`video_timeout_seconds`、`BailianVideoConfig.timeout_seconds`），ffmpeg 侧一个都没有，
外层也没有（`backend/services/materials.py` 同步调用 `parse_video`）。
视频在网络盘掉线、上传中的文件、畸形文件时会让 worker 被永久占住且无取消路径。
（注：**管道写满死锁不存在**——`capture_output=True` 下 `subprocess.run` 用 `communicate()` 并发读管道。
这条只加超时，不要动管道处理。）

**要改成**：给 `subprocess.run` 加 `timeout=`，超时转成 `FFmpegError`（沿用现有异常转换风格）。
超时值用**模块常量或配置项**，不要散落魔数。refinement 逐帧调用（最多 120 帧 × 24 区间）也要走同一路径。

**不要**：不要引入线程池/信号等复杂取消机制；`FileNotFoundError`/`CalledProcessError` 的现有转换要保留。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s1.4
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：超时抛 `FFmpegError`；测试数 ≥ 182。

---

# 阶段 2：清掉伪造与无关内容

**阶段目标**：交付物里**不再出现**与输入课程无关或凭空编造的教学内容。
这一阶段优先级最高的原因是：**S2.1 无需任何条件，每一份交付都命中**。

---

## S2.1 【最高】markdown 总结无条件写入硬编码电路课内容

**锚点**：`video_parser/rendering.py:758-825`（`_render_markdown_summary` 函数体全文，**共三处**硬编码：
`:773-779` 一句话结论、`:795-815` 判断方法/典型现象/易错点、`:819-821` 规范化表述标记）、
`:60-61`（唯一调用点）

**先定位**：
```bash
grep -n "_render_markdown_summary" video_parser/rendering.py
grep -n "通路\|断路\|短路\|LED\|正负极\|电源" video_parser/rendering.py
```

**现状**：`_render_markdown_summary` 的"一句话结论 / 判断方法 / 典型现象与安全提醒 / 易错点 /
证据处理说明"整段是**电路课**（通路/断路/短路/LED/正负极）的固定文本，**函数体内没有任何条件分支**
（函数是直线代码：`767-779` / `795-824` 是纯字面量列表；动态数据只有 `plan.learning_objectives`(780)、
`package.ir.teaching_units`(782-793)、`len()` 计数(819-820)。唯一近似"分支"的 `791` 行标签字典
只影响单条 block 前缀，不影响硬编码段落）
（函数体从前言直接进入硬编码结论）。任何课程都会原样写进 `content_summary.md`，
而该文件在 `artifact_manifest.json` 里是**正式交付产物**（`artifact_type=other`）。
实测：三角面积课的产物里逐字出现"判断电路先确认是否存在从电源正极回到负极的完整路径""LED 有方向：长脚接正极"。

**要改成**：删掉硬编码的电路段落，改为**由真实数据生成**（plan / IR / topic / 学习目标）。
信息不足时**整段省略**，而不是编内容。

**不要**：不要只是把"电路"替换成别的硬编码词；不要用占位符（如"待补充"）顶替；
不要动其它渲染分支。**如果真实数据不足以生成某个小节，就删掉那个小节的输出。**

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.1
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：非电路课产物里不含任何电路内容；标题/学习目标仍来自真实输入；测试数 ≥ 182。

---

## S2.2 电路兜底模板写入伪造的证据时间戳

**锚点**：`video_parser/ir_builder.py` 的 **7 处**写死绝对时间戳（全部在 `_build_circuit_ir` 内）：
`:601`（03:23–04:05）、`:611`（04:25）、`:613`（04:33–05:55）、`:625`（08:00–09:55）、
`:637`（10:00–13:45）、`:649`（14:06–15:30）、`:672`（05:55–07:30）

**先定位**：
```bash
grep -n "03:23\|0:0\|timecode\|真实\|讲" video_parser/ir_builder.py | sed -n '1,40p'
sed -n '590,660p' video_parser/ir_builder.py
```

**现状**：电路兜底模板里写死的绝对时间戳（**7 处，不是 3 处**），配合可核验的呈现格式，
等于**编造"视频 03:23 讲了 X"**。这些时间戳会作为"证据时间范围"写进
`ContentBlock.metadata["correction_reason"]` 与 `time_range`，与实际视频无关时依然输出。
（触发有门：`_is_circuit_lesson` 要求**文件名 stem + ASR 全文去空白后同时含"通路""断路""短路"**三个词。
但注意：`backend/config.py:104 video_parser_understanding=False` 默认关闭 →
`parser.py:310` 令 `parsed.video_understanding=None` → `ir_builder.py:320` 的短路条件不成立 →
**默认配置下只要那三个词出现就会走兜底模板**，且这条路径**无任何测试覆盖**）

**要改成**：时间戳要么来自**真实证据**（ASR/关键帧/OCR 的实际时间），要么留空并标记为待确认，
不能生成貌似可核验的假时间戳。

**不要**：不要把假时间戳换成一个"看起来更合理"的假值；不要删除"待确认"这一类显式标记机制
（显式标记是可以的，伪造不可以）。

**验证**：走电路兜底路径，检查每个单元的时间戳都能追溯到某条 evidence；没有证据的单元必须显式标记。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.2
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：兜底产物中不存在无法追溯的绝对时间戳；测试数 ≥ 182。

---

## S2.3 电路兜底把 quality 写死，真实失败信号全丢

**锚点**：`video_parser/ir_builder.py:785-793`，对照主路径 `:273`（正确写法）

**先定位**：
```bash
sed -n '265,280p' video_parser/ir_builder.py
sed -n '780,795p' video_parser/ir_builder.py
```

**现状（两条独立缺陷）**：
1. warnings 是两条固定文案，**完全丢弃 `parsed.warnings`**。实测：喂入
   `["parser warning: OCR failed", "parser warning: no audio"]` → 输出只剩两条硬编码文案。
   即 OCR/vision/ASR 全失败时，审核看不到任何真实失败信号。
2. `:793` 的 `status="warning" if result_conflicts else "warning"` —— **三元两支相同**，
   状态恒为 `"warning"`，正常兜底与真异常不可区分。

**要改成**：
1. 对齐主路径 `:273` 的写法：`warnings = list(parsed.warnings)` 再追加说明性文案。
2. 修掉三元表达式，让无冲突的正常兜底与有冲突的情况可区分（用**已有的** status 取值）。

**不要**：不要新增 status 取值。

**验证**：喂入含真实 warning 的 `parsed` → 输出的 `quality.warnings` 里能看到那些真实 warning。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.3
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：真实 warning 不再丢失；status 能区分正常与冲突；测试数 ≥ 182。

---

## S2.4 电路兜底时间戳被硬钳 → 大量单元退化成零长度区间

**锚点**：`video_parser/ir_builder.py:577`（魔数 `1101.067`）、`:682-684`（`min(..., duration)` 钳位）

**先定位**：
```bash
grep -n "1101.067" video_parser/ir_builder.py        # 预期全文件仅一处
sed -n '573,580p' video_parser/ir_builder.py
sed -n '678,688p' video_parser/ir_builder.py
```

**现状**：`duration = float(parsed.metadata.duration_seconds or 1101.067)`——时长探测失败返回 `0.0`
时会当成 1101.067 秒，时间戳指到视频之外且无 warning。再配合 `min(..., duration)`，
60 秒视频走电路兜底实测 **8 个单元里 7 个变成 `[60.0, 60.0]`**，全部指向视频末尾同一瞬间。
`PackageTimeRange` 只禁止 `end < start`、**不禁止零长度**，所以静默通过。

**要改成**：
1. 去掉 `1101.067` 魔数。时长缺失时**不要瞎猜**——要么不产出时间轴，要么把时间轴标为不可用并记 warning。
2. 短片不要再压成零长度：按比例缩放到 `[0, duration]`，或明确拒绝走兜底。

**不要**：不要引入新的"合理默认时长"；不要靠改 `PackageTimeRange` 来放行（那是阶段 4 的事，且方向相反）。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.4
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：60 秒电路课不再产出零长度单元；时长缺失时有 warning；测试数 ≥ 182。

---

## S2.5 单元标题被替换成「补充说明（待复核）」

**锚点**：`video_parser/planning.py:559-576`（`_presentation_title`），消费点 `:55`（stage.title）、
`:65`（key_points）、`:246`（教案 section.title / 教师活动）、`:254`（difficulties）

**先定位**：
```bash
grep -n "_presentation_title" video_parser/planning.py
sed -n '555,580p' video_parser/planning.py
```

**现状**：`noisy_markers` 含「这个/然后/对吧/OK/咱们」等中文口语里**极常见**的词；
一旦 `topic` 命中就进入重命名分支，而该分支的候选名**全是电路专有词**（通路/断路/短路/LED/长脚），
不匹配时返回占位符 `"补充说明（待复核）"`。实测：
`'这个红色的圆形'`、`'然后我们看三角形的面积'`、`'对吧，锐角三角形的定义'` → 全部变成
`"补充说明（待复核）"`；而 `'认识了红色和黄色'` 正常。一个真实说法里带个"这个"就中招。

**要改成**：去掉"命中常见口语词就进电路重命名分支"的耦合。兜底**不要用占位符标题**，
应回退到 `topic` 原文或该单元的首个知识点。

**不要**：不要把 `noisy_markers` 换成另一组硬编码词；不要保留电路候选名作为通用兜底。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.5
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：上述三个输入不再产生占位符标题；测试数 ≥ 182。

---

## S2.6 `--no-answer-key` 去不掉答案

**锚点**：`video_parser/planning.py:146-149`（内容页元素取自 `display_blocks`，不过滤 answer 块）、
`:199`（该开关只控制是否**额外**加一页"参考答案"）、`:274-332`（`build_interactive_spec` 从不读该开关）、
`video_parser/rendering.py:684`（把含 `correct_answer` 的 InteractiveSpec 原样嵌进 `index.html`）

**先定位**：
```bash
grep -n "include_answer_key" -r video_parser/
grep -n "correct_answer" video_parser/rendering.py
grep -n "display_blocks\|_is_presentable_block\|_presentation_priority" video_parser/planning.py | head
```

**现状**：`answer` 块被 `_is_presentable_block` / `_presentation_priority`（问题/答案类优先级 90）
选入展示块，于是答案同时出现在**普通内容页**上；开关只影响 `slide_00X_..._answer` 那一页。
实测：`include_answer_key=False` 时仍输出 `answer text on slide slide_003_... type=answer visibility=student`，
且 `interactive_html/index.html` 里仍有 `"correct_answer":"不工作"` —— 学生查看源码即可看到答案。

**要改成**：
1. `include_answer_key=False` 时，`answer` 块**不进入**内容页的展示块选择。
   （相关判据：`_is_presentable_block`（`:504-505`）与 `_presentation_priority`（`:517-518`）
   对 `answer` 类型只做字符数限制、不做答案屏蔽，并给 **90 分**优先级，仅次于 verified 的 100。
   另外 `:429-438` 的 `_stage_content` 会把 answer 块带进教案 `teacher_activity`。）
2. `build_interactive_spec` 也要读这个开关，答案不写进 `index.html` 的 SPEC。

**⚠️ 这里有一个必须由人决策的张力，不要自己选**：
交互页的判题逻辑是**客户端 JS**（`rendering.py:732` 用 `question.correct_answer` 比对），
所以答案**必须在页面里**——只要生成交互页，学生看源码就能拿到答案。
因此 `include_answer_key=False` 的"学生版"与"交互式 HTML"本质上冲突，可选方向：
- (a) `include_answer_key=False` 时**不生成**交互式 HTML（或降级为不含题目的静态页）
- (b) 生成但不带正确答案，改为无判题（学生自评）
- (c) 保持现状，但在产物说明里明确写"此 HTML 含答案，勿直接发给学生"

**执行方不要自己挑一个** —— 去看 **§0.5 决策 2**：
- 那里已经勾了 `☐` → **直接照勾选的选项做**，不用再问。
- 那里还是空框 → 停下来把上面三个选项报给委托人，等拍板后再动手。

**不要**：不要只删 `correct_answer` 字段而留下"参考答案：…"正文（那会让 HTML 的判题功能静默失效）；
不要改 `visibility` 的语义；不要用 CSS/JS 隐藏答案（源码里仍然可读，等于没修）。

**验证**：构造含 `question`/`answer` 块的 IR（`block_type=question/answer` 由 vision 模型产生，
`ir_builder._visual_block` 路径），分别用 `include_answer_key=True/False` 跑渲染：
- `False`：内容页无 answer 元素、`index.html` 里 grep 不到 `correct_answer`
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.6
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：`False` 时答案在内容页与 HTML 里都不可见；测试数 ≥ 182。

---

# 阶段 3：渲染器与产物完整性

**阶段目标**：让产物"要么完整可见、要么明确失败"，并且渲染路径的行为可预期。

---

## S3.1 【决策点】artifact-tool 渲染路线恒不可用 + 指向他人机器的硬编码路径

**锚点**：`video_parser/rendering.py:150`（`parents[2]`）、`:151-160`（`C:\Users\jjjj\...` 硬编码）、
`:142-145`（靠字符串匹配决定回落）、`:95-106`（`pptx_previews/` 收集逻辑）

**先定位**：
```bash
sed -n '140,165p' video_parser/rendering.py
./.venv/Scripts/python.exe -c "
from pathlib import Path
p=Path('video_parser/rendering.py').resolve()
print([str(x) for x in p.parents[:4]])     # 确认哪一层才是仓库根
"
find . -name "render_pptx_artifact.mjs" -not -path "./node_modules/*"
git log --all --oneline -- '**/render_pptx_artifact.mjs'
grep -rn "VIDEO_PARSER_NODE\|VIDEO_PARSER_ARTIFACT_SETUP" . --exclude-dir=node_modules --exclude-dir=.venv
```

**现状**：`__file__` 是 `<repo>/video_parser/rendering.py`，`parents[1]` 才是仓库根，
`parents[2]` 指向仓库外；且 `scripts/render_pptx_artifact.mjs` **全仓不存在，`git log --all` 也没有**；
默认 node/setup 路径属于另一个开发者（`C:\Users\jjjj\...`）；两个环境变量全仓无任何说明。
结果：这条路**恒定抛错**，被 `:142-145` 吞掉后静默回落 `_render_pptx_legacy`，
`pptx_previews/` 永不产生。另外只有 "runtime is not available" 这一种错误会回落——
一旦 artifact-tool 真可用但 JS 侧失败，`RenderingError` 会直接冒泡终止整次 generate，
而仓库里明明有可用的 legacy 渲染器。

核实补充（这几条决定了选项 A 的工作量）：
- `parents[1]` = 仓库根，`parents[1]/scripts` **确实存在**（含 `backup.sh`、`evaluate_ai_quality.py`、
  `restore.sh`、`verify_ai_smoke.py`、`verify_release_flow.py`）；而 `parents[2]` =
  `C:\Users\kaipie\Desktop`，且 **`C:\Users\kaipie\Desktop\scripts\` 整个目录都不存在**。
  所以改成 `parents[1]` 是必须的，但**光改索引不够** —— 脚本本身不存在，仍会走"not available"分支 →
  **选项 A 必须同时补脚本**。
- 该 `.mjs` 从未存在过的四条独立证据：全盘 find 0 命中；排除 `.venv`/`node_modules` 后全仓 `*.mjs`
  **0 个**；`git log --all` 空；`git log --all --diff-filter=A` 的**所有分支历史里从未新增过任何 .mjs 文件**。
- `C:/Users/jjjj/...node.exe` 在本机不存在（本机用户是 kaipie）；`.env.example` 的 10 个
  `VIDEO_PARSER_*`（第 60-69 行）**不含**这两个变量，实际 `.env` 也没设 → 必然走那个默认值。
- 除了 `:160` 的"not available"，另外两个 `RenderingError`（`:175` workspace setup failed、
  `:190` PPTX rendering failed）都会**直接冒泡终止整次 generate**；且 `:142` 的 `except` **只捕获
  `RenderingError`**，JS 侧抛别的异常类型也不会回落。
- `pptx_previews/` 的收集（`:95-106`，按后缀白名单 `.png/.webp/.json` 收）与创建（`:161`）：
  因为 `_render_pptx_with_artifact_tool` 恒定失败，`:161` **永远执行不到** →
  该目录永不产生 → **`:95-106` 恒为死分支**（`.is_dir()` 恒假）。

**这一步要做什么**：**先只做事实核实**（核实动作本身不需要批准），然后看 **§0.5 决策 1**：
- 那里已经勾了 `☐` → **直接照勾选的选项做**，不用再问。
- 那里还是空框 → 停下来把下面两个选项报给委托人，等拍板后再动手，不要自己选。

- 选项 A：修 `parents[2]` → `parents[1]`，并把 `.mjs` 脚本补进仓库 + 文档化两个环境变量。
  （**属于新增文件/新功能 → 必须人类批准**）
- 选项 B：承认这条路不存在，**删掉死路和指向他人机器的硬编码路径**，只留 legacy。
  （同时把回落条件放宽：artifact-tool 任何失败都应回落 legacy，而不是终止整次 generate）

**不要**：不要自己决定补脚本还是删代码（看 §0.5 决策 1 的勾选）；不要在没被批准的情况下新增文件。

**验证**：无论选哪个，都要保证：`grep -rn "jjjj" video_parser/` 为空；
渲染路线的行为可预期（要么真的可用，要么不存在死代码路径）。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s3.1
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：人类已就 A/B 做出决策，且代码与该决策一致；测试数 ≥ 182。

---

## S3.2 失败中止留下半个产物目录；复用输出目录时 manifest 哈希与磁盘不符

**锚点**：`video_parser/rendering.py:44-52`（先写 4 个 spec + quality_report 才做质量门禁）、
`:54-64`（逐个写产物，无暂存/清理）、`:126`（manifest 最后写）

**先定位**：
```bash
sed -n '40,70p' video_parser/rendering.py
sed -n '120,130p' video_parser/rendering.py
grep -n "artifact_manifest" video_parser/rendering.py
```

**现状**：产物不是"全成功才可见"的。质量门禁 `raise` 时目录里已留下
`generation_plan.json / slide_spec.json / lesson_plan_spec.json / interactive_spec.json / quality_report.json`，
且**不会**清理上一次成功运行留下的 `artifact_manifest.json`。下次消费方按 manifest 校验哈希就会失败，
也无法区分"下载损坏"和"刚刚生成失败"。实测：`stale_out` 两次渲染后
`manifest 记录 514d845f… vs 实际 7bcd2cc8…` → MISMATCH。

**要改成**：产物做成**全成功才可见**——先写临时目录，全部成功后原子移动；
或门禁通过后才开始写文件。失败时清理本次产生的文件**以及陈旧的 manifest**。

**不要**：不要把 manifest 改成先写（那会让哈希校验更不可信）；不要删掉质量门禁本身。

**验证**：故意触发一次门禁失败（如 answer_leak 场景），检查目录里没有残留 spec，也没有陈旧 manifest。
```bash
./.venv/Scripts/python.exe -m pytest -q
```
**完成判据**：门禁失败后目录不留半个产物；成功路径产物完整；测试数 ≥ 182。

---

## S3.3 `_render_docx` 的死代码让缺依赖时报裸 ImportError

**锚点**：`video_parser/rendering.py:640-648`（`return` 后的 `try/except ImportError` 永不可达）、
`:312`（`_render_docx_compact` 顶部裸 `from docx import Document`）

**先定位**：
```bash
sed -n '305,320p' video_parser/rendering.py
sed -n '635,655p' video_parser/rendering.py
```

**现状**：`_render_docx_compact(...)` 后紧跟 `return`，其后作者的
`try: from docx import Document except ImportError: raise RenderingError("DOCX renderer requires python-docx")`
**永不可达**；而实际使用的 `_render_docx_compact` 在函数顶部裸导入。
环境缺 python-docx 时会抛裸 `ImportError: No module named 'docx'`，还会造成"PPTX 已写出、DOCX 缺失"
的半个产物。

**要改成**：二选一（**选哪个都行，保持一致即可**）：
- 删除死分支；或
- 删死分支，同时把 `_render_docx_compact` 的导入包进 try 转成 `RenderingError`。

**不要**：不要保留两套 docx 渲染实现。

**验证**：monkeypatch 掉 `docx` 模块（或临时改 sys.modules）→ 应得到 `RenderingError` 而不是裸 ImportError。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s3.3
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：缺 docx 时错误类型是 `RenderingError`；测试数 ≥ 182。

---

## S3.4 从包里重新打包会静默丢掉全部图像

**锚点**：`video_parser/package.py:183-190`（`_source_path_candidates`）、`:193-206`（`_resolve_keyframe_path`）、
`:217`（路径被改写为 `assets/keyframes/...`）

**先定位**：
```bash
sed -n '180,225p' video_parser/package.py
grep -n "assets/keyframes" video_parser/package.py
```

**现状**：包写入的 `source/video_parse_result.json` 里 keyframe 路径被改写为
`assets/keyframes/asset_xxx.png` 这种**相对路径**，在任何机器上都解析不到；
`_resolve_keyframe_path` 只试 `Path(value)`（相对 CWD）与 `artifacts.run_dir` / `source_video.path`
（后者是**视频文件**却被当目录根），**从不试输入 JSON 自身所在目录**。
实测 `python -m video_parser package <pkg>/source/video_parse_result.json --output-dir new_pkg` →
新包 `assets: []`、`manifest.status=partial`、三条 `Missing optional keyframe asset`、
keyframe 路径全变空串，**但命令仍 exit 0 "成功"**。

**要改成**：`_resolve_keyframe_path` 增加候选根——**输入 JSON 自身所在目录**
（这样 `assets/keyframes/...` 能相对包根解析到）。让"从包里重新打包"能找回图像。

**为什么必须加这个根（核实时发现的关键点）**：现有候选根**只有两个**，而重新打包时**两个都失效**：
- `artifacts.run_dir` —— `_sanitize_result`（`:221-235`）把 `artifacts` 白名单化，**白名单不含 `run_dir`**；
- `source_video.path` —— `:211` 直接置空；而且它本来就**是视频文件而不是目录**
  （`:188-189` 直接 append 文件路径，没取 `.parent`），代进 `:199-205` 只会得到
  `<video.mp4>/assets/keyframes/...` 这种必然不存在的路径。

所以全部 keyframe 都落到 `:217` 的 `next(..., "")` 默认值（空串），并累积 `:117` 的
`Missing optional keyframe asset` 警告。（相对路径格式的来源在 `:98`：
`relative = f"assets/keyframes/{asset_id}{suffix}"`。）

**不要**：不要把 exit 0 改成 exit 非 0 来掩盖（那是另一个问题）；不要改已写出的相对路径格式
（那会破坏已有包的兼容性）。

**验证**：
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s3.4
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：重新打包后图像资源完整；测试数 ≥ 182。

---

# 阶段 4：分片串号与对齐正确性

**阶段目标**：修掉"内容属于 A、时间和证据属于 B"的错配，并让对齐层的自检真的能报出问题。

> 这一阶段是全计划**最重的逻辑修复**，每一步都必须补一条回归测试。

---

## S4.1 【最重】跨分片 ID 重名 → 章节串号 + 假冲突

**锚点**：`video_parser/parser.py:776-800`（`_merge_video_understanding_results` 裸拼接）、
`video_parser/segmenter.py:130`（`decisions = {item.candidate_id: item ...}`）、
`:260`（`id=f"seg_candidate_{_safe_identifier(chapter.chapter_id, chapter_index)}"`）、
`video_parser/video_alignment.py:411`（`by_id = {item.candidate_id: item for item in decisions}`）、
`video_parser/video_understanding.py` 的 schema 只要求 `chapter_id: {"type":"string","minLength":1}`（无唯一性约束）

**先定位**：
```bash
grep -n "_merge_video_understanding_results" -A 25 video_parser/parser.py | head -40
grep -n "candidate_id: item" video_parser/segmenter.py video_parser/video_alignment.py
grep -n "seg_candidate_" video_parser/segmenter.py
grep -n "chapter_id" -A 3 video_parser/video_understanding.py | sed -n '1,30p'
```

**现状（必读，这条最容易改错）**：
- 每个分片是**独立的一次模型请求**，模型看不到其它分片，所以**每个分片都从 `chapter_1`/`interval_1` 开始编号**
  —— 冲突是**默认行为**，不是偶发。
- 分片本身是**重叠切分**（`parser.py:704-722`，`start = end - overlap_seconds`，默认 15s），
  所以同一段视频内容会出现在两个分片里，**重名 + 重叠**叠加。
- `parser.py` 合并时只更新 `provenance` 的 chunk 起止 + usage + cache_hit，**不重写任何 ID**；
  且该重写被 `if provenance:` 保护 —— 若第 1 个分片没有 provenance，合并结果 provenance 仍为 `None`。
- `segmenter` 用 `{candidate_id: decision}` 建字典 → **重复键后写覆盖先写**；片段 ID 用
  `_safe_identifier(chapter.chapter_id, ...)` → 模型侧冲突被原样继承。

**实测后果**（duration=2400、两分片、accepted 正常路径）：
第 1 分片真实区间 30–90s 的章节被写成 `time_range=[1830.0, 1890.0]`（第 2 分片的时间）
并挂上第 2 分片的 ASR 证据 `tr_0002` / `ev_transcript_0002`；输出 4 条 segment 只有 **3 个唯一 ID**。
触发门槛：默认配置有效分片 1800s → **任何超过 30 分钟的视频都触发**。
还会让章节**靠另一分片的证据拿到 `formal_ir_eligible=True`**，`ir_builder` 按 candidate_id 去重时
会把同 ID 的另一章节当成"已验证"跳过。

**要改成**：在**合并处**给 ID 加命名空间（推荐，改一处见效）：
`_merge_video_understanding_results` 合并每个分片时，把该分片的 `chapter_id` / `interval_id`
统一加前缀（如 `c{chunk_index}_`），并**同步所有引用**（证据引用、alignment decisions、冲突记录）。

**不要**：
- 不要只在 `segmenter` 打补丁（`video_alignment` 与 `ir_builder` 也会踩，要治根）
- 不要改动对外可见的 ID（那是内部 id；但要先 grep 测试与前端确认没有断言写死 ID 形态）
- 不要让旧缓存/旧结果 JSON 变得不可读——如果会，就停下问人

**验证**（必须补一条回归测试）：
构造两个分片、同名 `chapter_1`、区间互不相交（如 10–60 与 900–960），跑
`align_video_understanding` + `_merge_video_understanding_results` + `build_segments_and_evidence`：
- 4 条 decision 应有 **4 个唯一 candidate_id**
- 第 1 分片的章节时间与证据必须**同源**（不再出现 [1830,1890] + `tr_0002`）
- segment 的 ID 全部唯一
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.1
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：上述断言全过，且新增回归测试；测试数 ≥ 183。

---

## S4.2 "重复/未覆盖"冲突检查恒不成立（正是它掩盖了 S4.1）

**锚点**：`video_parser/video_alignment.py:455-481`（判定在 `:463-466`），对照 `:71-98`

**先定位**：
```bash
sed -n '71,100p' video_parser/video_alignment.py
sed -n '450,485p' video_parser/video_alignment.py
```

**现状**：`align_video_understanding`（`:71-98`）为**每个** interval 都追加了决策，
所以 `decision_ids` 必含全部 `interval_id`，`if interval.interval_id not in decision_ids` **恒为假**，
该函数**永不产出冲突**。跨分片重名 interval_id 本应在这里报出来，实际静默通过——
这正是 S4.1 全程无告警的原因。

**核实补充（说明这个函数坏得比看上去更彻底）**：形参 `anchors` **完全未使用**，
`duration_seconds` 被 `del duration_seconds`（`:462`）明确丢弃 —— 也就是说函数名承诺的
**"uncovered（未覆盖）"检查根本不存在**，整个函数只剩一个恒假分支。
所以这一步不是"修一个判断"，而是**把承诺的两类检查（重复 id、未覆盖区间）真正实现出来**。

**要改成**：真正检测重复 id（用**计数**而不是集合成员判断），并把重复/未覆盖如实报成冲突。

**不要**：不要把这个检查删掉了事（它是防线）；不要改冲突数据结构。

**验证**：喂入含重复 `interval_id` 的输入 → 应产出冲突（S4.1 修好后，同类问题以后能被自动发现）。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.2
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：重复 id 能产出冲突记录；测试数 ≥ 183（含 S4.1 的回归测试）。

---

## S4.3 零长度区间：服务端 schema 放行、本地 pydantic 拒绝 → 整个分片作废

**锚点**：`video_parser/video_understanding.py:139-234`（`VIDEO_UNDERSTANDING_RESPONSE_SCHEMA` 字面量；
`:163-170` chapter 的 start/end、`:208-211` interval 的 start/end，**只有 `minimum: 0`、无 `end > start`、
无 `maximum`**；`:237-240` 是 `video_understanding_json_schema()` 辅助函数），
对照 `video_parser/schemas.py` 的 `VideoChapterCandidate.validate_range` / `CandidateEvidenceInterval.validate_range`（要求 `end > start`）

**先定位**：
```bash
grep -n "VIDEO_UNDERSTANDING_RESPONSE_SCHEMA" -A 60 video_parser/video_understanding.py | grep -n "start_seconds\|end_seconds\|minimum\|strict" 
grep -n "def validate_range" -A 8 video_parser/schemas.py
grep -n "_validate_local_range" -A 15 video_parser/video_understanding.py
```

**现状**：`strict: true` 只保证输出符合该 schema，而该 schema **合法允许 `start == end`**；
于是零长度输出**能过 jsonschema 校验**（实测 `{"start_seconds":100,"end_seconds":100}` 过
`Draft202012Validator`），最后倒在 pydantic 上。同一文件内的 `_normalize_asr_segments` 反而显式把
`end <= start` 判为非法，两套标准不一致。

**⚠️ 拦截点务必按核实结果来（原审查描述在这里写错了）**：零长度输出**过不了**
`_validate_local_range` —— 它写的是 `if start < 0 or end <= start or end > duration + 1e-6:`
`raise VideoRangeError(...)`（`video_understanding.py:780-782`），**同样拒绝零长度**。
真实链路是：
`model_validate`（`:494`/`:497`）抛 pydantic `ValidationError`
→ `:450-451` 的 `except (TypeError, ValueError)` 包成 `VideoSchemaError`
→ `VideoResponseError` → `BailianVideoError`
→ `parser.py:580` 捕获 → **整个分片（含其它正确章节）被丢弃**。
用户看到的是"模型未返回可用分段"，而实际上 90% 章节可用。
（结论方向不变，只是拦截点不是 `_validate_local_range`，所以**改的地方也别冲着它去**。）

**要改成**：让零长度区间**只影响它自己**，不作废整片。因为拦截点在 `model_validate`，
可行做法是：
1. 在 `model_validate` **之前**对响应做一次预处理：把 `start == end` 的候选归一化
   （如 `end = start + 最小步长`），或
2. 把 `model_validate` 的失败**按候选粒度**降级——单条不合法只跳过该条并记 warning，
   其余章节继续解析（这是更稳的方向，因为将来任何单条脏数据都不该作废整片）。

**不要**：不要放宽本地 pydantic 的 `end > start` 校验（那会污染下游 IR）；
不要在服务端 schema 里加约束后就假设服务端一定遵守（`strict` 只约束格式，加约束也可能直接让整批失败——
如果要动 schema，**先停下问人**）。

**验证**：喂入一个含零长度区间的分片响应 → 其余章节仍产出，零长度那条被跳过并记 warning。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.3
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：单个坏区间不再作废整片；测试数 ≥ 183。

---

## S4.4 末位采样点 = duration → 必然 FFmpegError，几乎每个视频末区间被误标

**锚点**：`video_parser/interval_refinement.py:199`（`buffered_end = min(duration_seconds, requested_end + config.buffer_seconds)`）、
`:372-395`（`plan_refinement_timestamps`，末位恒为 `end_seconds`）、`:212-218`（失败处理）

**先定位**：
```bash
sed -n '195,220p' video_parser/interval_refinement.py
sed -n '370,398p' video_parser/interval_refinement.py
grep -n "duration_seconds - 0.05\|duration - 0.05" -r video_parser/    # 其它抽帧点的 clamp 写法
```

**现状**：计划器保证最后一个采样点等于 `end_seconds`（数学上：
`count = ceil((end-start)*fps)+1` → `min(end, ...) == end`；截断分支更直接 `values[-1] = end_seconds`）。
当候选区间落在片尾 6 秒内时 `buffered_end == duration_seconds`，于是**以"视频总长"作 `-ss` 抽帧**。
仓库里**其它 7 处抽帧都显式 clamp 到 `duration_seconds - 0.05`**（ffmpeg.py:163、
keyframe_strategies.py:105/164、parser.py:1030、shot_detector.py:211、utils.py:60/62），**只有这一处没有**。
`extract_frame` 末尾有 `if not frame_path.exists(): raise FFmpegError`，所以**无论 ffmpeg 退出码如何都必然抛错**。
`strict=True` 时直接抛 `RefinementError` 终止整个 refine 阶段，后续区间全部丢失。

**要改成**：与其它 7 处对齐，clamp 到 `duration_seconds - 0.05`。

**不要**：不要只改 `plan_refinement_timestamps` 而不改 `buffered_end`（两处都要一致）；
不要放宽 `extract_frame` 的存在性检查。

**验证**：构造末区间贴片尾（如 3595–3600s、duration=3600）的用例 →
不再抛 `FFmpegError`、不再误标 `review_required`。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.4
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：末区间正常抽帧；测试数 ≥ 183。

---

## S4.5 非法区间的"拒绝"分支自己抛 ValidationError

**锚点**：`video_parser/video_alignment.py:200-233`，对照 `video_parser/schemas.py` 的 `AlignmentDecision.validate_ranges`

**先定位**：
```bash
sed -n '198,235p' video_parser/video_alignment.py
grep -n "def validate_ranges" -A 10 video_parser/schemas.py
```

**现状**：该分支意图返回 `status="rejected"` 的 `AlignmentDecision` 并记一条 high 冲突，
但 `validate_ranges` 要求 `original_end > original_start` 且 `aligned_end > aligned_start`——
对 `end <= start` 的候选，**构造决策时先被 pydantic 拒绝**。结果既没有 rejected 决策也没有冲突记录，
破坏了"对齐层永远保留可审阅记录"的设计意图。可达性受限于能否绕过 pydantic（`model_construct`、
上游放宽、未来新增输入源）。

**要改成**：让 rejected 决策能被构造出来（用 `model_construct` 或让该校验对 rejected 状态豁免），
保证非法候选留下可审阅记录而不是抛异常。

**不要**：不要直接放宽 `validate_ranges` 让所有决策都接受非法区间。

**验证**：用 `model_construct` 造 `start=end=10` 的 chapter 调 `align_video_understanding` →
得到 rejected 决策 + 冲突记录，不抛 `ValidationError`。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.5
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：非法候选有可审阅记录；测试数 ≥ 183。

---

## S4.6 `block_*` ID 冲突：`evidence_id` 参数被忽略

**锚点**：`video_parser/ir_builder.py:935`（`id=f"block_{unit_id}_visual_{index:03d}"`），
定义 `:918-940`，调用点 `:160-170`，对照电路路径 `:726`（**已正确去重**）

**先定位**：
```bash
sed -n '915,945p' video_parser/ir_builder.py
sed -n '720,730p' video_parser/ir_builder.py
```

**现状**：`index` 是**单条 visual evidence 内部**的 blocks 下标，`evidence_id` 作为形参传入却
**不参与 id 生成**。一个镜头内落多个采样关键帧是常态（约每 8 秒一帧，
`segmenter.py:67-71` 把同 shot 的关键帧全放进同一 segment）→
实测 3 条 visual evidence 各 1 个 block，产出 **3 个相同的 `block_seg_0001_visual_000`**。
下游会冲突：`_build_relations`（`:1012-1013`）用 block id 作 `Relation.from_id/to_id`，
`planning.py:632` 把它拼进 element id。**电路路径 `:726` 专门加了 `f"{candidate_block.id}_{spec['key']}"` 去重
——作者知道 id 必须唯一，主路径漏了。**

**要改成**：把 `evidence_id` 纳入 id 生成（如 `block_{unit_id}_{evidence_id}_visual_{index:03d}`），
保证同一 segment 内多条 visual evidence 的 block id 唯一。

**不要**：不要改动已在使用的 id 形态的**前缀语义**（下游按前缀解析）；如担心兼容，
先 grep 测试与前端确认没有写死 `block_{unit}_visual_` 这种形态。

**验证**：单 segment 挂 3 条 visual evidence → 打印全部 block id，断言无重复。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s4.6
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：block id 全唯一；测试数 ≥ 183。

---

# 阶段 5：静默失真与成本

**阶段目标**：不再"静默地"算错、复用一个错误结果、或产生预期外的账单。

---

## S5.1 fps 从未随视频元素发送 → 帧预算差 2 倍

**锚点**：`video_parser/video_understanding.py:311-331`（payload 构造，无 fps 字段）、
`:279`（`effective_chunk_seconds = min(max_chunk_seconds, max_frames / fps)`）、
`:664-668`（fps 只出现在提示词文本里）

**先定位**：
```bash
sed -n '275,335p' video_parser/video_understanding.py
grep -n "FPS\|fps" video_parser/video_understanding.py | sed -n '1,40p'
```

**现状**：fps 是**服务端参数**，须随视频元素（`{"video": url, "fps": n}`）/parameters 传入；
本地只在提示词里"声明"（`全局 FPS：1.0（由客户端控制）`，`:668` —— 这只是**叙事文本，不构成参数传递**），
服务端按**默认 2.0** 抽样，而本地预算（`:279`）、缓存键（`:370`）、provenance（`:466`）全按 1.0 计算。
默认 `fps=1.0, max_frames=1800, max_chunk_seconds=1800` → 实际抽帧 1800×2 = **3600 帧，
超过单请求 2000 帧上限** → 长于约 **16.7 分钟**的视频每个分片请求超限失败；
即使不超限，token/费用也是预算的 2 倍；缓存键里的 `fps=1.0` 与实际输入不符。
实测 monkey-patch transport 抓请求体，**确无 fps 字段**。

**四处构造全都不带 fps**（改的时候四处都要看）：payload 顶层与 `video_content`（`:311-330`）、
`video_understanding.py:546`（file_url）、`:551`（https_url）、`:561`（base64）——
后三处都只返回 `{"type": "video_url", "video_url": {"url": ...}}`；
SDK 路径的 `_dashscope_native_messages`（`:900-903`）也只产出 `{"video": url}`。

**要改成**：在 video 元素/parameters 里真正带上 fps，并让 `max_frames` 预算、
缓存键、provenance 三者与实际请求一致。

**不要**：不要只改提示词文本（那没用）；不要在没核实服务端参数名的情况下猜字段名
（先看 `video_understanding.py` 里其它参数的传法，或停下问人）。

**验证**：抓请求体应含 fps；默认配置下 20 分钟视频的分片不再超 2000 帧。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.1
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：请求体含 fps 且与本地预算一致；测试数 ≥ 183。

---

## S5.2 视频理解缓存键缺少 `video_type`

**锚点**：`video_parser/video_cache.py:16-45`（`make_video_cache_key` 入参缺 `video_type`），
调用方 `video_parser/video_understanding.py:364-373`，prompt 构造 `:664-665`

**先定位**：
```bash
sed -n '1,50p' video_parser/video_cache.py
grep -n "make_video_cache_key" -B 5 -A 15 video_parser/video_understanding.py
```

**现状**：传给模型的 prompt 里包含 `课程类型：{video_type}` 和 `视频总时长`，
但缓存键只含 `video_sha1/asr_hash/model/prompt_version/schema_version/fps/chunk 起止`，
**不含 `video_type`**。缓存目录 `<output_root>/<video_id>/video_understanding_cache` 默认开启
（`schemas.py:362`）且跨进程复用。实测：只把 `video_type` 从 `presentation` 改成 `whiteboard`，
transport 只被调用 **1** 次，第二次 `cache_hit=True`，返回上一次（不同 prompt、不同视角）的结果，
用户无法察觉。

**要改成**：`make_video_cache_key` 的入参加上 `video_type`。

**注意**：改了键会让**旧的（错误的）缓存全部 miss**——这正是想要的效果，
但请在报告里明确说明"旧缓存将失效"。

**不要**：不要只递增 `prompt_version` 当捷径（`video_type` 仍然是变化维度，下次还会踩）。

**验证**：同一视频分别用两个 `video_type` 解析 → 第二次应 miss、模型被真正调用。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.2
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：换 `video_type` 不再命中旧缓存；测试数 ≥ 183。

---

## S5.3 文本密度门槛形同虚设 → 空白帧全量进入付费 OCR

**锚点**：`video_parser/interval_refinement.py:56`（`text_density_threshold = 0.015`）、
`:410-422`（`estimate_text_density`：160×90 灰度 → FIND_EDGES → 统计 `value >= 80` 占比）、
`:487`（`_select_ocr_frames`）

**先定位**：
```bash
grep -n "text_density_threshold" -B 3 -A 3 video_parser/interval_refinement.py
sed -n '405,425p' video_parser/interval_refinement.py
sed -n '480,495p' video_parser/interval_refinement.py
```

**现状**：阈值 0.015 是在 14400 像素的小图上按"高梯度像素占比"计的，视频帧缩到 160×90 后
压缩噪点/安全框/边缘底噪就足以超过它，判定退化为"全部通过"。
**实测纯色空框图得 0.03444（> 0.015），其中 496 个高梯度像素全在四周边框环上**；
同数据喂 `_select_ocr_frames`，5 帧全部入选。`ocr=True` 时单区间最多 120 帧、最多 24 区间 →
单视频最多约 **2880 次腾讯云 `GeneralAccurateOCR` 付费调用**，成本与耗时放大 1–2 个数量级。

**要改成**：让空白/纯色/过渡帧真的被门槛挡住。可行方向（择一并说明理由）：
- 排除图像边框环后再统计占比；或
- 按实测空白帧值（0.03444）之上重设阈值；或
- 改用更能区分"有字/无字"的判据。

**不要**：不要把阈值往下调；不要直接关掉密度过滤（那会让成本失控更严重）。

**验证**：用**实测的空白帧数据**跑 `estimate_text_density` → 结果应**低于**新阈值；
真实含字帧仍应通过。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.3
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：空白帧不再入选 OCR；含字帧仍入选；测试数 ≥ 183。

---

## S5.4 SDK timeout 被当成请求参数发出，600s 配置完全无效

**锚点**：`video_parser/video_understanding.py:414-419`（`_dashscope_sdk_call(..., timeout_seconds=request.timeout_seconds, ...)`）

**先定位**：
```bash
sed -n '408,425p' video_parser/video_understanding.py
grep -rn "DEFAULT_REQUEST_TIMEOUT_SECONDS\|def call" .venv/Lib/site-packages/dashscope/aigc/multimodal_conversation.py | head
```

**现状**：`MultiModalConversation.call` **没有 `timeout_seconds` 形参**，未知 kwargs 被
`request_data.add_parameters(**kwargs)` 塞进**请求体 parameters**；真正的 HTTP 超时取 SDK 全局
`DEFAULT_REQUEST_TIMEOUT_SECONDS = 300`。所以 `BailianVideoConfig.timeout_seconds=600` 完全无效，
还向原生端点发送了一个**未定义参数**（有触发 InvalidParameter 的风险）。
实测：把 `dashscope.base_http_api_url` 指向本地抓包服务器，请求体 parameters 里出现 `"timeout": 600`。
默认 `input_mode="auto"` + 本地文件（最常见的本地部署路径）必走此路。

**要改成**（这个 SDK 机制已核实清楚，不用再猜）：SDK 的超时关键字是 **`request_timeout`**
（`dashscope/common/constants.py:21 REQUEST_TIMEOUT_KEYWORD = "request_timeout"`，
默认 `DEFAULT_REQUEST_TIMEOUT_SECONDS = 300` 在 `:20`）。所以把 `_dashscope_sdk_call` 里
传给 `MultiModalConversation.call` 的 `timeout=timeout_seconds`（`video_understanding.py:840-848`）
改成 **`request_timeout=timeout_seconds`**，让配置的 600s 真正生效，同时不再把
`timeout` 塞进请求体。

顺带清理（同一处改动范围内）：`response_format` 同样**不是** `call` 的形参
（会一并被 `**kwargs` 塞进 parameters），而 `:849-858` 那个 `except TypeError:` 兜底
在本版本**不可达**（`**kwargs` 吞掉未知关键字，实测 `sig.bind(...)` 正常返回不抛 TypeError）。
这两处一并在同一步里处理。

**不要**：不要魔改 site-packages 里的 SDK（`.venv` 里实测是 dashscope **1.27.6**，
pyproject 约束 `>=1.20,<2`、uv.lock 锁 1.27.2 —— 各环境小版本可能不同，所以**改完要在自己的环境里抓包确认**）。

**验证**：抓请求体 → parameters 里**不应**出现 timeout；超时行为符合配置。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.4
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：请求体无未定义参数；测试数 ≥ 183。

---

## S5.5 SDK 通路忽略 `config.base_url`，本地文件模式静默直连公网

**锚点**：`video_parser/video_understanding.py:414-420`

**先定位**：
```bash
sed -n '408,425p' video_parser/video_understanding.py
grep -n "base_url\|endpoint\|base_http_api_url" video_parser/video_understanding.py
```

**现状**：REST 通路用 `config.endpoint`（来自 `DASHSCOPE_BASE_URL`/`BAILIAN_BASE_URL`），
SDK 通路**完全不看该配置**，SDK 使用全局 `dashscope.base_http_api_url`（默认公网 `dashscope.aliyuncs.com`）；
而默认 `input_mode="auto"` 恰好走 SDK 通路。部署把 `DASHSCOPE_BASE_URL` 指向企业代理/私有网关时，
请求**静默绕过代理直连公网**，既违背配置意图也可能被网络策略阻断。

**要改成**：SDK 通路也尊重配置（把 `config.endpoint` 设置到 SDK 的全局配置上）。

**不要**：不要改全局 SDK 配置而不考虑多线程/多实例影响（如果涉及，先问人）。

**验证**：抓包确认请求指向配置的地址，而不是公网端点。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.5
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：SDK 通路的目标地址与配置一致；测试数 ≥ 183。

---

## S5.6 base64 / 本地文件体积上限与服务端限制不符

**锚点**：`video_parser/video_understanding.py:74`（`max_base64_bytes: int = 12 * 1024 * 1024`）、
`:556`（按原始字节比较后放行）

**先定位**：
```bash
grep -n "max_base64_bytes" -B 3 -A 5 video_parser/video_understanding.py
sed -n '550,560p' video_parser/video_understanding.py
```

**现状（核实后更准确的两条）**：
1. **base64 分支比较的是原始文件字节**：`size = path.stat().st_size`（`:552-561`），
   而实际发出的是 `f"data:{mime_type};base64,{encoded}"`，体积是 **4/3 倍**
   （12MiB 原始 → 约 16MiB 编码后），未乘系数。变量名/语义是"base64 上限"，比对的却是原始字节。
2. **file_url（`:537-546`）与 https_url（`:547-551`）两个分支完全没有体积检查** ——
   而默认 `input_mode="auto"` 走的正是 `file_url`（`:535-536`），
   所以**默认配置下本地不设任何上限**，直接交给服务端/SDK 上传。

官方限额（base64 编码后 10MB ≈ 原始 7.5MB；本地文件 100MB）**仍需按文档核实**（见下）。

**⚠️ 这一步的审查结论是 SUSPECT**：限制数值来自官方文档而非逐字核对（WebFetch 被域名安全策略拦截），
且 SDK 是否把 `file://` 换成 `oss://` 从而改按公网 URL 限额执行**未确认**。

**要改成**：**先核实官方限额**（查 Model Studio 文档原文，或问委托人），确认后再改：
base64 判定按编码后体积；本地文件模式补体积检查。
（**本轮做不做**看 **§0.5 决策 3**：勾了就做；空框就先只交核实结论、不动代码。）

**不要**：不要在没核实限额的情况下直接改数字。

**验证**：超限输入本地立即拒绝并给出清晰错误（不再等服务端拒绝）。
```bash
# 1) 本步的针对性检查（先读 scripts/verify_video_parser_fixes.py 文件头部说明）
./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s5.6
#    动手前应打印 [BUG] —— 这就是“这个 bug 真实存在”的证据；
#    改完后必须变成 [OK]。仍然是 [BUG] 就是没改对，pytest 全绿也不算完。
# 2) 全量回归
./.venv/Scripts/python.exe -m pytest -q     # 必须 ≥ 182 passed
```
**完成判据**：限额已核实 + 本地校验与服务端一致；测试数 ≥ 183。

---

# 阶段 6：低危收尾

**阶段目标**：清掉不影响主链路但会污染统计/掩盖故障/浪费磁盘的小问题。

每一步同样"一改一验证一提交"，但**可以合并成较少的 commit**（同类问题一起改）。
回归统一是 `./.venv/Scripts/python.exe -m pytest -q` ≥ 183 通过。

**下面哪些有自动检查**（跑 `scripts/verify_video_parser_fixes.py <编号>`）：

| 有自动检查（动手前 `[BUG]`，改完必须 `[OK]`） | 只能手工核对 |
|---|---|
| S6.1、S6.2、S6.4、S6.5、S6.8、S6.11、S6.13、S6.14 | S6.3、S6.6、S6.6b、S6.7、S6.9、S6.10、S6.12、S6.15、S6.16 |

**手工核对的那几条**：按"问题"列逐条给出**可复现的具体证据**（命令 + 输出，或代码行 + 为什么），
不要只写"已修复"。特别是 S6.7 / S6.9 / S6.10 这三条属于"删掉假逻辑"还是"接上真逻辑"的二选一，
**选完要在报告里写明选了哪个以及为什么**。

| # | 位置 | 问题 | 要改成 | 不要 |
|---|---|---|---|---|
| S6.1 | `video_parser/parser.py:683-684` | 同一条 warning 写两次（`_handle_warning_or_raise` 内部已 append，紧接着又 append），前端按条数统计会算错 | 删掉重复的 append | 不要改 `_handle_warning_or_raise` 的行为 |
| S6.2 | `video_parser/shot_detector.py:107-108`、`keyframe_strategies.py:198-199` | `fps = cap.get(CAP_PROP_FPS) or 25.0` 只兜得住 `0`（假值）；NaN/inf 是真值会被保留，随后 `int(round(fps/sample_fps))` 对 NaN 抛 `ValueError`、对 inf 抛 `OverflowError`，两者都**不在** `parser.py:193` 的捕获列表里 → 非 strict 模式也整体失败，连"退化为单镜头"的兜底都走不到 | 用 `math.isfinite` 之类的判断把 NaN/inf 也归到兜底值；或把异常纳入捕获列表 | 不要放宽 parser 的兜底语义 |
| S6.3 | `video_parser/shot_detector.py:170-176` | 取代表帧失败时静默回退读**第 0 帧**，但 `representative_timestamp_seconds` 仍是镜头中点 → 第 20 分钟的画面被标成 20:00 进入 IR/课件 | 回退时把时间戳也改成 0（保持一致），或标 warning 说明是回退帧 | 不要静默回退而不改时间戳 |
| S6.4 | `video_parser/ffmpeg.py:218-224` | `extract_sample_keyframes` 循环内任一帧抛 `FFmpegError` 就整体抛出，已成功写盘的帧全丢（磁盘上还在，只是不被返回）；而 profile 分支（`parser.py:113-131`）是逐帧 append、失败保留前几帧——**两条路径行为不一致**。调用方 `parser.py:84-132` 的 `except (FFmpegError, KeyframeStrategyError)` 会捕获并降级为 warning，此时该视频 keyframe 列表为空 | 统一成"逐帧保留已完成的部分 + 记 warning" | 不要两套行为并存 |
| S6.5 | `video_parser/ffmpeg.py:246-247` | `extract_frame` 只检查文件存在、不检查大小，而 `extract_video_segment`（`:147`）检查了 `st_size <= 0`——标准不统一 | 统一检查标准 | — |
| S6.6 | `video_parser/parser.py:520`（创建 `chunk_dir`）、`:539-542`（写分片） | 分片 mp4（CRF23 重编码，体积与源相当或更大）从不清理；文件名含 start/end，改参数重跑会留新文件而非覆盖 → `data/video-parser` 线性膨胀。核实确认：`run_dir`（`:58`）**长期保留，不是 TemporaryDirectory**；全目录的删除逻辑只有 `ffmpeg.py:216` 与 `shot_detector.py:161` 两处，都不针对分片 | 加清理策略（TTL 或 `--cleanup` 选项），或在重跑时覆盖同名分片 | 不要直接删正在使用的当前分片 |
| S6.6b | `video_parser/video_cache.py` | **核实时的附带发现**：`VideoUnderstandingCache` 同样**没有任何淘汰/TTL 逻辑**，`video_understanding_cache` 目录只增不减 | 与 S6.6 一起考虑清理策略 | 注意 S5.2 会改缓存键，改完旧缓存会全部失效——正好可以借机清理，但**先问人**再删 |
| S6.7 | `video_parser/ir_builder.py:268`（配 `:1120-1121` 的 `all_refs`） | `if item.id not in all_refs(units)` 是**死条件**（`item.id` 是 relation id，`all_refs` 返回 evidence id，两者永不相交，恒真），实际等于无条件全量 extend；`all_refs` 仅此一处使用，整段逻辑无效 | 删掉死条件，直接写明确的逻辑；或删掉 `all_refs` | 不要保留"看起来在过滤"的假逻辑 |
| S6.8 | `video_parser/planning.py:239` | `per_stage = max(1, plan.estimated_minutes // max(1, len(plan.stages)))` 向下取整 → 7 个单元、`estimated_minutes=20` 得每环节 2 分钟、**合计 14 分钟**，而标题页写的是"20 分钟"；45 分钟 / 10 单元 → 40 分钟 | 让各环节之和等于声明时长（余数分配到前几节） | 不要改声明时长去迁就取整 |
| S6.9 | `video_parser/cli.py:81,228` + `intermediate_schemas.py:222,224,226` | `--style`、`DemoGenerationRequest.outputs`、`language` **接受但不生效**（不传该参数产出逐字节相同；`outputs=["html"]` 仍渲染全部格式，且计划文件里记的是 `["html"]`，与实际产物不符） | 二选一：**要么实现**，**要么从 CLI 与模型里删掉**（不要让参数假装有效） | 不要保留"接受但忽略"的参数 |
| S6.10 | `video_parser/package.py:294-297`（`thresholds` 声明） | `thresholds`（`keyframe_duplicate_rate_max=0.15` 等）**只写不读**，全仓无读取方；实测重复率 0.667 远超 0.15 也不产生任何 issue。**归属订正**（核实时发现审查原话张冠李戴）：状态判定其实分两处，**都不看 thresholds** —— `quality.py:144` 只看 issue severity 计数（`error/warning/ok`）；`package.py:265` 只看 `warnings` 列表 + `ir.quality.status`（它写的正是 `quality_report.json` 的 `status` 字段）。`quality.py` 里 `grep "thresholds"` 与 `grep "ir.quality"` **均 0 命中**。唯一沾边的消费者是 `quality.py:156-159`：它只把 `metrics` 数值**原样拷贝**、不做任何阈值比较、不产生 issue | 二选一：**要么接上判定**，**要么删掉**（不要留着永不执行的自检契约） | 不要留假契约；不要顺手在 `quality.py` 里找判定逻辑（那里没有） |
| S6.11 | `video_parser/package.py:303-318` | `file_sha256` 失败时压入唯一化的 `missing:<id>` → "全部读不到"被算成"每帧都不同"：重复率 0.0、唯一数=全量——**质量指标反向变好**，掩盖真实故障 | 读不到的帧单独计数，**从唯一数与总样本里排除**并记 issue | 不要用占位符参与去重统计 |
| S6.12 | `video_parser/video_alignment.py:47,144-151` | 类型注解声称支持 `ParsedVideoSegment`，但用 `getattr(shot,"start_seconds"/"end_seconds",None)`，而 `schemas.ParsedVideoSegment` 只有 `time_range` → 静默贡献 **0 个锚点**，边界分数系统性偏低，`review_required` 误报增多 | 改为读 `time_range`，或修正类型注解 | 不要两边都不改 |
| S6.13 | `video_parser/interval_refinement.py:160-170` | `existing_evidence` 形参收而不读，函数体内从未引用；`_refined_range` 只用本区间新产生的证据时间戳 | 用它，或删掉形参 | 不要留"看起来在对齐"的假参数 |
| S6.14 | `video_parser/transcription.py:96` | `transcript_from_manual_text` 产出 `start_seconds=0.0, end_seconds=0.0` 的零长度段，与 `_normalize_asr_segments`（`end <= start` 判非法）及 schemas 的区间约束矛盾 | 修正区间或让该路径与主路径同标准 | 不要与 S4.3 的修法互相冲突 |
| S6.15 | `video_parser/schemas.py:79` | `Transcript.text_normalization` 的 Literal 含 `"none"`，但两个生产者恒写 `"simplified_chinese"`，且全仓无读取方 | 与 S1.2 一起处理（S1.2 会让它被真正使用） | — |
| S6.16 | `video_parser/ir_builder.py:1029` 附近 | 把 `schemas.ConflictItem` 投影成 IR 的 `ConflictItem` 时丢掉 `candidate_id`/`model_value`/`local_value`（压平进 `candidate_values`） | 补回字段，或明确记录这个取舍 | — |

---

## 附：明确排查过、**不要**再动的部分

以下地方审查确认**干净**，执行时不要"顺手重构"：

- `video_parser/vision.py`、`video_parser/ocr.py` 全文件
- `video_parser/utils.py` 全函数（含 `sample_timestamps` 各边界分支、`file_sha1` 分块读取）
- `ir_builder._chunk_text`（4000 组随机文本对拍通过）
- ffmpeg 参数/过滤器语法、分片切分（overlap 各种取值无空洞）、帧号时间戳换算
- segmenter / ir_builder 的双重核验门（无"时间区间无人覆盖"的空洞）
- 路径穿越防护、包完整性校验、缓存原子性、CLI 参数校验
- Windows 非 ASCII 路径处理（`video_parser` 内**没有**类似 `backend/config.py` 的隐患）

---

## 附：完成后的验收清单

全部阶段做完后，应满足：

- [ ] **`./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py` 全部 33 项 `[OK]`，0 项 `[BUG]`/`[ERR]`**
      （这是主判据：`33 项：33 通过，0 未通过`）
- [ ] `./.venv/Scripts/python.exe -m pytest -q` ≥ 183 通过，0 失败
- [ ] 非电路课产物里 grep 不到 通路/断路/短路/LED/正负极
- [ ] 缺 whisper 模型 / 缺 opencc / 缺 ffmpeg 三种环境下，解析都能降级而不崩
- [ ] >30 分钟视频：章节时间与证据同源，segment ID 全唯一
- [ ] `grep -rn "jjjj" video_parser/` 为空
- [ ] `grep -rn "1101.067" video_parser/` 为空
- [ ] 换成 `--video-type` 重解析会重新调用模型，不再命中旧缓存
