from __future__ import annotations

import html
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from hh_rss_parser import Vacancy
from user_db import TIER_BASIC, TIER_FREE, TIER_PREMIUM, FREE_SCAN_LIMIT, UserProfile

MSK_TZ = ZoneInfo("Europe/Moscow")

CITY_NAMES = {
    1: "Москва",
    2: "Санкт-Петербург",
    88: "Казань",
    3: "Екатеринбург",
    4: "Новосибирск",
}


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _tier_label(tier: str) -> str:
    labels = {
        TIER_FREE: "🆓 Бесплатный",
        TIER_BASIC: "⭐ Базовый",
        TIER_PREMIUM: "💎 Премиум",
    }
    return labels.get(tier, tier)


def _subscription_status(user: UserProfile) -> str:
    if user.subscription_tier == TIER_FREE:
        remaining = max(0, FREE_SCAN_LIMIT - user.scans_used)
        return f"Осталось бесплатных сканов: <b>{remaining}</b> из {FREE_SCAN_LIMIT}"
    if user.subscription_expires_at:
        expires = user.subscription_expires_at.astimezone(MSK_TZ)
        expires_text = expires.strftime("%d.%m.%Y")
        return f"Активна до <b>{expires_text}</b>"
    return "Активна"


def welcome_new_user(name: str) -> str:
    return (
        f"👋 <b>Добро пожаловать в StepOne, {esc(name)}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 <b>StepOne</b> — ваш умный помощник в поиске работы на hh.ru.\n\n"
        "✨ <b>Что умеет бот:</b>\n"
        "• 🔍 Мгновенный поиск вакансий по вашим критериям\n"
        "• 📬 Ежедневная подборка лучших предложений\n"
        "• 🔔 Автоуведомления о новых вакансиях (Премиум)\n"
        "• 📊 Статистика рынка по вашей специальности\n\n"
        f"🎁 Вам доступно <b>{FREE_SCAN_LIMIT} бесплатных скана</b> — попробуйте прямо сейчас!\n\n"
        "👇 Для начала укажите <b>желаемую должность</b>\n"
        "<i>Например: Python backend developer</i>"
    )


def welcome_back(name: str) -> str:
    return (
        f"👋 <b>С возвращением, {esc(name)}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Рады видеть вас снова. Используйте меню ниже для поиска вакансий "
        "или откройте <b>👤 Личный кабинет</b> для управления профилем."
    )


def onboarding_complete() -> str:
    return (
        "🎉 <b>Профиль настроен!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Все фильтры сохранены. Теперь вы можете:\n"
        "• 🔥 Искать вакансии за сегодня\n"
        "• 📋 Просматривать все подходящие вакансии\n"
        "• 👤 Управлять настройками в личном кабинете\n\n"
        f"🎁 У вас есть <b>{FREE_SCAN_LIMIT} бесплатных скана</b> для знакомства с ботом."
    )


def cabinet_text(user: UserProfile) -> str:
    city = CITY_NAMES.get(user.area_id, "Не указан") if user.area_id else "Не указан"
    position = user.search_query or "Не указана"
    salary = f"от {user.salary_min:,} ₽".replace(",", " ") if user.salary_min else "Не указана"
    notifications = "🔔 Включены" if user.notifications_enabled else "🔕 Выключены"
    active = "✅ Активна" if user.is_active else "⏸ Приостановлена"

    return (
        "👤 <b>Личный кабинет</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💼 <b>Должность:</b> {esc(position)}\n"
        f"🏙 <b>Город:</b> {esc(city)}\n"
        f"💰 <b>Зарплата:</b> {esc(salary)}\n\n"
        f"📋 <b>Подписка:</b> {_tier_label(user.subscription_tier)}\n"
        f"📅 {_subscription_status(user)}\n"
        f"📬 <b>Рассылка:</b> {active}\n"
        f"🔔 <b>Уведомления:</b> {notifications}\n"
        f"🔍 <b>Использовано сканов:</b> {user.scans_used}"
    )


def subscription_menu_text() -> str:
    return (
        "💎 <b>Тарифные планы StepOne</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите подписку, которая подходит именно вам.\n"
        "<i>Оплата демонстрационная — подписка активируется мгновенно.</i>"
    )


def subscription_basic_text() -> str:
    return (
        "⭐ <b>Базовый — 249 ₽/мес</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Неограниченный поиск вакансий\n"
        "✅ Ежедневная подборка в 22:22 МСК\n"
        "✅ Фильтры по должности, городу и зарплате\n"
        "✅ Статистика рынка после каждого скана\n\n"
        "👇 Нажмите кнопку ниже для активации"
    )


