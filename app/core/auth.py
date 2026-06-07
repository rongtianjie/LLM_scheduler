from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_config
from app.models import ApiKeyInfo

security = HTTPBasic(auto_error=False)


async def authenticate_request(request: Request):
    """Authenticate a proxy request.

    Returns:
        ApiKeyInfo if auth enabled and key valid, None if auth disabled (anonymous).

    Raises:
        HTTPException: 401 if missing/invalid key, 403 if key is disabled.
    """
    config = get_config()
    if not config.auth.enabled:
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth_header[7:]
    from app.database import get_db
    db = await get_db()
    cursor = await db.execute(
        "SELECT name, enabled, COALESCE(priority, 100) as priority, "
        "COALESCE(rate_limit, 0) as rate_limit, "
        "COALESCE(token_quota_daily, 0) as token_quota_daily, "
        "COALESCE(token_quota_monthly, 0) as token_quota_monthly "
        "FROM api_keys WHERE key = ?", (api_key,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not row["enabled"]:
        raise HTTPException(status_code=403, detail="API key is disabled")
    return ApiKeyInfo(
        name=row["name"],
        enabled=True,
        priority=row["priority"],
        rate_limit=row["rate_limit"],
        token_quota_daily=row["token_quota_daily"],
        token_quota_monthly=row["token_quota_monthly"],
    )


async def verify_admin(credentials: HTTPBasicCredentials = None, request: Request = None):
    """Verify admin Basic Auth credentials."""
    config = get_config()
    if not config.admin.enabled:
        return True

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})

    import base64
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    if username != config.admin.username:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    # Support both bcrypt-hashed and legacy plaintext passwords
    from app.core.password import verify_password
    try:
        ok = verify_password(password, config.admin.password)
    except Exception:
        ok = (password == config.admin.password)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return True


async def require_admin_session(request: Request):
    """Session-based admin auth. Redirects to login page if not authenticated."""
    config = get_config()
    if not config.admin.enabled:
        return True

    if request.session.get("admin"):
        return True

    return RedirectResponse(url="/admin/login", status_code=302)


async def require_admin_api(request: Request):
    """Session-based admin auth for API endpoints. Returns 401 JSON if not authenticated."""
    config = get_config()
    if not config.admin.enabled:
        return True

    if request.session.get("admin"):
        return True

    raise HTTPException(status_code=401, detail="Not authenticated")
