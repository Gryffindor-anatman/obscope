from sqlalchemy import text

from infra.db import get_engine


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, email, created_at FROM users")
        ).mappings().all()
        return [dict(r) for r in rows]


def insert(name: str, email: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            text("INSERT INTO users (name, email) VALUES (:name, :email)"),
            {"name": name, "email": email},
        )
        return result.lastrowid
