from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from hh_rss_parser import HHRssParser, Vacancy
from user_db import UserDB

logger = logging.getLogger(__name__)

MENU_CHANGE_POSITION = "🔄 Изменить должность"
MENU_CHANGE_CITY = "🏙️ Изменить город"
MENU_CHANGE_SALARY = "💰 Изменить зарплату"
MENU_TODAY_VACANCIES = "🔥 Вакансии за сегодня"
MENU_ALL_VACANCIES = "📋 Все вакансии"

STATE_WAITING_POSITION = "waiting_position"
STATE_WAITING_SALARY = "waiting_salary"

CITIES = {
    "Москва": 1,
    "Санкт-Петербург": 2,
    "Казань": 88,
    "Екатеринбург": 3,
    "Новосибирск": 4,
}
MSK_TZ = ZoneInfo("Europe/Moscow")


def get_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [MENU_CHANGE_POSITION, MENU_CHANGE_CITY],
            [MENU_CHANGE_SALARY, MENU_TODAY_VACANCIES],
            [MENU_ALL_VACANCIES],
        ],
        resize_keyboard=True,
    )


def get_city_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=city_name, callback_data=f"city:{area_id}")]
        for city_name, area_id in CITIES.items()
    ]
    return InlineKeyboardMarkup(rows)


def _set_state(context: ContextTypes.DEFAULT_TYPE, state: Optional[str]) -> None:
    context.user_data["state"] = state


def _get_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return context.user_data.get("state")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    db: UserDB = context.application.bot_data["db"]
    user_id = update.effective_user.id

    user = await db.get_user(user_id)
    if user:
        await db.set_active(user_id, True)
        await update.message.reply_text(
            "С возвращением! Используйте меню для управления фильтрами и поиска.",
            reply_markup=get_main_menu(),
        )
        return

    await db.upsert_user(user_id)
    _set_state(context, STATE_WAITING_POSITION)
    await update.message.reply_text("Введите желаемую должность (например: Python backend developer).")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return

    db: UserDB = context.application.bot_data["db"]
    parser: HHRssParser = context.application.bot_data["parser"]
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = _get_state(context)

    if text == MENU_CHANGE_POSITION:
        _set_state(context, STATE_WAITING_POSITION)
        await update.message.reply_text("Введите новую должность:")
        return

    if text == MENU_CHANGE_CITY:
        await update.message.reply_text("Выберите город:", reply_markup=get_city_keyboard())
        return

    if text == MENU_CHANGE_SALARY:
        _set_state(context, STATE_WAITING_SALARY)
        await update.message.reply_text("Укажите желаемую зарплату от (в рублях):")
        return

    if text == MENU_TODAY_VACANCIES:
        await send_today_vacancies(update=update, context=context, parser=parser, db=db, user_id=user_id)
        return

    if text == MENU_ALL_VACANCIES:
        await send_all_vacancies(update=update, context=context, parser=parser, db=db, user_id=user_id)
        return

    if state == STATE_WAITING_POSITION:
        await db.update_search_query(user_id, text)
        _set_state(context, None)

        user = await db.get_user(user_id)
        if user and user.area_id is None:
            await update.message.reply_text("Выберите город:", reply_markup=get_city_keyboard())
        elif user and user.salary_min is None:
            _set_state(context, STATE_WAITING_SALARY)
            await update.message.reply_text("Укажите желаемую зарплату от (в рублях):")
        else:
            await update.message.reply_text("Должность обновлена.", reply_markup=get_main_menu())
        return

    if state == STATE_WAITING_SALARY:
        salary_min = _parse_salary_input(text)
        if salary_min is None:
            await update.message.reply_text("Введите корректное число, например: 180000")
            return

        await db.update_salary_min(user_id, salary_min)
        _set_state(context, None)
        await update.message.reply_text("Зарплата обновлена.", reply_markup=get_main_menu())
        return

    await update.message.reply_text("Используйте кнопки меню ниже.", reply_markup=get_main_menu())


async def handle_city_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.effective_user:
        return

    await update.callback_query.answer()
    payload = update.callback_query.data or ""
    if not payload.startswith("city:"):
        return

    db: UserDB = context.application.bot_data["db"]
    user_id = update.effective_user.id
    area_id = int(payload.split(":", maxsplit=1)[1])
    await db.update_area_id(user_id, area_id)

    user = await db.get_user(user_id)
    if user and user.salary_min is None:
        _set_state(context, STATE_WAITING_SALARY)
        await update.callback_query.message.reply_text("Укажите желаемую зарплату от (в рублях):")
    else:
        _set_state(context, None)
        await update.callback_query.message.reply_text("Город обновлён.", reply_markup=get_main_menu())


async def send_today_vacancies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parser: HHRssParser,
    db: UserDB,
    user_id: int,
) -> None:
    message_target = update.effective_message
    if not message_target:
        return

    user = await db.get_user(user_id)
    if not user or not user.search_query or not user.area_id or user.salary_min is None:
        _set_state(context, STATE_WAITING_POSITION)
        await message_target.reply_text(
            "Профиль заполнен не полностью. Введите желаемую должность для настройки.",
        )
        return

    try:
        vacancies = await parser.fetch_and_filter_today(
            search_query=user.search_query,
            area_id=user.area_id,
            salary_min=user.salary_min,
        )
    except Exception as exc:
        logger.exception("Failed to fetch vacancies for user %s: %s", user_id, exc)
        await message_target.reply_text("Не удалось получить вакансии. Попробуйте позже.")
        return

    if not vacancies:
        await message_target.reply_text("На сегодня новых вакансий не найдено.")
        return

    await message_target.reply_text(_format_vacancies_message(vacancies, header="Новые вакансии за сегодня:"))


async def send_all_vacancies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parser: HHRssParser,
    db: UserDB,
    user_id: int,
) -> None:
    message_target = update.effective_message
    if not message_target:
        return

    user = await db.get_user(user_id)
    if not user or not user.search_query or not user.area_id or user.salary_min is None:
        _set_state(context, STATE_WAITING_POSITION)
        await message_target.reply_text(
            "Профиль заполнен не полностью. Введите желаемую должность для настройки.",
        )
        return

    try:
        vacancies = await parser.fetch_and_filter_all(
            search_query=user.search_query,
            area_id=user.area_id,
            salary_min=user.salary_min,
            limit=20,
        )
    except Exception as exc:
        logger.exception("Failed to fetch all vacancies for user %s: %s", user_id, exc)
        await message_target.reply_text("Не удалось получить вакансии. Попробуйте позже.")
        return

    if not vacancies:
        await message_target.reply_text("Вакансий по вашему запросу не найдено.")
        return

    await message_target.reply_text(
        _format_vacancies_message(vacancies, header="Все вакансии (от новых к старым):")
    )


def _parse_salary_input(value: str) -> Optional[int]:
    digits_only = "".join(ch for ch in value if ch.isdigit())
    if not digits_only:
        return None
    salary = int(digits_only)
    if salary <= 0:
        return None
    return salary


def _format_vacancies_message(vacancies: list[Vacancy], header: str) -> str:
    lines = [f"{header}\n"]
    for idx, vacancy in enumerate(vacancies, start=1):
        published_msk = vacancy.pub_date.astimezone(MSK_TZ)
        published_text = datetime.strftime(published_msk, "%d.%m.%Y %H:%M")
        lines.append(
            (
                f"{idx}. {vacancy.title}\n"
                f"📅 {published_text} (МСК)\n"
                f"💰 {vacancy.salary_text}\n"
                f"🏢 {vacancy.company}\n"
                f"🔗 {vacancy.link}\n"
            )
        )
    return "\n".join(lines)