def subscription_premium_text() -> str:
    return (
        "💎 <b>Премиум — 499 ₽/мес</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔥 <b>Всё из Базового, плюс:</b>\n"
        "• 🚀 <b>Автоуведомления</b> о новых вакансиях каждые 5 часов\n"
        "• ⚡ Приоритетная доставка свежих вакансий\n"
        "• 🎯 Мгновенные алерты по вашей специальности\n"
        "• 💎 Значок премиум-статуса в профиле\n\n"
        "<i>Идеально для тех, кто активно ищет работу!</i>\n\n"
        "👇 Нажмите кнопку ниже для активации"
    )


def subscription_activated(tier: str) -> str:
    if tier == TIER_PREMIUM:
        return (
            "🎉 <b>Премиум подписка активирована!</b>\n\n"
            "Теперь вам доступны:\n"
            "• 🚀 Автоуведомления каждые 5 часов\n"
            "• ⚡ Безлимитный поиск\n"
            "• 📬 Ежедневная подборка\n\n"
            "Удачи в поиске работы! 💪"
        )
    return (
        "🎉 <b>Базовая подписка активирована!</b>\n\n"
        "Теперь вам доступны:\n"
        "• 🔍 Безлимитный поиск вакансий\n"
        "• 📬 Ежедневная подборка в 22:22 МСК\n\n"
        "Удачи в поиске работы! 💪"
    )


def scan_limit_reached() -> str:
    return (
        "🔒 <b>Бесплатные сканы исчерпаны</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Вы использовали все <b>{FREE_SCAN_LIMIT} бесплатных скана</b>.\n\n"
        "Оформите подписку, чтобы продолжить поиск:\n"
        "• ⭐ <b>Базовый</b> — 249 ₽/мес\n"
        "• 💎 <b>Премиум</b> — 499 ₽/мес (автоуведомления!)\n\n"
        "Нажмите <b>💎 Подписка</b> в меню."
    )


def format_vacancies_message(
    vacancies: list[Vacancy],
    header: str,
    footer: Optional[str] = None,
) -> list[str]:
    """Разбивает длинные сообщения на части (лимит Telegram — 4096 символов)."""
    chunks: list[str] = []
    current_lines = [f"📋 <b>{esc(header)}</b>\n━━━━━━━━━━━━━━━━━━━━\n"]

    for idx, vacancy in enumerate(vacancies, start=1):
        published_msk = vacancy.pub_date.astimezone(MSK_TZ)
        published_text = datetime.strftime(published_msk, "%d.%m.%Y %H:%M")
        block = (
            f"\n<b>{idx}.</b> {esc(vacancy.title)}\n"
            f"🏢 <b>Компания:</b> {esc(vacancy.company)}\n"
            f"💰 <b>Зарплата:</b> {esc(vacancy.salary_text)}\n"
            f"📅 <b>Дата:</b> {published_text} (МСК)\n"
            f"🔗 <a href=\"{esc(vacancy.link)}\">Открыть вакансию</a>\n"
            "─────────────────"
        )
        if sum(len(line) for line in current_lines) + len(block) > 3800:
            chunks.append("\n".join(current_lines))
            current_lines = [f"📋 <b>{esc(header)}</b> <i>(продолжение)</i>\n\n"]
        current_lines.append(block)

    if footer:
        current_lines.append(f"\n{footer}")
    chunks.append("\n".join(current_lines))
    return chunks


def market_stats(vacancies: list[Vacancy]) -> str:
    salaries = [v.salary for v in vacancies if v.salary is not None]
    if not salaries:
        return (
            "📊 <b>Статистика рынка</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Найдено вакансий: <b>{len(vacancies)}</b>\n"
            "💰 Зарплата указана не во всех объявлениях."
        )

    avg = sum(salaries) // len(salaries)
    min_s = min(salaries)
    max_s = max(salaries)
    with_salary = len(salaries)

    return (
        "📊 <b>Статистика рынка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Найдено: <b>{len(vacancies)}</b> вакансий\n"
        f"💰 С указанной зарплатой: <b>{with_salary}</b>\n"
        f"📈 Средняя: <b>{avg:,} ₽</b>\n".replace(",", " ")
        + f"📉 Минимум: <b>{min_s:,} ₽</b>\n".replace(",", " ")
        + f"📊 Максимум: <b>{max_s:,} ₽</b>".replace(",", " ")
    )


def no_vacancies_today() -> str:
    return (
        "😔 <b>На сегодня новых вакансий не найдено</b>\n\n"
        "Попробуйте позже или измените фильтры в личном кабинете."
    )


def no_vacancies_all() -> str:
    return (
        "😔 <b>Вакансий по вашему запросу не найдено</b>\n\n"
        "Попробуйте изменить должность, город или снизить планку зарплаты."
    )


def profile_incomplete() -> str:
    return (
        "⚠️ <b>Профиль заполнен не полностью</b>\n\n"
        "Откройте <b>👤 Личный кабинет</b> и настройте все параметры поиска."
    )


def new_vacancies_alert(count: int) -> str:
    return (
        f"🚀 <b>Новые вакансии!</b>\n"
        f"Найдено <b>{count}</b> свежих предложений по вашему профилю:"
    )
