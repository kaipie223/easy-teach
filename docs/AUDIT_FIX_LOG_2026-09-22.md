# 代码审计与修复日志（2026-09-22）

对 easy-teach 全仓库做的一轮审计与逐项修复记录。

- 范围：`backend/`、`ai/`、`frontend/src`、`tests`
- 基线：`pytest -q` = **178 通过 / 3 失败**（3 项均为既有失败）
- 方式：先审计出清单，确认后**一次只改一项**，每项改完立即跑 `pytest` + 实际运行验证

## 硬约束

- 不新增第三方依赖
- 不改数据库 schema
- 不改 API 契约（前端正在调用）
- 测试通过数不低于 178；不通过修改测试断言来让测试变绿
- 不重构 `backend/services/generator.py`（在飞）
- 不做纯风格重构

## 问题清单（按优先级）

| # | 优先级 | 问题 | 状态 |
|---|---|---|---|
| 1 | P0 | SSE 方法不匹配：前端硬编码 POST，事件端点只有 GET → 三处实时进度失效 + 静默无限重连 | ✅ 已修复 |
| 2 | P1 | 模板兜底产出空壳页（标题=唯一要点）与占位符泄漏（"待确认"等） | ✅ 已修复 |
| 3 | P1 | 模板给出的 layout 值不是规范值 → 被位置兜底吞掉版式意图 | ✅ 已修复 |
| 4 | P1 | chromadb 版本不匹配，`FastEmbedEmbeddingFunction` 缺 `name` | ✅ 已修复 |
| 5 | P1 | `normalize_chroma_path()` 用了盘符相对路径 `Path("C:")` | ✅ 已修复 |
| 6 | P2 | RAG 静默降级：未建索引时返回空列表，用户完全不知情 | ⏸ 未修复（见下） |

---

## 修复记录

### 6. P2 — RAG 静默降级（未修复，与硬约束冲突，待另行设计）

未建索引 / 集合缺失时，`search_sync` 吞掉 `FileNotFoundError` 返回 `[]`，前端只显示
"来源：暂无可用证据"，用户不知道是"没有证据"还是"根本没建索引"。

**不做这项的原因**：让用户感知需要把"未建索引"作为状态信号暴露给前端（改 `RAGDocument`
返回结构或新增状态字段），属于**改 API 契约**，与本次硬约束"不改 API 契约"冲突。已保留原状，
建议作为独立的一轮（连同知识库页面的引导 UI）一起设计。


### 4. P1 — chromadb 版本不匹配，embedding function 缺 Chroma 1.x 要求的方法（已修复）

**现象**：`test_fastembed_adapter_builds_and_queries_chroma` 报
`AttributeError: 'FastEmbedEmbeddingFunction' object has no attribute 'name'`。

**根因**：`pyproject.toml` 钉 chromadb 0.5.3，实际安装 1.5.9。Chroma 1.x 在读取集合时调用
`embedding_function.name()` 做冲突检测、查询时调用 `embed_query()`，而本项目是普通类、
没有继承 Chroma 的 `EmbeddingFunction` 协议，两个方法都不存在。

**改动**（`ai/rag/embedding.py`，**未改依赖版本、未新增依赖**）：

- 新增 `name() -> str`
- 新增 `embed_query(input)` → 转发 `__call__`

**验证**：

```
$ pytest tests/test_m3_knowledge.py::test_fastembed_adapter_builds_and_queries_chroma -q
1 passed
```

---

### 5. P1 — `normalize_chroma_path` 盘符相对路径 + 与生产校验冲突（已修复）

**现象**：`CHROMA_PERSIST_DIR must be inside DATA_DIR`（两个 security 测试红）。路径随 CWD 漂移。

**根因**（两层）：

1. `Path(system_drive)` 其中 `system_drive="C:"` 是盘符相对路径，`.resolve()` 锚到 CWD。
2. 更深一层：ASCII 重定向（为绕开 hnswlib 的非 ASCII 限制）本身与"chroma 必须在 DATA_DIR 内"
   的生产校验**根本矛盾**——非 ASCII 的 DATA_DIR 里任何路径都非 ASCII，重定向到盘根必然出界。
   这正是这两个测试在基线里就红的原因。

**处置**（与用户"冲突即报告"一致，已在此说明）：

- `backend/config.py`：**生产环境不做重定向**，`chroma_persist_dir` 保持原路径（由运维保证 ASCII 的
  DATA_DIR，硬校验兜底）；只有开发环境才做 ASCII 重定向。
- `backend/config.py`：盘根改用 `Path(f"{system_drive}\\")`，不再 CWD 漂移。
- `ai/rag/retriever.py`、`ai/rag/embedder.py`：去掉运行时的 `normalize_chroma_path` 调用，改为
  `Path(...).expanduser().resolve()`——它们拿到的本就是配置解析阶段归一过的路径，重定向只该在
  配置处发生一次；这也让"传入明确目录（测试/临时目录）就原样使用"的行为恢复正确。

**验证**：

```
$ pytest -q  ->  181 passed, 2 warnings     # 三个既有失败全部转绿
```

