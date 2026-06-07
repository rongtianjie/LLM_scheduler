import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_config
from app.core.auth import require_admin_session
from app.core.password import verify_password

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# In-memory login failure tracking
_login_failures: dict[str, list[float]] = {}
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT_SECONDS = 300


def reset_login_failures():
    """Clear all login failure records (for testing)."""
    _login_failures.clear()


async def _require_login(request: Request):
    """Dependency: require admin session, redirect to login if not authenticated."""
    result = await require_admin_session(request)
    if isinstance(result, RedirectResponse):
        return result
    return None


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Render login page. Redirect to dashboard if already logged in."""
    if request.session.get("admin"):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse(request, "login.html")


@router.post("/admin/login")
async def admin_login_submit(request: Request):
    """Process login form submission with bcrypt support and lockout."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    config = get_config()

    client_ip = request.client.host if request.client else "unknown"

    # Check lockout
    now = time.time()
    failures = _login_failures.get(client_ip, [])
    failures = [t for t in failures if now - t < _LOGIN_LOCKOUT_SECONDS]
    _login_failures[client_ip] = failures
    if len(failures) >= _LOGIN_MAX_FAILURES:
        remaining = int(_LOGIN_LOCKOUT_SECONDS - (now - failures[0]))
        return templates.TemplateResponse(
            request, "login.html",
            {"error": f"登录尝试过多，请 {remaining} 秒后重试"}
        )

    # Verify password (works with both bcrypt hashed and plaintext)
    password_ok = False
    if username == config.admin.username:
        try:
            password_ok = verify_password(password, config.admin.password)
        except Exception:
            password_ok = (password == config.admin.password)

    if password_ok:
        _login_failures.pop(client_ip, None)
        request.session["admin"] = True
        request.session["username"] = username
        return RedirectResponse(url="/admin", status_code=302)

    # Record failure
    failures.append(now)
    _login_failures[client_ip] = failures
    attempts_left = _LOGIN_MAX_FAILURES - len(failures)
    msg = f"用户名或密码错误（还剩 {attempts_left} 次尝试）" if attempts_left > 0 else f"用户名或密码错误，账户已锁定 {_LOGIN_LOCKOUT_SECONDS} 秒"
    return templates.TemplateResponse(
        request, "login.html", {"error": msg}
    )


@router.get("/admin/logout")
async def admin_logout(request: Request):
    """Logout: clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _=Depends(_require_login)):
    if isinstance(_, RedirectResponse):
        return _
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/admin/api-keys", response_class=HTMLResponse)
async def admin_api_keys(request: Request, _=Depends(_require_login)):
    if isinstance(_, RedirectResponse):
        return _
    return templates.TemplateResponse(request, "api_keys.html")


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, _=Depends(_require_login)):
    if isinstance(_, RedirectResponse):
        return _
    return templates.TemplateResponse(request, "logs.html")


@router.get("/admin/management", response_class=HTMLResponse)
async def admin_management(request: Request, _=Depends(_require_login)):
    if isinstance(_, RedirectResponse):
        return _
    return templates.TemplateResponse(request, "management.html")
