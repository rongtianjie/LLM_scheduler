from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_config

security = HTTPBasic(auto_error=False)


async def authenticate_request(request: Request) -> str:
    """Authenticate a proxy request. Returns the username (or 'anonymous')."""
    config = get_config()
    if not config.auth.enabled:
        return "anonymous"

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth_header[7:]
    from app.database import get_db
    db = await get_db()
    cursor = await db.execute("SELECT name, enabled FROM api_keys WHERE key = ?", (api_key,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not row["enabled"]:
        raise HTTPException(status_code=403, detail="API key is disabled")
    return row["name"]


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

    if username != config.admin.username or password != config.admin.password:
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
