"""Distill entrypoint — `python -m api`, `python main.py`, or uvicorn."""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("distill")


def main() -> None:
    # Limit native thread pools before torch can ever be imported (lazy diarize).
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    os.environ.setdefault("TORCH_NUM_THREADS", "1")

    import uvicorn

    from api.config import Settings

    settings = Settings.from_env()
    logger.info(
        "Booting Distill bind=%s:%s (factory; no model load at startup)",
        settings.host,
        settings.port,
    )
    # factory=True: create_app() runs in the server process after uvicorn starts,
    # and must stay free of torch/pyannote imports.
    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=True,
        # lifespan still runs, but our startup is a no-op so bind is immediate.
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Distill stopped")
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("Fatal error during boot: %s", exc)
        sys.exit(1)
