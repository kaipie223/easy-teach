# Easy-Teach Backend API Contract

This document describes the M0, M1 and M2 API that is implemented in `backend/`.
The canonical API prefix is `/api/v1`. The root health endpoints are outside
that prefix.

## Common Rules

- JSON endpoints use `application/json`.
- Upload endpoints use `multipart/form-data`.
- Every response includes `X-Request-ID`.
- Authenticated application endpoints require `Authorization: Bearer <access_token>`.
- The legacy session endpoints allow anonymous requests for local demos. When
  a token is present, sessions, files, tasks and messages are filtered by the
  authenticated user (administrators can inspect all users' resources).
- Failed responses use the following envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": [],
    "request_id": "uuid",
    "recoverable": true,
    "suggested_action": "请修正请求字段后重试"
  }
}
```

## Health

### `GET /`

Returns the application identity and `status: "running"`.

### `GET /health`

Returns API, database, Redis and Chroma status. Chroma uses
`status: "not_indexed"` when the persistence directory exists but the
`knowledge_base` collection has not been built yet.

```json
{
  "app": "easy-teach",
  "version": "0.1.0",
  "environment": "development",
  "status": "degraded",
  "checks": {
    "database": {"status": "ok", "engine": "sqlite"},
    "redis": {"status": "error", "message": "localhost:6379 unavailable"},
    "chroma": {"status": "not_indexed", "path": "..."}
  }
}
```

## Authentication

### `POST /api/v1/auth/register`

Request:

```json
{"email": "teacher@example.com", "password": "password123", "display_name": "张老师"}
```

Returns `201` with `access_token`, `token_type`, `expires_in` and the created
user. Passwords are stored as Argon2 hashes; the API never returns the hash.

### `POST /api/v1/auth/login`

Accepts `email` and `password`, returning the same token response as register.

### `GET /api/v1/auth/me`

Returns the current user. This endpoint returns `401` when the bearer token is
missing, expired or invalid.

## Projects

All project endpoints require authentication. Projects are owned by the
current user and use a soft-delete lifecycle.

### `GET /api/v1/projects?include_deleted=false`

Returns the current user's projects, newest updated first. Administrators can
see all projects.

### `POST /api/v1/projects`

Request:

```json
{"title": "Python 入门", "scenario": "大一新生"}
```

Returns `201` with a `ProjectInfo` object.

### `GET /api/v1/projects/{project_id}`

Returns a project owned by the current user. Use `include_deleted=true` to
inspect a deleted project before restoring it.

### `PATCH /api/v1/projects/{project_id}`

Accepts either `title` or `scenario` and returns the updated project.

### `DELETE /api/v1/projects/{project_id}`

Marks the project as deleted and returns the archived project. Data and files
remain available for recovery.

### `POST /api/v1/projects/{project_id}/restore`

Clears the soft-delete marker and returns the active project.

## Sessions

### `POST /api/v1/sessions`

Request:

```json
{"teacher_name": "张老师", "subject": "Python 入门", "project_id": "p_12345678"}
```

`project_id` is optional for anonymous M0 compatibility and required when a
session is being attached to an authenticated project. Returns `201` with a
`SessionInfo` object containing `session_id`, ownership fields and persisted
`messages`.

### `GET /api/v1/sessions`

Returns the newest sessions first.

### `GET /api/v1/sessions/{session_id}`

Returns one session, its persisted messages, or `SESSION_NOT_FOUND` when the
session is outside the caller's ownership scope.

## Chat SSE

### `POST /api/v1/sessions/{session_id}/chat`

Request:

```json
{"message": "帮我设计一节 TCP 三次握手课程"}
```

The response is `text/event-stream`. Each event has an explicit event name and
one JSON data line:

```text
event: question
data: {"event_type":"question","content":"请补充授课对象","data":{"prompt":"请补充授课对象","options":[],"allow_free":true}}

event: confirm
data: {"event_type":"confirm","content":"...","data":{"fields":{},"note":"..."}}
```

Supported event names are `text`, `question` and `confirm`.

### `POST /api/v1/projects/{project_id}/messages`

This is the authenticated project workflow equivalent of the legacy session
chat endpoint. It creates a project session on first use when necessary and
returns the same `text/event-stream` event contract. The user message and every
assistant event are persisted in `chat_messages`; structured `question` and
`confirm` payloads are available as `event_data` when the session is fetched.

## TeachingBrief

TeachingBrief is the structured, editable requirement baseline for a project.
The latest draft is returned by the following endpoints, and all of them
require the project owner's bearer token (or an administrator token).

### `GET /api/v1/projects/{project_id}/brief`

Returns the latest draft or confirmed version. A project without a chat-derived
brief returns `404` with code `BRIEF_NOT_FOUND`.

### `PATCH /api/v1/projects/{project_id}/brief`

Accepts any subset of the editable TeachingBrief fields, including
`teaching_goal`, `target_audience`, `duration_minutes`, `knowledge_points`,
`logic_flow`, `teaching_focus`, `teaching_difficulties`, `output_types`, and
the optional interaction/style/constraint fields. Fields edited through this
endpoint are marked with `source_refs: {"field": "teacher"}`.

Editing a confirmed version creates a new draft version and leaves the
confirmed snapshot unchanged. Editing an existing draft updates that draft.

### `POST /api/v1/projects/{project_id}/brief/confirm`

Request body:

```json
{"expected_version": 2}
```

`expected_version` is optional; when supplied it provides optimistic-locking
protection. Confirmation returns `422` with code `BRIEF_INCOMPLETE` and a
`missing_fields` list until all required fields are present. A stale version
returns `409` with code `BRIEF_VERSION_CONFLICT`. A successful confirmation
marks that immutable version as `confirmed`, locks the session intent state,
and records it as the project's current version.

## Materials

### `POST /api/v1/upload`

Multipart fields:

- `file`: PDF, DOCX, PPTX, image or video.
- `session_id`: owning session ID.
- `ref_description`: optional teacher note.

Returns `201` with `FileInfo`. The server stores files under a random file ID
directory and enforces `MAX_UPLOAD_SIZE_MB`.

### `GET /api/v1/files/{file_id}`

Returns metadata for an uploaded or generated file.

## Speech

### `POST /api/v1/speech/transcribe`

Multipart fields: `audio` and optional `session_id`. Returns `text`,
`duration_seconds` and the supplied `session_id`. The frontend sends returned
transcript text through the same chat SSE pipeline; transcription itself does
not create a second message record.

## Generation

### `POST /api/v1/generate`

Request:

```json
{"session_id": "s_12345678"}
```

Returns `202` and a task with one of the statuses `pending`, `processing`,
`completed` or `failed`.

### `GET /api/v1/tasks/{task_id}/status`

Returns the current task status and generated output metadata.

### `POST /api/v1/feedback`

Request:

```json
{"task_id": "task_12345678", "feedback": "简化第 3 页"}
```

## Downloads

### `GET /api/v1/download/{file_id}`

Returns the file bytes after the file record and path have been verified.

## Administration

### `GET /api/v1/admin/users`

Returns the user list for administrators. A valid non-admin token receives
`403` and an unauthenticated request receives `401`.

## Compatibility Note

The session-oriented endpoints are the M0 transport contract. The authenticated
project-oriented `/projects/{id}` routes are the M1/M2 application contract.
Existing anonymous session requests remain available only as a local/demo
compatibility path; authenticated application flows should create a project
first and attach the session with `project_id`.
