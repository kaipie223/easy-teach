# Easy-Teach Backend API Contract

This document describes the M0 through M5 API that is implemented in `backend/`.
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

## Courseware plans and generation

M4 compiles a confirmed TeachingBrief into one versioned `CoursewarePlan`. The
plan contains `SlideSpec`, `LessonPlanSectionSpec`, `InteractionSpec` and
locatable `EvidenceRef` entries. PPTX, DOCX and HTML output all consume this
same plan JSON; they do not independently rebuild the teaching structure.

### `POST /api/v1/projects/{project_id}/plan`

Requires a confirmed TeachingBrief. The optional request body is:

```json
{"force_rebuild": false}
```

Returns `201` with the latest or newly compiled `CoursewarePlan`. Rebuilding
after a new confirmed brief creates the next immutable plan version. A plan
includes stable IDs such as `slide_001`, lesson section durations, interaction
items and source locators such as PDF page numbers.

### `GET /api/v1/projects/{project_id}/plan`

Returns the newest plan version. A project without a plan returns
`PLAN_NOT_FOUND`.

### `POST /api/v1/projects/{project_id}/generate`

Request body is optional; when supplied it can select a plan version:

```json
{"plan_id": "plan_12345678"}
```

Returns `202` with a task. The task contains `plan_id` and eventually three
outputs: `pptx`, `docx` and `html`. Use the existing task status and download
endpoints to poll and retrieve them. Local deterministic rendering is used
when no external model key is configured; an external LLM is not required to
validate the blueprint-to-artifact contract.

## Revisions and immutable artifact versions

The first project generation creates an immutable `ArtifactVersion` snapshot
bound to the selected `CoursewarePlan`. Every successful edit or restore
creates a new snapshot; old versions are never overwritten or deleted.

### `GET /api/v1/projects/{project_id}/versions`

Returns versions newest first. Each item includes `artifact_version_id`,
`version`, `base_version_id`, `summary` and the complete validated
`CoursewarePlan` snapshot.

### `POST /api/v1/projects/{project_id}/revisions/interpret`

Creates a preview `RevisionPatch` without modifying the base version.

Request:

```json
{
  "instruction": "简化第 3 页",
  "base_version_id": "av_12345678"
}
```

The local deterministic interpreter currently maps page title changes,
simplify/expand/case edits, page deletion and page movement. The response
lists stable target IDs, allowed operations, cascade checks and whether
confirmation is required. Unsupported or ambiguous instructions return a
structured error rather than guessing a target.

### `POST /api/v1/projects/{project_id}/revisions/apply`

Request:

```json
{"patch_id": "patch_12345678", "confirmed": true}
```

Applies a preview against its exact `base_version_id` and creates the next
version. If the project has advanced, the API returns `VERSION_CONFLICT` and
leaves the old patch and versions unchanged.

### `POST /api/v1/projects/{project_id}/versions/{version_id}/restore`

Copies the selected snapshot into a new current version. Restoring v1 after
v2 therefore creates v3; v1 and v2 remain available.

## Version-bound exports

### `GET /api/v1/projects/{project_id}/exports`

Lists export records, optionally filtered by `artifact_version_id`.

### `POST /api/v1/projects/{project_id}/exports`

Creates idempotent export records for `pptx`, `docx` and/or `html`:

```json
{
  "artifact_version_id": "av_12345678",
  "formats": ["pptx", "docx", "html"],
  "force": false
}
```

Each record is tied to one immutable version and exposes `pending`,
`processing`, `completed` or `failed` status. Repeating the same request
returns an existing active/completed record unless `force` is true.

### `GET /api/v1/exports/{export_id}`

Returns one export status and its checksum/filename when completed.

### `GET /api/v1/exports/{export_id}/download`

Downloads the file only after the version-bound export is completed and the
requester passes the project ownership check. Export filenames include the
course title, artifact version and export date.

## Materials

### `POST /api/v1/upload`

Multipart fields:

