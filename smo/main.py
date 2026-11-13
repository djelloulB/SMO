"""
Main entrypoint for SMO.
"""
import logging

from .config import settings
from .config.security import security_layer
from .server.app import app as fastapi_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting SMO version %s", settings.app_version)
    logger.info("Security layer: dry_run=%s, human_gate=%s", security_layer.dry_run, security_layer.human_gate)

    uvicorn.run(
        "smo.server.app:app",
        host="127.0.0.1",
        port=8023,
        log_level="info",
    )