---

### 3. P1 — 模板页型名不被渲染器识别（已修复）

**现象**：模板兜底路径产出的 layout 序列是 `cover / agenda / overview / knowledge×4 / example / interaction / summary`，其中只有 3 个是规范值，其余被 `_slide_layout()` 的位置兜底吞掉，版式意图丢失。

**根因**：`compile_plan_content()` 用 `overview / knowledge / example / misconception / process / interaction / application` 这些自定义页型名，而 `LAYOUT_ALIASES` 里没有对应映射。

**改动**（`backend/schemas.py` 的 `LAYOUT_ALIASES`，一处生效，历史快照也一并修复）：

```
overview → bullets     example → steps        process → flow
knowledge → bullets    misconception → compare
interaction → bullets  application → bullets
```

**验证**：

```
overview       -> bullets    OK
knowledge      -> bullets    OK
example        -> steps      OK
misconception  -> compare    OK
process        -> flow       OK
interaction    -> bullets    OK
application    -> bullets    OK

中间页 example: steps      # 不再被位置兜底成 bullets
末页 summary : summary
首页 cover   : cover

$ pytest -q  ->  3 failed, 178 passed
```


**现象**：`generation_mode="template"`（以及 AI 失败时的降级）下，10 页课件里有 4 页是"标题与唯一要点完全相同"的空壳；封面与概览页出现 `授课对象：待确认1`、`教学重点：1` 这种面向学生的坏数据。

**根因**（三条独立）：

1. 空壳页 — `_point_bullets()`：知识点没有 `key_points` 也没有 `examples` 时，`bullets.extend(details or [point.title])` 把标题原样当要点，而幻灯片标题也是 `point.title`。
2. 占位符泄漏 — `compile_plan_content()` 无条件拼接 `授课对象：{...}` / `教学重点：{...}`，字段为空或脏值也照拼。
3. "待确认"的来源 — `ai/prompts/confirm.txt:27` 要求模型对缺失信息标注"待确认"，于是该标记进入 brief 字段后被投影给学生。

**改动**（`backend/services/courseware.py`）：

- 新增 `_usable_field(value, min_length=2)`：空值、单个字符、纯数字、以及含 `待确认/待定/tbd/n-a` 的值一律判为不可用。
- 封面：`授课对象` 不可用时整条省略，而不是输出 `授课对象：`。
- 概览页与误区页：重点/难点先经 `_usable_field` 过滤，脏值退回通用表述。
- `_point_bullets()`：知识点无要点时改为给出可执行的学习任务（"用自己的话解释… / 举一个…例子"），不再是标题复读。

**验证**：

```
# 脏 brief（修复前：4 空壳页 + 2 条占位符）
总页数: 10   空壳页: 0
含占位符的要点: []
封面要点: ['课程时长：45 分钟', '学习目标：理解浮力']   # 授课对象：待确认1 已省略

# 干净 brief（确认没有误过滤）
封面要点: ['授课对象：初三学生', '课程时长：45 分钟', '学习目标：理解浮力']
概览: 教学重点：影响浮力大小的因素
概览: 学习难点：阿基米德原理的理解与应用

$ pytest -q  ->  3 failed, 178 passed   # 无回归
```


**现象**：`ExportsView` / `MaterialsView` / `EditorView`（任务流）的进度订阅全部拿不到数据，前端每 3 秒重连一次，且界面上没有任何报错。

**根因**：`frontend/src/utils/sse.js:39` 硬编码 `method: 'POST'`；而 `backend/routers/projects.py:198`、`backend/routers/generate.py:163` 只注册 `@router.get`，POST 必然 405。405 是永久性失败，但两个视图的 `onError` 只调用 `scheduleReconnect()`（3000ms）而不上报错误，于是变成静默空转。

**改动**（纯前端，后端契约未变）：

- `frontend/src/utils/sse.js` — 构造函数增加 `{ method = 'POST' }` 选项；`connect()` 在 GET 时不发送请求体、也不声明 `Content-Type: application/json`
- `frontend/src/views/MaterialsView.vue:640`、`ExportsView.vue:156`、`EditorView.vue:535` — 事件订阅显式传 `{ method: 'GET' }`
- 对话（`/chat`）、蓝图（`/plan`）、局部重生成（`/revisions/regenerate`）仍走 POST 带请求体，行为不变

**验证**：

```
# 改动前（对运行中后端）
/api/v1/projects/p_346affbd…/events   POST -> 405    GET -> 200
/api/v1/tasks/task_missing/events     POST -> 405    GET -> 404

# 改动后：构建产物中三个视图各自带上 GET
$ Select-String -Path dist/assets/*.js -Pattern 'method:"GET"'
EditorView-B3IVZU5p.js: 命中 1 次
ExportsView-rNljSSOT.js: 命中 1 次
MaterialsView-BjkXRiwe.js: 命中 1 次

$ pytest -q  ->  3 failed, 178 passed        # 后端未动，基线保持一致
$ npm run build  ->  ✓ built in 9.10s
```
