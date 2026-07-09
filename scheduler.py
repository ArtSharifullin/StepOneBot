from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application
from zoneinfo import ZoneInfo

from bot_handlers import _format_vacancies_message
from hh_rss_parser import HHRssParser
from user_db import UserDB

logger = logging.getLogger(__name__)

MSK_TZ = ZoneInfo("Europe/Moscow")


class VacancyScheduler:
    def __init__(self, app: Application, db: UserDB, parser: HHRssParser) -> None:
        self.app = app
        self.db = db
        self.parser = parser
        self.scheduler = AsyncIOScheduler(timezone=MSK_TZ)

    def start(self) -> None:
        self.scheduler.add_job(
            self.send_daily_digest,
            trigger=CronTrigger(hour=22, minute=22, timezone=MSK_TZ),
            id="daily_vacancy_digest",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info("Daily scheduler started for 22:22 MSK")

    async def stop(self) -> None:
        if self.scheduler.state == STATE_RUNNING:
            self.scheduler.shutdown(wait=False)

    async def send_daily_digest(self) -> None:
        users = await self.db.get_active_users()
        if not users:
            logger.info("No active users for daily digest")
            return

        for user in users:
            if not user.search_query or not user.area_id or user.salary_min is None:
                continue

            try:
                vacancies = await self.parser.fetch_and_filter_today(
                    search_query=user.search_query,
                    area_id=user.area_id,
                    salary_min=user.salary_min,
                )
            except Exception as exc:
                # По ТЗ: ошибка логируется, но задача не роняет бота.
                logger.exception("Daily digest failed for user %s: %s", user.telegram_id, exc)
                continue

            # По ТЗ не отправляем "ничего не найдено" в автодоставке.
            if not vacancies:
                continue

            message = _format_vacancies_message(vacancies)
            try:
                await self.app.bot.send_message(chat_id=user.telegram_id, text=message)
            except Exception as exc:
                logger.exception("Failed to send digest to user %s: %s", user.telegram_id, exc)

            # Ограничиваем скорость отправки, чтобы не упереться в лимиты Telegram.
            await asyncio.sleep(0.05)
