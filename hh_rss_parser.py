from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

MSK_TZ = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class Vacancy:
    title: str
    link: str
    description: str
    pub_date: datetime
    salary: Optional[int]
    salary_text: str
    company: str


class HHRssParser:
    def __init__(self, rss_base_url: str, request_timeout_seconds: int = 15) -> None:
        self.rss_base_url = rss_base_url
        self.request_timeout_seconds = request_timeout_seconds
        self.fallback_rss_url = "https://hh.ru/search/vacancy/rss"
        self.headers = {
            # Нормальный User-Agent обязателен: некоторые публичные RSS-источники
            # блокируют "пустые" или подозрительные агенты.
            "User-Agent": (
                "StepOneBot/1.0 (+https://t.me/your_bot_username) "
                "Python aiohttp feedparser"
            )
        }

    async def fetch_and_filter_today(
        self, search_query: str, area_id: int, salary_min: int
    ) -> list[Vacancy]:
        feed = await self._fetch_feed(search_query=search_query, area_id=area_id)
        return self._filter_vacancies(feed=feed, salary_min=salary_min, only_today=True, limit=10)

    async def fetch_and_filter_all(
        self, search_query: str, area_id: int, salary_min: int, limit: int = 20
    ) -> list[Vacancy]:
        feed = await self._fetch_feed(search_query=search_query, area_id=area_id)
        return self._filter_vacancies(feed=feed, salary_min=salary_min, only_today=False, limit=limit)

    async def _fetch_feed(self, search_query: str, area_id: int) -> feedparser.FeedParserDict:
        params = {"text": search_query, "area": area_id}
        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
        content = await self._fetch_content(url=self.rss_base_url, params=params, timeout=timeout)
        parsed = feedparser.parse(content)

        # Иногда указанный endpoint может отдать HTML вместо RSS.
        # В этом случае пробуем официальный RSS URL поиска вакансий.
        if self._looks_like_html(content) or not parsed.entries:
            logger.warning(
                "Primary HH URL returned non-RSS or empty feed, fallback is used. url=%s",
                self.rss_base_url,
            )
            fallback_content = await self._fetch_content(
                url=self.fallback_rss_url,
                params=params,
                timeout=timeout,
            )
            parsed = feedparser.parse(fallback_content)

        if parsed.bozo:
            logger.warning("RSS parse warning: %s", parsed.bozo_exception)
        return parsed

    async def _fetch_content(
        self, url: str, params: dict[str, object], timeout: aiohttp.ClientTimeout
    ) -> bytes:
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.read()

    def _looks_like_html(self, content: bytes) -> bool:
        snippet = content[:500].decode("utf-8", errors="ignore").lower()
        return "<html" in snippet and "<rss" not in snippet

    def _entry_to_vacancy(self, entry: feedparser.FeedParserDict) -> Optional[Vacancy]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        description = html.unescape((entry.get("description") or entry.get("summary") or "").strip())
        pub_date_raw = entry.get("published") or entry.get("pubDate") or ""

        if not title or not link or not pub_date_raw:
            return None

        pub_date = self._parse_publication_date(pub_date_raw)
        if pub_date is None:
            return None

        salary = self._extract_salary_from_text(f"{title}\n{description}")
        if salary is None:
            salary = self._extract_salary_from_rss_description(description)
        salary_text = f"от {salary:,} ₽".replace(",", " ") if salary else "Не указана"
        company = self._extract_company(entry=entry, title=title, description=description)

        return Vacancy(
            title=title,
            link=link,
            description=description,
            pub_date=pub_date,
            salary=salary,
            salary_text=salary_text,
            company=company or "Не указана",
        )

    def _parse_publication_date(self, pub_date_raw: str) -> Optional[datetime]:
        # Чаще всего HH RSS отдаёт ISO-дату вида 2026-07-09T12:41:32.177+03:00.
        # Но часть источников может отдавать RFC-строку, поэтому поддерживаем оба формата.
        try:
            return datetime.fromisoformat(pub_date_raw)
        except ValueError:
            pass

        try:
            return parsedate_to_datetime(pub_date_raw)
        except (TypeError, ValueError):
            return None

    def _filter_vacancies(
        self,
        feed: feedparser.FeedParserDict,
        salary_min: int,
        only_today: bool,
        limit: int,
    ) -> list[Vacancy]:
        today_msk = datetime.now(MSK_TZ).date()
        filtered: list[Vacancy] = []

        for entry in feed.entries:
            vacancy = self._entry_to_vacancy(entry)
            if not vacancy:
                continue

            # Если нужен режим "только за сегодня", сравниваем дату в МСК.
            if only_today and vacancy.pub_date.astimezone(MSK_TZ).date() != today_msk:
                continue

            # В RSS нет серверной фильтрации по зарплате, поэтому фильтруем локально.
            # Если зарплата не распознана, вакансию оставляем (по ТЗ: "пропускаем или помечаем").
            if vacancy.salary is not None and vacancy.salary < salary_min:
                continue

            filtered.append(vacancy)

        # Для режима "все вакансии" и "за сегодня" список стабильно сортируем
        # по дате публикации от новых к старым.
        filtered.sort(key=lambda item: item.pub_date, reverse=True)
        return filtered[:limit]

    def _extract_salary_from_text(self, text: str) -> Optional[int]:
        normalized = html.unescape(text).replace("\xa0", " ")

        # Ищем блоки вида "100 000", "от 150000", "до 250 000 руб"
        # и отбрасываем слишком маленькие числа (например, "3 года").
        raw_numbers = re.findall(r"(?<!\d)(\d[\d\s]{2,}\d)(?!\d)", normalized)
        candidates: list[int] = []
        for raw in raw_numbers:
            value = int(re.sub(r"\s+", "", raw))
            if value >= 10_000:
                candidates.append(value)

        if not candidates:
            return None

        return max(candidates)

    def _strip_html(self, text: str) -> str:
        clean = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        clean = re.sub(r"</p>", "\n", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", " ", clean)
        return html.unescape(clean).replace("\xa0", " ")

    def _extract_salary_from_rss_description(self, description: str) -> Optional[int]:
        plain = self._strip_html(description)
        match = re.search(
            r"Предполагаемый уровень месячного дохода:\s*(?:от\s*)?([\d\s]+)",
            plain,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        digits = re.sub(r"\s+", "", match.group(1))
        if not digits.isdigit():
            return None
        value = int(digits)
        return value if value >= 10_000 else None

    def _extract_company(
        self,
        entry: feedparser.FeedParserDict,
        title: str,
        description: str,
    ) -> str:
        author = (entry.get("author") or "").strip()
        if author:
            return author

        plain_description = self._strip_html(description)

        # HH RSS: «Вакансия компании: Название»
        company_patterns = [
            r"Вакансия компании:\s*([^\n\r]+)",
            r"Компания:\s*([^\n\r]+)",
            r"Работодатель:\s*([^\n\r]+)",
            r"employer[\"']?\s*:\s*[\"']([^\"']+)",
        ]
        for pattern in company_patterns:
            match = re.search(pattern, plain_description, flags=re.IGNORECASE)
            if match:
                company = match.group(1).strip()
                if company and company.lower() not in ("не указана", "не указан"):
                    return company

        # Иногда компания указана в заголовке после разделителя.
        for separator in ("—", "–", "-", "|", "·"):
            if separator in title:
                parts = [part.strip() for part in title.split(separator) if part.strip()]
                if len(parts) > 1:
                    return parts[-1]

        return ""