- `file`: PDF, DOCX, PPTX, image or video.
- `session_id`: owning session ID.
- `ref_description`: optional teacher note.

Returns `201` with `FileInfo`. The server stores files under a random file ID
directory and enforces `MAX_UPLOAD_SIZE_MB`.

### Project materials

The current project workflow uses the following authenticated endpoints:

```text
GET    /api/v1/projects/{project_id}/materials
POST   /api/v1/projects/{project_id}/materials
GET    /api/v1/materials/{material_id}
GET    /api/v1/materials/{material_id}/analysis
GET    /api/v1/materials/{material_id}/evidence
GET    /api/v1/materials/{material_id}/bindings
PUT    /api/v1/materials/{material_id}/bindings
GET    /api/v1/materials/{material_id}/download
DELETE /api/v1/materials/{material_id}
```

Uploads are parsed synchronously with page, slide, paragraph or image metadata
stored in `material_analyses` and `evidence_chunks`. The material delete route
is a soft delete: active bindings and evidence are invalidated, while the
stored file remains isolated on disk. Video uploads return
`VIDEO_PARSING_DEFERRED` until the later video milestone.

The binding payload accepts multiple `usage_type` values, including
`content_basis`, `knowledge_structure`, `case_source`, `visual_style`,
`interaction_asset` and `archive_only`. Each binding also has a
`target_type` scope.

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
{"session_id": "s_12345678", "idempotency_key": "teacher-req-001"}
```

Returns `202` and a task with one of the statuses `pending`, `processing`,
`completed` or `failed`. Repeating the request with the same authenticated
user and `idempotency_key` returns the original task instead of creating a
second generation job.

### `GET /api/v1/tasks/{task_id}/status`

Returns the current task status and generated output metadata.

The response also exposes `retry_count`, `max_retries`, `error_code`,
`started_at`, `updated_at` and `completed_at`. Generation is dispatched to
Celery through Redis; the API process never executes the long-running render.
The worker retries transient failures up to `TASK_MAX_RETRIES`, applies the
configured soft/hard time limits, and the periodic stale-job recovery task
requeues jobs whose heartbeat lease has expired.

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

## Knowledge base

Knowledge-base writes require an administrator token. Teachers can list only
enabled, indexed documents and can use the search endpoint.

```text
GET    /api/v1/knowledge/documents
POST   /api/v1/knowledge/documents              # multipart import
PATCH  /api/v1/knowledge/documents/{id}        # title/enabled
DELETE /api/v1/knowledge/documents/{id}        # soft delete
POST   /api/v1/knowledge/index                  # rebuild enabled docs
POST   /api/v1/knowledge/documents/{id}/index   # rebuild and verify one doc
POST   /api/v1/knowledge/search                 # authenticated retrieval
```

Import persists the document and immutable `evidence_chunks` first. Indexing
rebuilds the `knowledge_base` Chroma collection from enabled, valid evidence
and preserves `evidence_id`, document ID and locator metadata in retrieval
results. A model download or embedding failure marks affected documents as
`failed`; the same index endpoint can be retried after the model service is
available. The current implementation is a synchronous request with a long
client timeout; Celery progress reporting remains a later task milestone.

## M6 quality and queue contract

Every project artifact version stores a deterministic quality report with
`quality_status` (`pending`, `passed`, `warning` or `failed`). Blocking
structural issues fail generation; warnings such as missing evidence links do
not discard the artifact and remain visible in the version response.

Celery task names are `easy_teach.generate`, `easy_teach.export` and
`easy_teach.recover_stale_jobs`. The worker uses an independent SQLAlchemy
session, late acknowledgements and a single-job prefetch to keep task state
recoverable after a worker restart.

## Compatibility Note

The session-oriented endpoints are the M0 transport contract. The authenticated
project-oriented `/projects/{id}` routes are the M1/M2 application contract.
Existing anonymous session requests remain available only as a local/demo
compatibility path; authenticated application flows should create a project
first and attach the session with `project_id`.
