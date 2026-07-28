# Backend API Contract

Base URL: `/api`

## Common rules

- Requests use JSON unless the endpoint is multipart upload.
- Successful responses are JSON unless the endpoint returns a file.
- Failed responses follow:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": [],
    "request_id": "uuid"
  }
}
```

## Health

### `GET /`
### `GET /health`

Response:

```json
{
  "app": "easy-teach",
  "version": "0.1.0",
  "environment": "development",
  "status": "ok"
}
```

## Chat

### `POST /chat/send`

Request:

```json
{
  "session_id": "session_123",
  "message": "帮我生成一节初中数学课",
  "history": [
    { "role": "user", "content": "..." }
  ]
}
```

Response:

```json
{
  "session_id": "session_123",
  "reply": "已收到消息，正在分析教学意图。",
  "intent": null
}
```

## Upload

### `POST /upload/file`

Multipart field: `file`

Response:

```json
{
  "file_id": "uuid",
  "file_name": "lesson.pdf",
  "status": "accepted"
}
```

## Speech

### `POST /speech/transcribe`

Multipart field: `audio`

Response:

```json
{
  "text": "语音转文字结果尚未接入，当前返回占位文本。",
  "duration_seconds": 0
}
```

## Generate

### `POST /generate/start`

Request:

```json
{
  "session_id": "session_123",
  "intent": {
    "subject": "数学",
    "grade": "初中",
    "topic": "一元一次方程",
    "keywords": ["方程", "解法"],
    "lesson_type": "新课",
    "style": "互动",
    "confidence": 0.82,
    "missing_info": []
  },
  "references": ["file_1"],
  "rag_docs": ["doc_1"],
  "extra_instructions": "多加练习题"
}
```

Response:

```json
{
  "task_id": "uuid",
  "status": "pending",
  "created_at": "2026-07-28T00:00:00Z",
  "pptx_url": null,
  "docx_url": null,
  "html_url": null
}
```

### `GET /generate/status/{task_id}`

Response: same shape as `GenerateTask`.

### `POST /generate/feedback`

Request:

```json
{
  "task_id": "uuid",
  "feedback": "增加例题解释"
}
```

Response:

```json
{
  "task_id": "uuid",
  "status": "feedback_received"
}
```

## Export

### `GET /export/download/{file_id}`

Current behavior: returns a `404` error envelope until generated files are available.

## Enums

- `Subject`: 语文, 数学, 英语, 物理, 化学, 生物, 历史, 地理, 政治
- `Grade`: 小学, 初中, 高中
- `TaskStatus`: pending, processing, completed, failed
