import asyncio
import base64
import hmac
import logging

from repositories import app_credential_repo
from schemas.auth_schema import AppCredential

logger = logging.getLogger(__name__)


async def verify_basic_auth(authorization: str) -> AppCredential | None:
    if not authorization.startswith("Basic "):
        return None

    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
    except Exception:
        logger.info("auth failed: invalid base64")
        return None

    if ":" not in decoded:
        logger.info("auth failed: no colon separator")
        return None

    app_key, app_secret = decoded.split(":", 1)
    if not app_key or not app_secret:
        return None

    row = await asyncio.to_thread(app_credential_repo.find_by_app_key, app_key)
    if row is None:
        logger.info("auth failed: app_key=%s not found", app_key)
        return None

    credential = AppCredential(**row)
    if not credential.is_active:
        logger.info("auth failed: app_id=%s inactive", credential.app_id)
        return None

    if not hmac.compare_digest(app_secret, credential.app_secret):
        logger.info("auth failed: app_id=%s secret mismatch", credential.app_id)
        return None

    logger.info("auth ok: app_id=%s", credential.app_id)
    return credential
