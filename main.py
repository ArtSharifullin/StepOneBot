from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot_handlers import handle_city_callback, handle_text, start
from hh_rss_parser import HHRssParser
from scheduler import VacancyScheduler
from user_db import UserDB

LOCK_FILE = Path("bot.lock")
logger = logging.getLogger(__name__)


def acquire_single_instance_lock() -> None:
    """Не даём запустить второй polling-процесс с тем же токеном."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            os.kill(old_pid, 0)
        except (ValueError, ProcessLookupError, OSError):
            LOCK_FILE.unlink(missing_ok=True)
        else:
            raise RuntimeError(
                f"Бот уже запущен (PID {old_pid}). "
                "Остановите предыдущий процесс перед новым запуском."
            )

    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config() -> dict[str, str]:
    # override=True гарантирует, что значения из .env перезапишут
    # ранее выставленные переменные окружения (в т.ч. пустые в PowerShell).
    load_dotenv(override=True)
    config = {
        "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
        "DB_PATH": os.getenv("DB_PATH", "users.db"),
        "RSS_BASE_URL": os.getenv("RSS_BASE_URL", "https://hh.ru/search/vacancy/rss"),
        "REQUEST_TIMEOUT_SECONDS": os.getenv("REQUEST_TIMEOUT_SECONDS", "15"),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
    }
    config["BOT_TOKEN"] = config["BOT_TOKEN"].strip()
    if not config["BOT_TOKEN"]:
        raise RuntimeError("BOT_TOKEN is required. Add it to .env file.")
    return config


async def post_init(app: Application) -> None:
    db: UserDB = app.bot_data["db"]
    await db.init()

    scheduler: VacancyScheduler = app.bot_data["scheduler"]
    scheduler.start()


async def post_shutdown(app: Application) -> None:
    scheduler: VacancyScheduler = app.bot_data["scheduler"]
    await scheduler.stop()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning(
            "Конфликт polling: запущен ещё один экземпляр бота. "
            "Оставьте только один процесс main.py."
        )
        return

    logger.exception("Unhandled error: %s", context.error)


def main() -> None:
    config = load_config()
    setup_logging(config["LOG_LEVEL"])
    acquire_single_instance_lock()

    db = UserDB(config["DB_PATH"])
    parser = HHRssParser(
        rss_base_url=config["RSS_BASE_URL"],
        request_timeout_seconds=int(config["REQUEST_TIMEOUT_SECONDS"]),
    )

    application = (
        Application.builder()
        .token(config["BOT_TOKEN"])
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    scheduler = VacancyScheduler(app=application, db=db, parser=parser)

    application.bot_data["db"] = db
    application.bot_data["parser"] = parser
    application.bot_data["scheduler"] = scheduler

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_city_callback, pattern=r"^city:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)

    application.run_polling(close_loop=False, drop_pending_updates=True)


if __name__ == "__main__":
    main()
