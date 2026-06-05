import structlog
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin_api import router as admin_api_router
from app.api.admin_pages import router as admin_pages_router
from app.api.proxy import router as proxy_router
from app.config import init_config, get_config
from app.core.metrics import get_metrics, metrics_enabled
from app.core.queue import init_queue
from app.database import close_db, init_db


def create_app() -> FastAPI:
    # Load config
    config = init_config()

    # Configure structured logging
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.dev.ConsoleRenderer()
                if config.logging.format == "text"
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── Lifespan ──────────────────────────────────────────────────
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        cfg = get_config()
        await init_db(cfg)
        init_queue(cfg.queue.max_length)
        logger = structlog.get_logger()

        loaded = getattr(cfg, "_loaded_files", [])
        logger.info(
            "gateway.startup",
            port=cfg.server.port,
            config_files=loaded or ["(defaults)"],
            queue_max=cfg.queue.max_length,
            priority_strategy=cfg.priority.strategy,
            auth_enabled=cfg.auth.enabled,
            backend_base_url=cfg.backend.base_url,
            backend_timeout=cfg.backend.timeout,
        )

        if cfg.auth.enabled:
            logger.info("AUTH is ENABLED — clients must provide Authorization: Bearer <api-key>")
        else:
            logger.info("AUTH is DISABLED — all requests pass through without authentication")
        yield
        # Shutdown
        await close_db()
        logger.info("gateway.shutdown")

    # Create FastAPI app
    app = FastAPI(
        title="LLM Gateway Proxy",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ── Routes ────────────────────────────────────────────────────

    # Admin pages
    app.include_router(admin_pages_router)

    # Admin API
    app.include_router(admin_api_router, prefix="/admin/api")

    # Proxy endpoints
    app.include_router(proxy_router)

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Prometheus metrics
    if metrics_enabled():
        @app.get("/metrics")
        async def metrics():
            data = await get_metrics()
            return PlainTextResponse(data, media_type="text/plain; version=0.0.4")

    # Root redirect
    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin")

    # Serve static files
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


def main():
    app = create_app()
    config = get_config()
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.logging.level.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
