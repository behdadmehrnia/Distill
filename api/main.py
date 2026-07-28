"""Distill entrypoint — `python -m api`, `python main.py`, or uvicorn."""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("distill")


def main() -> None:
    import uvicorn

    from api.config import Settings

    settings = Settings.from_env()
    logger.info("Distill listening on http://%s:%s", settings.host, settings.port)
    uvicorn.run(
        "api.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Distill stopped")
    except Exception as exc:
        logger.error("Fatal error: %s", exc)
        sys.exit(1)
