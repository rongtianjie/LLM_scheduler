from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_config
from app.core.auth import require_admin_session

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    """Process login form submission."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    config = get_config()

    if username == config.admin.username and password == config.admin.password:
        request.session["admin"] = True
        request.session["username"] = username
        return RedirectResponse(url="/admin", status_code=302)

    return templates.TemplateResponse(
        request, "login.html", {"error": "用户名或密码错误"}
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
