"""批量把教材 PDF 导入教师私人知识库，最后统一建一次索引。

为什么要有这个脚本（而不是在页面上一个个上传）：

1. 页面单文件上限 50 MB、个人配额默认 1 GB，整架教材会被挡在门外；
2. 页面上传/索引是一本一本来的，整架教材点起来太慢；这个脚本批量导入后一次建库；
3. 页面上看不到「这本书抽出了多少字」——整本扫描件会表现为"导入成功、0 个块"。

用法（在仓库根目录执行）：

    # 1) 体检：只解析，不写库、不嵌入。报出每本页数/抽字率/块数
    .venv\\Scripts\\python.exe scripts/import_knowledge.py probe --limit 1
    .venv\\Scripts\\python.exe scripts/import_knowledge.py probe

    # 2) 先导一本并建索引，量出本机每秒能嵌入多少块（用来估总时长）
    .venv\\Scripts\\python.exe scripts/import_knowledge.py import --owner-email xxx --limit 1
    .venv\\Scripts\\python.exe scripts/import_knowledge.py index  --owner-email xxx

    # 3) 全量导入 + 建索引（建议后台跑，日志重定向到文件）
    .venv\\Scripts\\python.exe scripts/import_knowledge.py import --owner-email xxx
    .venv\\Scripts\\python.exe scripts/import_knowledge.py index  --owner-email xxx

参数：--source --match --limit --collection --threads --report --owner-email
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import settings  # noqa: E402
from backend.services.materials import parse_material  # noqa: E402

DEFAULT_SOURCE = REPO_ROOT / "knowledge-base" / "教材PDF"
DEFAULT_COLLECTION = "计算机专业教材"


def _log(message: str) -> None:
    print(message, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _human(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} 分 {rest} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


SOURCE_SUFFIXES = {".pdf", ".txt", ".md"}


def file_type_of(path: Path) -> str:
    """按扩展名给出解析类型：扫描件 OCR 出来的 .txt 走 text 解析（带页码标记）。"""
    return "text" if path.suffix.lower() in {".txt", ".md"} else "pdf"


def list_sources(source: Path, match: str | None, limit: int | None) -> list[Path]:
    """可导入的来源：教材 PDF 与 OCR 文本（scripts/ocr_textbooks.py 的产物）。"""
    files = sorted(
        path
        for path in source.iterdir()
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
    )
    if match:
        files = [path for path in files if match.lower() in path.name.lower()]
    if limit:
        files = files[:limit]
    return files


# ── 体检：只解析，不写库、不嵌入 ──────────────────────────────────────────────


def probe(source: Path, files: list[Path], report: Path | None) -> list[dict[str, Any]]:
    _log(
        f"体检：{len(files)} 个文件，分块上限 {settings.knowledge_chunk_chars} 字符"
        f"（向量模型 {settings.embedding_model} 的窗口约 512 token）"
    )
    _log("")
    _log(f"{'文件':<46}{'大小MB':>8}{'页数':>7}{'有字页':>8}{'抽字率':>8}{'块数':>7}{'解析秒':>8}")
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for path in files:
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb == 0:
            _log(f"{path.name[:44]:<46}{'0':>8}{'-':>7}{'-':>8}{'-':>8}{'-':>7}{'-':>8}  ← 空文件")
            rows.append({"file": path.name, "size_mb": 0.0, "error": "empty file"})
            continue
        began = time.perf_counter()
        try:
            parsed = parse_material(
                file_type_of(path), path, max_chars=settings.knowledge_chunk_chars
            )
        except Exception as exc:  # 单本失败不该带走整批
            _log(f"{path.name[:44]:<46}{size_mb:>8.1f}  解析失败：{exc}")
            rows.append({"file": path.name, "size_mb": round(size_mb, 2), "error": str(exc)})
            continue
        elapsed = time.perf_counter() - began
        meta = parsed.result_json or {}
        pages = int(meta.get("page_count") or 0)
        text_pages = int(meta.get("text_pages") or 0)
        coverage = (text_pages / pages) if pages else 0.0
        row = {
            "file": path.name,
            "size_mb": round(size_mb, 2),
            "pages": pages,
            "text_pages": text_pages,
            "coverage": round(coverage, 4),
            "chars": len(parsed.text_content or ""),
            "chunks": len(parsed.chunks),
            "parse_seconds": round(elapsed, 2),
        }
        rows.append(row)
        flag = ""
        if coverage and coverage < 0.9:
            flag = "  ← 抽字率偏低，逐页确认"
        elif coverage == 0:
            flag = "  ← 一页都没抽出字，需 OCR"
        _log(
            f"{path.name[:44]:<46}{size_mb:>8.1f}{pages:>7}{text_pages:>8}"
            f"{coverage * 100:>7.0f}%{len(parsed.chunks):>7}{elapsed:>8.2f}{flag}"
        )

    elapsed = time.perf_counter() - started
    ok = [row for row in rows if "error" not in row]
    pages = sum(int(row["pages"]) for row in ok)
    chunks = sum(int(row["chunks"]) for row in ok)
    chars = sum(int(row["chars"]) for row in ok)
    _log("")
    _log("─" * 88)
    _log(f"合计：{len(ok)} 本可用，{pages} 页，{chars / 10000:.1f} 万字，{chunks} 个知识块")
    _log(f"解析总耗时 {_human(elapsed)}（这步不写库、不嵌入）")
    _log("")
    _log("向量化时长预估（按每秒嵌入块数）：")
    for rate in (20, 40, 80, 150):
        _log(f"  {rate:>4} 块/秒 → {_human(chunks / rate)}")
    _log("真实速率由 import + index 实测得出（先导一本再 index 即可量出）。")

    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": str(source),
                    "chunk_chars": settings.knowledge_chunk_chars,
                    "totals": {
                        "books": len(ok),
                        "pages": pages,
                        "chars": chars,
                        "chunks": chunks,
                        "probe_seconds": round(elapsed, 1),
                    },
                    "books": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _log(f"体检结果已写入 {report}")
    return rows


# ── 导入 / 建索引 ────────────────────────────────────────────────────────────


def resolve_owner(db, email: str):
    from backend.models.user import User

    teachers = (
        db.query(User)
        .filter(User.role == "teacher")
        .order_by(User.created_at.desc())
        .all()
    )
    if email:
        found = next((item for item in teachers if item.email == email), None)
        if found is None:
            raise SystemExit(f"找不到教师账号：{email}")
        return found
    if not teachers:
        raise SystemExit("库里没有教师账号，请先注册一个教师账号")
    _log("未指定 --owner-email，可选的教师账号：")
    for item in teachers:
        _log(f"  {item.email:<32}{item.display_name or '（未命名）':<12}{item.user_id}")
    raise SystemExit("请用 --owner-email 指定导入到哪个账号")


def import_books(
    *,
    source: Path,
    files: list[Path],
    owner_email: str,
    collection: str,
    report: Path | None,
    min_coverage: float = 0.9,
) -> None:
    from backend.db.database import SessionLocal
    from backend.models.knowledge import KnowledgeDocument
    from backend.services.knowledge import (
        KnowledgeValidationError,
        import_document,
    )
    from backend.services.limits import current_storage_bytes

    db = SessionLocal()
    try:
        owner = resolve_owner(db, owner_email)
        quota_bytes = settings.storage_quota_mb_per_user * 1024 * 1024
        used_before = current_storage_bytes(db, owner.user_id)
        _log("")
        _log(f"导入到：{owner.email}（{owner.display_name or '未命名'}）")
        _log(f"存储：已用 {used_before / 1024 / 1024:.0f} MB / 配额 {quota_bytes / 1024 / 1024:.0f} MB")
        if used_before + sum(path.stat().st_size for path in files) > quota_bytes:
            _log("")
            _log("⚠ 这些书会把个人配额撑爆。配额只管生成与存储，不影响导入本身，但配额满了")
            _log("  之后生成课件会被拒绝（STORAGE_QUOTA_EXCEEDED）。请在 .env 里调高：")
            _log("  STORAGE_QUOTA_MB_PER_USER=8192")
        _log("")

        results: list[dict[str, Any]] = []
        started = time.perf_counter()
        for index, path in enumerate(files, start=1):
            size_bytes = path.stat().st_size
            prefix = f"[{index}/{len(files)}] {path.name[:44]:<46}"
            if size_bytes == 0:
                _log(f"{prefix} 跳过：0 字节空文件")
                results.append({"file": path.name, "status": "skipped", "reason": "empty file"})
                continue

            staged_dir = settings.data_dir / ".upload_tmp" / f"kb_bulk_{uuid.uuid4().hex}"
            staged_dir.mkdir(parents=True, exist_ok=True)
            staged_path = staged_dir / path.name
            began = time.perf_counter()
            document: KnowledgeDocument | None = None

            # 先体检再入库：纯图片扫描件能"导入成功"却贡献 0 个块，界面上一切正常、
            # 检索永远空手。抽字率不达标就挡在门外，并把它列进待 OCR 清单。
            try:
                sample = parse_material(
                    file_type_of(path), path, max_chars=settings.knowledge_chunk_chars
                )
            except Exception as exc:
                _log(f"{prefix} 解析失败：{exc}")
                results.append({"file": path.name, "status": "failed", "reason": str(exc)})
                shutil.rmtree(staged_dir, ignore_errors=True)
                continue
            sample_meta = sample.result_json or {}
            pages = int(sample_meta.get("page_count") or 0)
            text_pages = int(sample_meta.get("text_pages") or 0)
            coverage = (text_pages / pages) if pages else 0.0
            if coverage < min_coverage or not sample.chunks:
                _log(
                    f"{prefix} 跳过：抽字率 {coverage * 100:.0f}%（{text_pages}/{pages} 页有字）"
                    f"，需 OCR 后重新导入"
                )
                results.append(
                    {
                        "file": path.name,
                        "status": "needs_ocr",
                        "pages": pages,
                        "text_pages": text_pages,
                        "coverage": round(coverage, 4),
                    }
                )
                shutil.rmtree(staged_dir, ignore_errors=True)
                continue

            try:
                # 复制而不是移动：原件留在 knowledge-base/教材PDF 里
                shutil.copy2(path, staged_path)
                document = import_document(
                    db,
                    owner_id=owner.user_id,
                    collection_id=collection,
                    title=path.stem,
                    filename=path.name,
                    staged_path=staged_path,
                    size_bytes=size_bytes,
                    checksum=_sha256(staged_path),
                    enabled=True,
                )
            except KnowledgeValidationError as exc:
                if exc.code == "KNOWLEDGE_DOCUMENT_EXISTS":
                    _log(f"{prefix} 已存在，跳过")
                    results.append({"file": path.name, "status": "exists"})
                else:
                    _log(f"{prefix} 失败：{exc}（{exc.code}）")
                    results.append({"file": path.name, "status": "failed", "reason": str(exc)})
            except Exception as exc:  # 单本失败不影响后面
                _log(f"{prefix} 异常：{exc}")
                results.append({"file": path.name, "status": "failed", "reason": str(exc)})
            finally:
                shutil.rmtree(staged_dir, ignore_errors=True)

            elapsed = time.perf_counter() - began
            if document is not None:
                meta = document.metadata_json or {}
                chunks = int(meta.get("chunk_count") or 0)
                parse_state = document.index_status
                _log(f"{prefix} {chunks:>6} 块  {elapsed:>6.1f} 秒  解析状态={parse_state}")
                results.append(
                    {
                        "file": path.name,
                        "status": "imported",
                        "document_id": document.document_id,
                        "chunks": chunks,
                        "pages": int(meta.get("page_count") or 0),
                        "text_pages": int(meta.get("text_pages") or 0),
                        "parse_seconds": round(elapsed, 2),
                        "index_status": parse_state,
                    }
                )
            if index < len(files):
                done = time.perf_counter() - started
                _log(f"          已用 {_human(done)}，预计还需 {_human(done / index * (len(files) - index))}")

        used_after = current_storage_bytes(db, owner.user_id)
        imported = [row for row in results if row["status"] == "imported"]
        needs_ocr = [row for row in results if row["status"] == "needs_ocr"]
        chunks = sum(int(row["chunks"]) for row in imported)
        _log("")
        _log("─" * 88)
        _log(
            f"导入完成：{len(imported)} 本进库，{chunks} 个知识块，"
            f"总耗时 {_human(time.perf_counter() - started)}"
        )
        if needs_ocr:
            ocr_pages = sum(int(row["pages"]) for row in needs_ocr)
            _log("")
            _log(f"待 OCR 的 {len(needs_ocr)} 本（约 {ocr_pages} 页，{min_coverage * 100:.0f}% 以下不入库）：")
            for row in sorted(needs_ocr, key=lambda item: item["pages"], reverse=True):
                _log(
                    f"  {row['file'][:44]:<46}{row['pages']:>6} 页  "
                    f"抽字率 {row['coverage'] * 100:.0f}%"
                )
        _log(
            f"存储：{used_after / 1024 / 1024:.0f} MB（导入前 {used_before / 1024 / 1024:.0f} MB）"
            f" / 配额 {quota_bytes / 1024 / 1024:.0f} MB"
        )
        if used_after > quota_bytes:
            _log("⚠ 已超出个人配额，生成课件会被拒绝。请调高 STORAGE_QUOTA_MB_PER_USER。")
        _log("")
        _log("下一步：建索引（一次即可，全量重建本账号所有已启用文档）")
        _log(f"  .venv\\Scripts\\python.exe scripts/import_knowledge.py index --owner-email {owner.email}")

        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "books": results},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _log(f"导入结果已写入 {report}")
    finally:
        db.close()


def run_index(*, owner_email: str, collection: str, report: Path | None) -> None:
    from backend.db.database import SessionLocal
    from backend.models.knowledge import KnowledgeDocument
    from backend.services.knowledge import KnowledgeIndexError, rebuild_index

    db = SessionLocal()
    try:
        owner = resolve_owner(db, owner_email)
        enabled = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.owner_id == owner.user_id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.enabled.is_(True),
            )
            .count()
        )
        _log(f"为 {owner.email} 重建索引：{enabled} 本已启用文档，线程数 {settings.embedding_threads}")
        _log("（这一步是真正花时间的：全量重新向量化，进度形如 [embedder] 100/20000 块已写入）")
        _log("")
        started = time.perf_counter()
        try:
            # force=True：脚本要的就是一份干净的基准索引（这个脚本自己的说明书也是
            # "全量"），不想让增量判定把它悄悄变成"只补几本"。
            result = rebuild_index(db, owner_id=owner.user_id, force=True)
        except KnowledgeIndexError as exc:
            _log(f"建索引失败：{exc} / {exc.details}")
            raise SystemExit(1) from exc
        elapsed = time.perf_counter() - started
        chunks = int(result.get("chunk_count") or 0)
        rate = (chunks / elapsed) if elapsed else 0.0
        _log("")
        _log("─" * 88)
        _log(
            f"索引完成：{result.get('status')}，{len(result.get('indexed_document_ids') or [])} 本，"
            f"{chunks} 个块，耗时 {_human(elapsed)}，约 {rate:.1f} 块/秒"
        )
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps(
                    {
                        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "collection": collection,
                        "chunk_count": chunks,
                        "seconds": round(elapsed, 1),
                        "chunks_per_second": round(rate, 1),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _log(f"索引结果已写入 {report}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="批量导入教材 PDF 到教师私人知识库")
    parser.add_argument("action", choices=("probe", "import", "index"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--match", default=None, help="只处理文件名包含该子串的书")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 本（校准用）")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="集合名（只用于分类展示）")
    parser.add_argument("--owner-email", default=None, help="导入/建索引到哪个教师账号")
    parser.add_argument("--threads", type=int, default=None, help="本次运行的嵌入线程数（1-8）")
    parser.add_argument("--report", type=Path, default=None, help="把结果写到 JSON")
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.9,
        help="抽字率低于该值视为扫描件、不入库（默认 0.9；想强行导入就传 0）",
    )
    args = parser.parse_args()

    if args.threads:
        settings.embedding_threads = max(1, min(8, args.threads))
        _log(f"本次运行嵌入线程数设为 {settings.embedding_threads}")

    if args.action == "index":
        if not args.owner_email:
            raise SystemExit("index 需要 --owner-email")
        run_index(owner_email=args.owner_email, collection=args.collection, report=args.report)
        return

    source = args.source
    if not source.is_dir():
        raise SystemExit(f"目录不存在：{source}")
    files = list_sources(source, args.match, args.limit)
    if not files:
        raise SystemExit(f"没有匹配到 PDF：{source}（--match={args.match}）")

    if args.action == "probe":
        probe(source, files, args.report)
        return

    if not args.owner_email:
        raise SystemExit("import 需要 --owner-email")
    import_books(
        source=source,
        files=files,
        owner_email=args.owner_email,
        collection=args.collection,
        report=args.report,
        min_coverage=args.min_coverage,
    )


if __name__ == "__main__":
    main()
