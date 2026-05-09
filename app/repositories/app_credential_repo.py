from sqlalchemy import text

from infra.db import get_engine


def find_by_app_key(app_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT app_id, app_key, app_secret, description, is_active "
                "FROM app_credentials WHERE app_key = :key"
            ),
            {"key": app_key},
        ).mappings().first()
        return dict(row) if row else None
