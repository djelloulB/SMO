"""Main FastAPI application with binding, healthcheck, and redaction (REC-007)."""
import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config.security import security_layer
from ..config.settings import settings
from ..mcp_server.http import MCP_PATH, MCPHttpGateway
from ..mcp_server.server import build_server
from ..memory.sqlite_store import SQLiteStore
from ..orchestrator.scheduler import start_scheduler, stop_scheduler
from ..security.audit import audit_logger
from ..utils.redact import SecretRedactionFilter, redact_dict
from .api import router as api_router
from .oauth import router as oauth_router

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def build_mcp_gateway() -> MCPHttpGateway:
    return MCPHttpGateway.from_environment(build_server)

# Configure logging with redaction
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("root").addFilter(SecretRedactionFilter())

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    gateway = build_mcp_gateway() if settings.mcp_enabled else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        start_scheduler()
        try:
            async with AsyncExitStack() as stack:
                if gateway is not None:
                    await stack.enter_async_context(gateway.lifespan())
                yield
        finally:
            stop_scheduler()

    app = FastAPI(
        title="SMO (Social Media Orchestrator)",
        version="0.1.0",
        description="Autonomous social media orchestration agent with built-in security.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.include_router(oauth_router, prefix="/oauth", tags=["oauth"])
    app.include_router(api_router)
    if gateway is not None:
        app.add_route(MCP_PATH, gateway)

    @app.get("/healthz")
    async def healthcheck() -> dict:
        """Healthcheck endpoint with safe defaults."""
        try:
            db = SQLiteStore()
            with db.get_session() as session:
                from sqlalchemy import text
                row_count = session.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        except Exception as e:
            logger.warning("Healthcheck DB unavailable: %s", e)
            row_count = 0

        payload = {
            "status": "ok",
            "db": "up" if row_count is not None else "down",
            "accounts": row_count,
            "dry_run": security_layer.dry_run,
            "human_gate": security_layer.human_gate,
        }
        return redact_dict(payload)

    @app.get("/")
    async def root():
        """Serve the dashboard if built, otherwise return API info."""
        index_file = _FRONTEND_DIST / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return redact_dict(
            {
                "service": "SMO (Social Media Orchestrator)",
                "version": "0.1.0",
                "security": {
                    "dry_run": security_layer.dry_run,
                    "human_gate": security_layer.human_gate,
                    "budget_per_day_usd": security_layer.budget_per_day_usd,
                },
                "endpoints": {
                    "health": "/healthz",
                    "oauth": "/oauth/{platform}",
                    "oauth_callback": "/oauth/{platform}/callback",
                    "docs": "/docs",
                    "redoc": "/redoc",
                },
            }
        )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        import time
        start_time = time.time()
        response = await call_next(request)
        response.headers["X-Process-Time"] = str(time.time() - start_time)
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback
        request_id = str(uuid.uuid4())
        tb_str = traceback.format_exc()
        logger.error(
            "Unhandled exception %s | requestId=%s path=%s method=%s",
            str(exc), request_id, request.url.path, request.method,
        )
        audit_logger.log(
            actor_id=f"api_error:{request_id}",
            tool="global_exception_handler",
            payload_hash=__import__("hashlib").sha256(tb_str.encode()).hexdigest()[:32],
            result="failure",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error", "request_id": request_id, "detail": str(exc)},
        )

    # Serve the SPA (React/Vite build) if present; otherwise the API-only root remains.
    if _FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            file_path = _FRONTEND_DIST / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app


app = create_app()
