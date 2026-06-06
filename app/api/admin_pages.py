from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _admin_auth(request: Request):
    from app.core.auth import verify_admin
    return await verify_admin(request=request)


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _=Depends(_admin_auth)):
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/admin/api-keys", response_class=HTMLResponse)
async def admin_api_keys(request: Request, _=Depends(_admin_auth)):
    return templates.TemplateResponse(request, "api_keys.html")


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, _=Depends(_admin_auth)):
    return templates.TemplateResponse(request, "logs.html")


@router.get("/admin/management", response_class=HTMLResponse)
async def admin_management(request: Request, _=Depends(_admin_auth)):
    return templates.TemplateResponse(request, "management.html")
