from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram.constants import ParseMode
from telegram.ext import Application
from zoneinfo import ZoneInfo

from messages import format_vacancies_message, new_vacancies_alert
from hh_rss_parser import HHRssParser, Vacancy
from user_db import TIER_BASIC, TIER_PREMIUM, UserDB, UserProfile

logger = logging.getLogger(__name__)

MSK_TZ = ZoneInfo("Europe/Moscow")
PREMIUM_CHECK_HOURS = 5


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
        self.scheduler.add_job(
            self.send_premium_alerts,
            trigger=IntervalTrigger(hours=PREMIUM_CHECK_HOURS, timezone=MSK_TZ),
            id="premium_vacancy_alerts",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(
            "Scheduler started: daily digest at 22:22 MSK, premium alerts every %s hours",
            PREMIUM_CHECK_HOURS,
        )

    async def stop(self) -> None:
        if self.scheduler.state == STATE_RUNNING:
            self.scheduler.shutdown(wait=False)

    async def send_daily_digest(self) -> None:
        users = await self.db.get_subscribed_users()
        if not users:
            logger.info("No subscribed users for daily digest")
            return

        for user in users:
            if user.subscription_tier not in (TIER_BASIC, TIER_PREMIUM):
                continue
            await self._send_vacancies_to_user(
                user=user,
                header="📬 Ежедневная подборка вакансий",
                only_new=False,
            )

    async def send_premium_alerts(self) -> None:
        users = await self.db.get_premium_users()
        if not users:
            logger.info("No premium users for auto-alerts")
            return

        for user in users:
            await self._send_vacancies_to_user(
                user=user,
                header="🚀 Новые вакансии по вашему профилю",
                only_new=True,
            )

    async def _send_vacancies_to_user(
        self,
        user: UserProfile,
        header: str,
        only_new: bool,
    ) -> None:
        if not user.search_query or not user.area_id or user.salary_min is None:
            return

        try:
            vacancies = await self.parser.fetch_and_filter_today(
                search_query=user.search_query,
                area_id=user.area_id,
                salary_min=user.salary_min,
            )
        except Exception as exc:
            logger.exception("Scheduled send failed for user %s: %s", user.telegram_id, exc)
            return

        if only_new:
            sent_links = await self.db.get_sent_links(user.telegram_id)
            vacancies = [v for v in vacancies if v.link not in sent_links]

        if not vacancies:
            return

        await self._deliver_vacancies(
            telegram_id=user.telegram_id,
            vacancies=vacancies,
            header=header,
            alert_prefix=new_vacancies_alert(len(vacancies)) if only_new else None,
        )
        await self.db.record_sent_vacancies(
            user.telegram_id,
            [v.link for v in vacancies],
        )
        await asyncio.sleep(0.05)

    async def _deliver_vacancies(
        self,
        telegram_id: int,
        vacancies: list[Vacancy],
        header: str,
        alert_prefix: Optional[str] = None,
    ) -> None:
        if alert_prefix:
            try:
                await self.app.bot.send_message(
                    chat_id=telegram_id,
                    text=alert_prefix,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.exception("Failed to send alert to user %s: %s", telegram_id, exc)

        chunks = format_vacancies_message(vacancies, header=header)
        for chunk in chunks:
            try:
                await self.app.bot.send_message(
                    chat_id=telegram_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                logger.exception("Failed to send vacancies to user %s: %s", telegram_id, exc)
                break
            await asyncio.sleep(0.05)
