import logging
import yaml
from pathlib import Path

import structlog
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin_api import router as admin_api_router
from app.api.admin_pages import router as admin_pages_router
from app.api.proxy import router as proxy_router
from app.config import init_config, get_config
from app.core.health_checker import init_health_checker, get_health_checker
from app.core.http_client import init_client, close_client
from app.core.metrics import get_metrics, metrics_enabled
from app.core.queue import init_queue
from app.database import close_db, init_db


def _write_config_password(cfg, hashed: str):
    """Write the hashed password back to the primary config file."""
    config_path = getattr(cfg, "_config_path", None)
    if not config_path:
        return
    p = Path(config_path)
    if not p.exists():
        return
    try:
        with open(p, "r") as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("admin", {})["password"] = hashed
        with open(p, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        structlog.get_logger().warning("config.write_failed", error=str(e))


def create_app() -> FastAPI:
    # Load config
    config = init_config()

    # Configure structured logging
    logging.basicConfig(level=config.logging.level.upper(), format="%(message)s")
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
        init_queue(cfg.queue.max_length, cfg.queue.concurrency)
        init_health_checker()
        await init_client(cfg.proxy.to_url() or "")
        # Suppress uvicorn access logs (set after uvicorn's own log config)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logger = structlog.get_logger()

        loaded = getattr(cfg, "_loaded_files", [])
        backend_info = ", ".join(
            f"{b.name or b.base_url}({','.join(b.protocols)})" for b in cfg.backends
        ) if cfg.backends else "(none)"
        logger.info(
            "gateway.startup",
            port=cfg.server.port,
            config_files=loaded or ["(defaults)"],
            queue_max=cfg.queue.max_length,
            priority_strategy=cfg.priority.strategy,
            auth_enabled=cfg.auth.enabled,
            backends=backend_info,
            debug_enabled=cfg.debug.enabled,
        )

        if cfg.auth.enabled:
            logger.info("AUTH is ENABLED — clients must provide Authorization: Bearer <api-key>")
        else:
            logger.info("AUTH is DISABLED — all requests pass through without authentication")

        # Admin password: auto-hash plaintext on first startup
        if cfg.admin.enabled:
            from pathlib import Path
            from app.core.password import hash_password, verify_password

            # Check for password reset file
            reset_file = Path("reset_admin_password")
            if reset_file.exists():
                new_plain = reset_file.read_text().strip()
                reset_file.unlink()
                hashed = hash_password(new_plain)
                cfg.admin.password = hashed
                _write_config_password(cfg, hashed)
                logger.info("ADMIN_PASSWORD_RESET — password updated from reset file")
            elif len(cfg.admin.password) < 60:  # bcrypt hashes are ~60 chars, plaintext is short
                # Looks like a plaintext password — hash it and write back
                hashed = hash_password(cfg.admin.password)
                cfg.admin.password = hashed
                _write_config_password(cfg, hashed)
                logger.info("ADMIN_PASSWORD_HASHED — plaintext password auto-hashed")

            if len(cfg.admin.password) < 6:
                logger.warning("ADMIN_PASSWORD_WEAK — password is too short (< 6 chars)")

        # Log cleanup on startup
        from app.database import cleanup_old_logs
        await cleanup_old_logs(
            retention_days=cfg.log_retention.retention_days,
            max_records=cfg.log_retention.max_records,
        )
        # Start health checker background task
        hc = get_health_checker()
        await hc.start(cfg.backends, proxy_url=cfg.proxy.to_url() or "")

        yield
        # Shutdown
        await hc.stop()
        await close_client()
        await close_db()
        logger.info("gateway.shutdown")

    # Create FastAPI app
    app = FastAPI(
        title="LLM Scheduler",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Session middleware for admin login
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.admin.secret_key,
        max_age=86400,  # 24 hours
        same_site="lax",
        https_only=config.admin.session_https_only,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors.origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # GZip compression for responses > 1000 bytes
    app.add_middleware(GZipMiddleware, minimum_size=1000)

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

    # Readiness check
    @app.get("/health/ready")
    async def health_ready():
        """Readiness probe: DB + at least one backend must be reachable."""
        import structlog as _sl
        _log = _sl.get_logger()
        try:
            from app.database import get_db
            db = await get_db()
            await db.execute("SELECT 1")
        except Exception as e:
            _log.warning("health.ready.db_fail", error=str(e))
            return JSONResponse(status_code=503, content={"status": "not ready", "reason": "database"})
        try:
            from app.core.health_checker import get_health_checker
            hc = get_health_checker()
            cfg = get_config()
            enabled_backends = [b for b in cfg.backends if b.enabled]
            if enabled_backends:
                any_healthy = any(hc.is_healthy(b.base_url) for b in enabled_backends)
                if not any_healthy:
                    _log.warning("health.ready.no_backend")
                    return JSONResponse(status_code=503, content={"status": "not ready", "reason": "no healthy backend"})
        except Exception as e:
            _log.warning("health.ready.hc_fail", error=str(e))
        return {"status": "ready"}

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
