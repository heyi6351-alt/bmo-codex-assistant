"""Entry point — start the AI Gold Trading Bot (long-polling)."""

from __future__ import annotations

import os

# Pin BLAS/OpenMP to a single thread BEFORE numpy/pandas import. Our indicator
# math is small-array work, so this costs nothing and avoids OpenBLAS thread
# explosions / memory-allocation failures on low-RAM Windows machines.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import logging

from telegram import Update

from bot.app import build_application
from config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
# Telegram/httpx are chatty at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
log = logging.getLogger("goldbot")


def main() -> None:
    missing = CONFIG.missing_required()
    if missing:
        log.error("Missing required keys: %s", ", ".join(missing))
        log.error("Copy .env.example to .env and fill them in, then run again.")
        raise SystemExit(1)

    for warning in CONFIG.missing_recommended():
        log.warning("Not set — %s", warning)

    app = build_application()
    log.info("AI Gold Trading Bot is starting (long-polling). Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
