import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base, get_db
from backend.main import app
from backend.schemas import IntentResult
from backend.services.limits import reset_local_limits


@pytest.fixture()
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def client(db_session_factory):
    reset_local_limits()

    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    reset_local_limits()


@pytest.fixture()
def stub_intent_analyzer(monkeypatch):
    """Keep chat workflow tests deterministic and isolated from paid model calls."""

    def analyze(_self, _session_id, messages):
        teaching_goal = messages[-1].get("content", "") if messages else ""
        return IntentResult(
            teaching_goal=teaching_goal,
            missing_info=["授课对象", "核心知识点"],
            follow_up_question="请补充授课对象和核心知识点。",
            is_complete=False,
        )

    monkeypatch.setattr("backend.services.intent.IntentAnalyzer.analyze", analyze)
