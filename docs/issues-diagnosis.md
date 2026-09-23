# 🏥 easy-teach 项目问题诊断报告

> 生成日期：2026-08-12  
> 诊断范围：全栈（后端 FastAPI + 前端 Vue3 + AI 模块 + 基础设施）  
> 问题总数：**28 项**（Critical 5 / High 9 / Medium 8 / Low 6）

---

## 目录

- [一、后端服务层（Backend Services）](#一后端服务层backend-services)
- [二、后端路由层（Backend Routers）](#二后端路由层backend-routers)
- [三、前端 API 层（Frontend API Layer）](#三前端-api-层frontend-api-layer)
- [四、前端视图层（Frontend Views）](#四前端视图层frontend-views)
- [五、AI 模块（ai/ Module）](#五ai-模块ai-module)
- [六、基础设施 / DevOps](#六基础设施--devops)
- [七、数据模型 / Schema 对齐](#七数据模型--schema-对齐)
- [附录：严重程度定义](#附录严重程度定义)

---

## 一、后端服务层（Backend Services）

**归属部门：后端开发组**  
**涉及目录：`backend/services/`**

### 🔴 C1 — `intent.py` 文件结构严重损坏，无法导入

**文件**：`backend/services/intent.py`  
**严重程度**：Critical（应用无法启动）

**问题详述**：

该文件是 M1 意图分析模块的核心胶水层，负责连接 `ai/intent/analyzer.py`（真实 LLM 调用）与 `orchestrator.py`（编排调度）。当前文件处于严重损坏状态，存在以下致命错误：

1. **第 7 行** — `logger = logging.getLogger(__name__)` 使用了 `logging` 模块但从未 `import logging`，启动时抛出 `NameError`
2. **第 45-48 行** — `_analyzer = IntentAnalyzer(api_key=..., base_url=...)` 引用了不存在的 `IntentAnalyzer` 类。真实的 `IntentAnalyzer` 类位于 `ai/intent/analyzer.py`，但本文件从未导入它
3. **第 51-177 行** — `__init__`、`analyze`、`lock_intent`、`_parse_intent` 等方法缩进在模块级别，没有包裹在 `class IntentAnalyzer:` 声明中。Python 语法上合法（模块级嵌套函数），但这些函数接收 `self` 参数却无类绑定，运行时根本无法被正确调用
4. **第 55、89 行** — 使用了 `OpenAI` 和 `json` 但从未 import
5. **第 183-189 行** — 模块级函数 `get_raw_intent()` 和 `lock_intent()` 调用已损坏的 `_analyzer` 对象

**影响范围**：

`orchestrator.py` 第 27 行 `from services.intent import get_intent_analyzer` 触发此文件的导入 → 整个 `orchestrator.py` 无法加载 → 所有路由（chat、generate）不可用 → **整个后端应用在首次请求时崩溃**。

**参考**：`ai/intent/analyzer.py` 中有完整可用的 `IntentAnalyzer` 类（含状态机、LLM 调用、追问/确认生成），可作为修复基础。

---

### 🔴 C2 — `parser.py` 导入路径错误

**文件**：`backend/services/parser.py` 第 13 行  
**严重程度**：Critical（模块加载即失败）

**问题详述**：

```python
from backend.config import settings
```

当 FastAPI 应用从 `backend/` 目录启动时（`uvicorn main:app`），Python 的当前工作目录是 `backend/`，`sys.path` 包含 `backend/` 但不包含其父目录。此时 `backend` 不是有效的顶级包名，导入语句抛出 `ModuleNotFoundError: No module named 'backend'`。

正确写法应为 `from config import settings`（该模块中其他后端文件均使用此写法）。

**影响范围**：任何调用 `parser.py` 中函数（`parse_pdf`、`parse_docx`、`parse_image`、`parse_video`）的代码全部失败。这意味着 `_load_references()` 中所有文件解析都会抛异常 → 课件生成时参考资料永远为空。

---

### 🔴 C3 — `rag.py` 导入路径错误

**文件**：`backend/services/rag.py` 第 5 行  
**严重程度**：Critical（模块加载即失败）

**问题详述**：

```python
from models.schemas import RAGDocument
```

`RAGDocument` 类定义在 `backend/schemas.py`（模块名 `schemas`），而非 `backend/models/schemas.py`（该文件不存在）。正确写法应为 `from schemas import RAGDocument`。

**影响范围**：RAG 检索模块无法加载 → 课件生成第 2 步（RAG 检索）失败 → 课件缺少知识库上下文。

---

### 🔴 C4 — `speech.py` 依赖脆弱的 sys.path hack

**文件**：`backend/services/speech.py` 第 4 行  
**严重程度**：Critical（取决于导入顺序，非确定性失败）

**问题详述**：

```python
from ai.speech.transcriber import SpeechTranscriber
```

`ai/speech/transcriber.py` 在模块顶部执行 `sys.path.insert(0, ...)` 来 hack 导入路径：

```python
# ai/speech/transcriber.py 第 5-7 行
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "backend"))
```

如果 `services/speech.py` 在 `ai/speech/transcriber.py` 之前被导入（Python 按 `import` 语句执行顺序加载），`sys.path` 尚未被修改 → `ModuleNotFoundError`。导入顺序取决于模块加载顺序，非确定性。

**影响范围**：语音转写功能随机不可用。在 orchestartor 不直接 import speech 的场景下（当前 codebase 如此），问题可能隐藏；但一旦有代码路径触发 → 崩溃。

---

### 🔴 C5 — `orchestrator.py` 导入不存在的函数

**文件**：`backend/services/orchestrator.py` 第 27 行  
**严重程度**：Critical（应用无法启动）

**问题详述**：

```python
from services.intent import get_intent_analyzer
```

当前 `services/intent.py` 中**不存在**名为 `get_intent_analyzer` 的函数。该文件末尾只定义了 `get_raw_intent` 和 `lock_intent`（且它们本身也是损坏的，参见 C1）。

`Orchestrator.__init__` 第 39 行调用 `get_intent_analyzer()` → `ImportError`。

**影响范围**：这是系统核心调度器的初始化入口。此导入失败 → Orchestrator 单例创建失败 → 所有 chat、generate 请求 500 错误。

---

### 🟠 H1 — `parse_image` 返回类型与调用方不兼容

**文件**：`backend/services/parser.py:23` vs `backend/services/orchestrator.py:234`  
**严重程度**：High（运行时类型错误）

**问题详述**：

- `parse_image()` 返回 `dict`：`{"status": "success", "message": "识别成功", "data": result_text}`
- 但 `orchestrator.py` 的 `_load_references()` 将其返回值当作 `str` 使用：
  ```python
  text = _sync(parse_image(str(path)))
  refs.append(ReferenceMaterial(extracted_text=text, ...))
  ```
- `ReferenceMaterial.extracted_text` 的类型是 `str`，实际传入的是 `dict`
- Pydantic 会将 dict 强制转为字符串表示（`"{'status': 'success', ...}"`），导致课件中充斥着 Python dict 字面量垃圾文本

**影响范围**：所有包含图片解析的课件生成。

---

### 🟠 H2 — `_sync()` 函数存在嵌套事件循环死锁风险

**文件**：`backend/services/orchestrator.py:203-215`  
**严重程度**：High（特定条件下死锁）

**问题详述**：

```python
def _sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
```

当 `run_generation` 在 FastAPI `BackgroundTasks` 中执行时，它运行在 `anyio` 的工作线程池中。此时 `asyncio.get_running_loop()` 可能返回主线程的 event loop（非 None），但主 event loop 正忙于处理 HTTP 请求。代码检测到运行中的 loop 后，在线程池中再开线程执行 `asyncio.run()`。这种"event loop 嵌套 + 线程池嵌套"的组合在 CPython 的 GIL 和 asyncio 实现下存在死锁风险。

此外，`ThreadPoolExecutor` 的默认 `max_workers` 受 `os.cpu_count()` 限制，在高并发时可能耗尽。

**影响范围**：后台课件生成任务可能永久挂起。

---

### 🟠 H3 — PDF 图片提取保存到当前工作目录

**文件**：`backend/services/parser.py:126`  
**严重程度**：High（并发冲突 + 安全风险）

**问题详述**：

```python
image_filename = f"extracted_pdf_p{page_num + 1}_img{real_img_count}.png"
img_obj.save(image_filename, format="PNG")
```

文件名仅包含页码和计数器，无随机标识。当两个 PDF 被同时解析时，后者的图片会覆盖前者，导致内容错乱。此外，文件写入当前工作目录（CWD），在 Docker 中为 `/app`，可能无写权限，也可能污染应用目录。

**影响范围**：并发文件解析时内容错乱；Docker 部署时可能因无写权限而崩溃。

---

### 🟡 M1 — PPT 生成器仅填充标题，无实际教学内容

**文件**：`backend/services/generator.py`  
**严重程度**：Medium（功能残缺）

**问题详述**：

`generate_pptx()` 创建幻灯片后仅设置标题（`shapes.title.text = kp.get("title", "知识点")`），不填充正文、要点、示例、图片。生成的 PPT 只有封面 + 各知识点的空白标题页，无教学价值。

---

### 🟡 M2 — Word 生成器正文写死占位文本

**文件**：`backend/services/generator.py`  
**严重程度**：Medium（功能残缺）

**问题详述**：

"教学过程"各步骤的正文内容写死为 `"（教学内容待补充）"`：

```python
for step in intent.get("logic_flow", []):
    doc.add_heading(step, level=2)
    doc.add_paragraph("（教学内容待补充）")
```

RAG 检索到的知识库内容和参考资料未被注入到 Word 教案中。

---

### 🟡 M3 — HTML 互动页面无真实互动逻辑

**文件**：`backend/services/generator.py`  
**严重程度**：Medium（功能残缺）

**问题详述**：

```html
<button onclick="alert('恭喜完成！')">✅ 我已掌握</button>
```

点击按钮仅弹出浏览器 alert 对话框，无答题逻辑、无计分、无答案验证。整个 HTML 页面仅展示了知识点的静态标题和要点，未实现任何互动教学功能。

---

### 🟡 M4 — `parse_video` 仅获取时长，不做内容分析

**文件**：`backend/services/parser.py:220-236`  
**严重程度**：Medium（功能残缺）

**问题详述**：

函数文档声称"提取关键帧 + 音频转录"，但实际实现仅调用 `ffprobe` 获取视频时长元数据，返回字符串 `"[视频] 时长: {duration}秒"`。无关键帧提取、无音频转录、无内容理解。

---

## 二、后端路由层（Backend Routers）

**归属部门：后端开发组**  
**涉及目录：`backend/routers/`**

### 🟠 H4 — Chat 消息从未持久化到数据库

**文件**：`backend/routers/chat.py` + `backend/services/orchestrator.py:chat()`  
**严重程度**：High（数据丢失）

**问题详述**：

`ChatMessage` ORM 模型（`backend/models/session.py`）定义了完整的消息表结构（id、session_id、role、content、msg_type、created_at），但：

- `routers/chat.py` 的 SSE 端点从不对消息做入库操作
- `orchestrator.chat()` 方法从不对用户消息或 AI 响应调用 `db.add()`
- `GET /api/v1/sessions/{id}` 的 `SessionInfo` 响应模型不含 messages 字段

**影响**：用户刷新页面后对话历史全部丢失；`lock_intent` 的数据库降级路径永远查不到消息。

---

### 🟠 H5 — Feedback 端点不持久化反馈数据

**文件**：`backend/routers/generate.py:55-58`  
**严重程度**：High（功能形同虚设）

**问题详述**：

```python
@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    if not req.feedback.strip():
        raise ApiError("反馈内容不能为空", code="empty_feedback", status_code=422)
    return FeedbackResponse(task_id=req.task_id, status="feedback_received")
```

反馈内容验证非空后直接丢弃，不写入数据库、不触发重新生成、不关联任务记录。`FeedbackResponse(status="feedback_received")` 是虚假的成功响应。

---

### 🟠 H6 — Chat 端点不验证 session 是否存在

**文件**：`backend/routers/chat.py:18`  
**严重程度**：High（数据完整性）

**问题详述**：

```python
@router.post("/{session_id}/chat")
async def chat(session_id: str, req: ChatRequest):
```

`session_id` 来自 URL 路径，未做任何数据库存在性校验。用户可以向不存在的 session ID 发送聊天消息，意图分析正常执行但结果无处关联。

---

### 🟡 M5 — `list_sessions` 无分页

**文件**：`backend/routers/session.py:40-52`  
**严重程度**：Medium（性能）

**问题详述**：

`GET /api/v1/sessions` 一次性返回全部会话记录（`db.query(Session).order_by(...).all()`）。会话数增长时响应体积无界增长，且前端无对应消费页面。

---

### 🔵 L1 — `export.py` 文件名与功能不符

**文件**：`backend/routers/export.py`  
**严重程度**：Low（可维护性）

**问题详述**：

文件名是 `export.py`，实际功能是文件下载（`GET /download/{file_id}`）。下载功能应放在 `download.py` 或合并入 `generate.py`。

---

## 三、前端 API 层（Frontend API Layer）

**归属部门：前端开发组**  
**涉及目录：`src/api/`、`src/utils/`**

### 🟠 H7 — SSE confirm 事件 `data=None` 导致前端确认面板数据断裂

**文件**：`backend/services/orchestrator.py:66` vs `src/api/chat.ts:36-43`  
**严重程度**：High（数据链路断裂）

**问题详述**：

后端确认事件：
```python
yield ChatEvent(
    event_type=MessageType.CONFIRM,
    content=result.confirm_summary or _build_confirm_text(result),
    data=None,  # ← 结构化数据被丢弃
)
```

前端解析逻辑：
```typescript
base.confirmData = {
  courseName: (event.data.courseName as string) || ...,
  targetAudience: (event.data.targetAudience as string) || '',
  chapters: (event.data.chapters as Array<...>) || [],
}
```

`event.data` 为 `null` → `confirmData` 所有字段回退到空字符串/空数组 → Chat 页面的确认面板显示空白 → Preview 页面章节列表为空。

**根因**：后端 `IntentResult` 有 `teaching_goal`、`knowledge_points` 等字段，前端 `ConfirmData` 期望 `courseName`、`chapters`。两端数据结构完全不一致，且缺少映射转换层。

---

### 🟡 M6 — 前端 `EventType` 含 `done` 但后端从不发送

**文件**：`src/types/chat.ts:5` vs `backend/schemas.py:23-25`  
**严重程度**：Medium（死代码）

**问题详述**：

- 前端定义 `EventType = 'text' | 'question' | 'confirm' | 'done'`
- 后端 `MessageType` 枚举只有 `TEXT`、`QUESTION`、`CONFIRM`，无 `DONE`
- `Chat.vue` 第 297 行渲染了 `eventType === 'done'` 的分支，但该事件永远不会被触发

---

## 四、前端视图层（Frontend Views）

**归属部门：前端开发组**  
**涉及目录：`src/views/`**

### 🔴 C5（前端） — `Preview.vue` 课件生成为假实现

**文件**：`src/views/Preview.vue:28-42`  
**严重程度**：Critical（核心功能不工作）

**问题详述**：

```typescript
const handleConfirmAndGenerate = async () => {
  isGenerating.value = true
  await new Promise((resolve) => setTimeout(resolve, 2000))  // ← 只睡了 2 秒
  chatStore.setGenerationStatus('success')
  const fileId = `file_${Date.now()}`                       // ← 伪造 ID
  router.push({ name: 'Download', params: { fileId } })
}
```

该方法**从未**执行以下任何一步真实操作：
1. 调用 `POST /api/v1/generate` 触发后端异步生成任务
2. 轮询 `GET /api/v1/tasks/{task_id}/status` 获取生成进度（0-100%）
3. 使用后端返回的真实 `task_id` 和 `outputs` 数据

步骤条（`activeStep`）由前端手动递增，与后端实际的 `progress` 字段完全无关。

---

### 🔴 C6（前端） — `Download.vue` 下载按钮全部为空壳

**文件**：`src/views/Download.vue:13-15`  
**严重程度**：Critical（核心功能不工作）

**问题详述**：

```typescript
const handleDownload = (format: string) => {
  ElMessage.success(`${format} 格式课件正在打包下载，请稍候...`)
}
```

三个下载按钮（PPTX / PDF / Word 讲义）全部只调用 `ElMessage.success` 弹出一条假成功提示，**从未**执行：
- 调用 `GET /api/v1/download/{file_id}` 获取文件
- 触发浏览器文件下载（Blob / `<a>` 标签 / `window.open`）

---

### 🟠 H8 — `Chat.vue` 文件上传按钮永久禁用

**文件**：`src/views/Chat.vue:174-176`  
**严重程度**：High（功能不可达）

**问题详述**：

```html
<el-button type="primary" plain size="small" disabled>
  上传资料
</el-button>
```

- `disabled` 属性硬编码，按钮永远不可点击
- 无 `@click` 事件处理器
- 无 `<input type="file">` 文件选择器
- 无 `FormData` 构造逻辑
- `POST /api/v1/upload` 从未被前端调用

M2 模块（参考资料上传）的前端入口完全不存在。

---

### 🟠 H9 — `Chat.vue` 确认生成使用客户端伪造的 taskId

**文件**：`src/views/Chat.vue:148-157`  
**严重程度**：High（数据完整性）

**问题详述**：

```typescript
const handleConfirmGenerate = () => {
  const confirmMsg = messages.value.find((m) => m.eventType === 'confirm')
  if (confirmMsg?.confirmData) {
    chatStore.setCourseName(confirmMsg.confirmData.courseName)
    chatStore.updateOutline(confirmMsg.confirmData.chapters)
  }
  const taskId = `task_${Date.now()}`  // ← 客户端伪造，后端无此记录
  router.push({ name: 'Preview', params: { taskId } })
}
```

- `taskId` 由 `Date.now()` 在浏览器端生成，与后端数据库中的 `tasks` 表完全无关
- 从未调用 `POST /api/v1/generate` 创建真实任务
- `confirmData.chapters` 来自 SSE 事件解析（参见 H7），在 `data=None` 时为空数组

---

### 🟠 H10 — `handleEditChapter` 为空壳

**文件**：`src/views/Preview.vue:23-25`  
**严重程度**：High（功能缺失）

**问题详述**：

```typescript
const handleEditChapter = (chapterTitle: string) => {
  ElMessage.info(`「${chapterTitle}」编辑功能开发中，敬请期待`)
}
```

章节编辑功能完全未实现，仅弹提示信息。

---

### 🟡 M7 — `Chat.vue` `onMounted` 自动发送消息无去重

**文件**：`src/views/Chat.vue:34-38`  
**严重程度**：Medium（重复请求）

**问题详述**：

```typescript
if (session.subject) {
  sendToSSE(sessionId, `我要准备一堂关于${session.subject}的课`)
}
```

每次进入 Chat 页面（包括刷新）都会自动发送首条消息，无去重机制。后端 `analyze()` 也无幂等处理。

---

### 🔵 L2 — Element Plus 图标语法错误

**文件**：`src/views/Chat.vue:169`  
**严重程度**：Low（UI 显示异常）

**问题详述**：

```html
<el-icon :size="48"><i class="el-icon-upload" /></el-icon>
```

这是 Element UI（Vue 2）的旧语法。Element Plus（Vue 3）应使用 `<el-icon><Upload /></el-icon>`（从 `@element-plus/icons-vue` 导入）。当前写法会导致图标不显示。

---

### 🔵 L3 — `watch(messages, ..., { deep: true })` 性能隐患

**文件**：`src/views/Chat.vue:101`  
**严重程度**：Low（性能）

**问题详述**：

```typescript
watch(messages, scrollToBottom, { deep: true })
```

深度监听整个消息数组，每条消息的每个属性变化都触发回调。对话消息量增大后滚动性能下降。改为监听数组长度变化即可满足需求。

---

## 五、AI 模块（`ai/` Module）

**归属部门：AI/算法组**  
**涉及目录：`ai/`**

### 🟡 M8 — `_is_chinese` 函数使用了错误的比较方式

**文件**：`ai/rag/retriever.py:13-14`  
**严重程度**：Medium（潜在分词错误）

**问题详述**：

```python
def _is_chinese(ch):
    return chr(0x4e00) <= ch <= chr(0x9fff)
```

`chr()` 返回字符串，`ch` 也是字符串。Python 字符串比较使用字典序，恰好对单字符 CJK 范围有效，但写法误导。且 CJK 扩展 A 区（U+3400–U+4DBF）未被覆盖。建议使用 `ord()`：

```python
return ord(ch) >= 0x4e00 and ord(ch) <= 0x9fff
```

### 🔵 L4 — `ai/` 目录含 IDE 配置文件

**文件**：`ai/.idea/`  
**严重程度**：Low（仓库污染）

**问题详述**：

PyCharm IDE 配置目录被提交到 Git 仓库。应在 `.gitignore` 中添加 `.idea/`。

---

## 六、基础设施 / DevOps

**归属部门：DevOps / 基础设施组**  
**涉及目录：根目录、`frontend/`**

### 🔴 C7 — 存在两个相互冲突的 Vite 配置文件

**文件**：`vite.config.ts`（根目录） vs `frontend/vite.config.js`  
**严重程度**：Critical（构建不确定性）

**问题详述**：

| 项目 | 根目录 `vite.config.ts` | `frontend/vite.config.js` |
|------|------------------------|--------------------------|
| `@` alias 指向 | `src/`（根目录下） | `frontend/src/` |
| proxy target | `http://backend:8000` | `http://localhost:8000` |
| alias 语法 | CJS `__dirname` | ESM `import.meta.url` |
| 生效场景 | 从根目录 `npm run dev` | Docker / 从 `frontend/` 运行 |

Vite 根据工作目录选择配置文件，开发者无法确定哪个配置生效。`http://backend:8000` 仅在 Docker 网络内可解析；`http://localhost:8000` 仅在本地开发环境可用。

---

### 🔴 C8 — 生产环境 `docker-compose.yml` 前端容器无服务运行

**文件**：`docker-compose.yml`  
**严重程度**：Critical（生产环境无法使用）

**问题详述**：

```yaml
frontend:
  command: sleep infinity
```

前端容器设为永久睡眠，无 Web 服务器运行。该文件缺少：
- Nginx 服务来托管前端静态文件
- `npm run build` 构建步骤
- 前端到后端的网络代理
- 后端服务配置（端口映射、环境变量）

---

### 🟠 H11 — Docker 开发环境未使用 `VITE_API_TARGET` 环境变量

**文件**：`docker-compose.dev.yml:43` vs `frontend/vite.config.js:16`  
**严重程度**：High（Docker 内代理可能失败）

**问题详述**：

`docker-compose.dev.yml` 设置了环境变量：
```yaml
environment:
  - VITE_API_TARGET=http://backend:8000
```

但 `frontend/vite.config.js` 中 proxy target 是硬编码的：
```javascript
proxy: {
  '/api': {
    target: 'http://localhost:8000',  // ← 硬编码，忽略环境变量
  }
}
```

在 Docker 网络中，`localhost` 指向容器自身而非后端容器，代理请求失败。应使用 `process.env.VITE_API_TARGET` 或 Docker 服务名 `http://backend:8000`。

---

### 🔵 L5 — `nginx.conf` proxy path 与 API base URL 有潜在不匹配

**文件**：`nginx.conf`  
**严重程度**：Low（当前可用，但不健壮）

**问题详述**：

```nginx
location /api/ {
    proxy_pass http://backend:8000;
}
```

前端 `baseURL` 为 `/api/v1`，请求路径如 `/api/v1/sessions`。nginx 的 `/api/` location 匹配后，将完整路径 `/api/v1/sessions` 转发到 `http://backend:8000/api/v1/sessions`。当前恰好匹配后端 FastAPI 路由。但如果后端路由前缀变化或前端 `baseURL` 调整，静默失败。

---

### 🔵 L6 — `Dockerfile.backend` 可能缺少系统依赖

**文件**：`Dockerfile.backend`  
**严重程度**：Low（运行时错误）

**问题详述**：

`Dockerfile.backend` 安装了 `build-essential` 和 `libffi-dev`，但 `faster-whisper` 运行时还需要 `libstdc++` 等库。且 `requirements.txt` 中 `faster-whisper` 需要下载模型文件（约 1-2GB），首次请求时耗时极长。

---

## 七、数据模型 / Schema 对齐

**归属部门：全栈（前后端协作）**

### 🟠 H12 — ChatEvent data 字段与前端 Message 类型不兼容

**文件**：`backend/schemas.py`（ChatEvent） vs `src/types/chat.ts`（Message） vs `src/api/chat.ts`（chatEventToMessage）  
**严重程度**：High（数据链路断裂）

**问题详述**：

三层数据流的字段映射断裂：

| 层级 | 字段名 / 结构 |
|------|-------------|
| **后端** `ChatEvent` | `event_type`, `content`, `data: dict \| None` |
| **后端** confirm 的 `data` 内容 | `IntentResult` → `teaching_goal`, `knowledge_points[]`, `logic_flow[]` |
| **前端** `chatEventToMessage` 期望 | `event.data.courseName`, `event.data.chapters[{title, detail}]` |
| **前端** `ConfirmData` | `courseName: string`, `targetAudience: string`, `chapters: {title, detail}[]` |
| **前端** `KnowledgePoint`（后端） | `order, title, difficulty, key_points[], examples[]` |

**断裂点**：
1. 后端 confirm 事件传 `data=None`（orchestrator.py:66），前端永远拿不到结构化数据
2. 即使后端传了 `data`，字段名完全不同（`teaching_goal` vs `courseName`，`knowledge_points` vs `chapters`）
3. `knowledge_points` 的元素结构 `{order, title, difficulty, key_points, examples}` 与前端期望的 `{title, detail}` 不兼容
4. 缺少服务器端或客户端的字段映射/转换层

---

### 🟡 M9 — `GET /api/v1/sessions/{id}` 响应不含消息列表

**文件**：`backend/routers/session.py:55-66` vs `src/api/session.ts:14-19`  
**严重程度**：Medium（前后端期望不一致）

**问题详述**：

- 后端 `SessionInfo` 响应：`{ session_id, teacher_name, subject, status, created_at }`
- 前端 `SessionDetail` 类型：`{ sessionId, teacherName, subject, status, messages: Message[] }`
- 后端从不查询 `chat_messages` 表来填充消息列表
- 即使消息被持久化（参见 H4），`getSession` 也不会返回它们

---

## 附录：严重程度定义

| 级别 | 标识 | 定义 |
|------|------|------|
| **Critical** | 🔴 | 应用无法启动 / 启动即崩溃 / 核心功能完全不工作 |
| **High** | 🟠 | 核心功能在特定条件下失效 / 数据丢失 / 数据链路断裂 |
| **Medium** | 🟡 | 功能残缺 / 性能隐患 / 前后端不一致 |
| **Low** | 🔵 | 代码异味 / 文档错误 / 可维护性问题 |

---

## 问题统计

| 部门 | Critical | High | Medium | Low | 合计 |
|------|----------|------|--------|-----|------|
| 后端服务层 | 5 | 3 | 4 | 0 | **12** |
| 后端路由层 | 0 | 3 | 1 | 1 | **5** |
| 前端 API 层 | 0 | 1 | 1 | 0 | **2** |
| 前端视图层 | 2 | 4 | 1 | 2 | **9** |
| AI 模块 | 0 | 0 | 1 | 1 | **2** |
| DevOps | 2 | 1 | 0 | 2 | **5** |
| 数据模型 | 0 | 1 | 1 | 0 | **2** |
| **总计** | **9** | **13** | **9** | **6** | **28** |

---

> **下一步行动建议**：优先修复 Critical 级别问题（C1-C8），这些是阻塞应用启动和核心流程的硬错误。其次修复 High 级别的数据链路断裂问题（H4-H12），这些导致功能虽然不崩溃但无实际效果。
