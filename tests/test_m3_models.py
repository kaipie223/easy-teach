import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.models import (
    EvidenceChunk,
    KnowledgeDocument,
    Material,
    Project,
    Session as LessonSession,
    User,
)


def test_m3_tables_are_registered():
    assert {
        "materials",
        "material_analyses",
        "material_bindings",
        "evidence_chunks",
        "knowledge_documents",
    }.issubset(Base.metadata.tables)


def test_evidence_requires_exactly_one_source():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        user = User(email="schema@example.com", display_name="Schema", password_hash="hash")
        project = Project(owner_id="u_schema", title="Schema test", scenario="")
        user.user_id = "u_schema"
        project.project_id = "p_schema"
        lesson_session = LessonSession(session_id="s_schema", user_id=user.user_id, project_id=project.project_id)
        material = Material(
            material_id="mat_schema",
            owner_id=user.user_id,
            project_id=project.project_id,
            session_id=lesson_session.session_id,
            original_name="lesson.pdf",
            file_type="pdf",
            stored_path="data/uploads/mat_schema/lesson.pdf",
            size_bytes=1,
            checksum_sha256="a" * 64,
        )
        knowledge = KnowledgeDocument(
            document_id="kb_schema",
            collection_id="default",
            title="Knowledge",
            source_path="knowledge-base/lesson.pdf",
            file_type="pdf",
            checksum_sha256="b" * 64,
        )
        db.add_all([user, project, lesson_session, material, knowledge])
        db.commit()

        valid = EvidenceChunk(
            evidence_id="evidence_valid",
            material_id=material.material_id,
            source_type="uploaded_pdf",
            text="A source fragment",
            content_hash="c" * 64,
        )
        db.add(valid)
        db.commit()

        invalid = EvidenceChunk(
            evidence_id="evidence_invalid",
            material_id=material.material_id,
            knowledge_document_id=knowledge.document_id,
            source_type="ambiguous",
            text="Ambiguous source",
            content_hash="d" * 64,
        )
        db.add(invalid)
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.close()
