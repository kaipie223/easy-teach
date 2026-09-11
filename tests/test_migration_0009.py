import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0009_private_teacher_knowledge.py"
)
spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_constraint_name_uses_reflected_postgres_name():
    constraints = [
        {
            "name": "knowledge_documents_owner_id_fkey",
            "constrained_columns": ["owner_id"],
        }
    ]

    assert migration._constraint_name(
        constraints,
        ["owner_id"],
        "fk_knowledge_documents_owner_id_users",
    ) == "knowledge_documents_owner_id_fkey"


def test_constraint_name_falls_back_for_unnamed_sqlite_constraint():
    constraints = [{"name": None, "constrained_columns": ["owner_id"]}]

    assert migration._constraint_name(
        constraints,
        ["owner_id"],
        "fk_knowledge_documents_owner_id_users",
    ) == "fk_knowledge_documents_owner_id_users"
