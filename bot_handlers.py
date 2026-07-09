from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from hh_rss_parser import HHRssParser
from messages import (
    cabinet_text,
    format_vacancies_message,
    market_stats,
    no_vacancies_all,
    no_vacancies_today,
    onboarding_complete,
    profile_incomplete,
    scan_limit_reached,
    subscription_activated,
    subscription_basic_text,
    subscription_menu_text,
    subscription_premium_text,
    welcome_back,
    welcome_new_user,
)
from user_db import TIER_BASIC, TIER_PREMIUM, UserDB

logger = logging.getLogger(__name__)

MENU_CABINET = "👤 Личный кабинет"
MENU_TODAY_VACANCIES = "🔥 Вакансии за сегодня"
MENU_ALL_VACANCIES = "📋 Все вакансии"
MENU_SUBSCRIPTION = "💎 Подписка"
MENU_HELP = "❓ Помощь"

STATE_WAITING_POSITION = "waiting_position"
STATE_WAITING_SALARY = "waiting_salary"

CITIES = {
    "Москва": 1,
    "Санкт-Петербург": 2,
    "Казань": 88,
    "Екатеринбург": 3,
    "Новосибирск": 4,
}


def get_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [MENU_CABINET, MENU_TODAY_VACANCIES],
            [MENU_ALL_VACANCIES, MENU_SUBSCRIPTION],
            [MENU_HELP],
        ],
        resize_keyboard=True,
    )


def get_city_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=city_name, callback_data=f"city:{area_id}")]
        for city_name, area_id in CITIES.items()
    ]
    return InlineKeyboardMarkup(rows)


def get_cabinet_keyboard(user_notifications: bool) -> InlineKeyboardMarkup:
    notif_label = "🔕 Выключить уведомления" if user_notifications else "🔔 Включить уведомления"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💼 Изменить должность", callback_data="cabinet:position")],
            [InlineKeyboardButton("🏙 Изменить город", callback_data="cabinet:city")],
            [InlineKeyboardButton("💰 Изменить зарплату", callback_data="cabinet:salary")],
            [InlineKeyboardButton(notif_label, callback_data="cabinet:toggle_notif")],
            [
                InlineKeyboardButton("⏸ Приостановить", callback_data="cabinet:pause"),
                InlineKeyboardButton("▶️ Возобновить", callback_data="cabinet:resume"),
            ],
            [InlineKeyboardButton("💎 Управление подпиской", callback_data="sub:menu")],
        ]
    )


def get_subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ℹ️ Базовый", callback_data="sub:info_basic"),
                InlineKeyboardButton("⭐ Купить — 249 ₽", callback_data="sub:buy_basic"),
            ],
            [
                InlineKeyboardButton("ℹ️ Премиум", callback_data="sub:info_premium"),
                InlineKeyboardButton("💎 Купить — 499 ₽", callback_data="sub:buy_premium"),
            ],
            [InlineKeyboardButton("◀️ Назад в кабинет", callback_data="cabinet:show")],
        ]
    )


def _set_state(context: ContextTypes.DEFAULT_TYPE, state: Optional[str]) -> None:
    context.user_data["state"] = state


def _get_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return context.user_data.get("state")


async def _reply_html(
    update: Update,
    text: str,
    reply_markup=None,
) -> None:
    target = update.effective_message
    if not target:
        return
    await target.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    db: UserDB = context.application.bot_data["db"]
    user_id = update.effective_user.id
    name = update.effective_user.first_name or "друг"

    user = await db.get_user(user_id)
    if user:
        await db.set_active(user_id, True)
        await _reply_html(update, welcome_back(name), reply_markup=get_main_menu())
        return

    await db.upsert_user(user_id)
    _set_state(context, STATE_WAITING_POSITION)
    await _reply_html(update, welcome_new_user(name))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return

    db: UserDB = context.application.bot_data["db"]
    parser: HHRssParser = context.application.bot_data["parser"]
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = _get_state(context)

    if text == MENU_CABINET:
        await show_cabinet(update, context, db, user_id)
        return

    if text == MENU_TODAY_VACANCIES:
        await send_today_vacancies(update=update, context=context, parser=parser, db=db, user_id=user_id)
        return

    if text == MENU_ALL_VACANCIES:
        await send_all_vacancies(update=update, context=context, parser=parser, db=db, user_id=user_id)
        return

    if text == MENU_SUBSCRIPTION:
        await _reply_html(update, subscription_menu_text(), reply_markup=get_subscription_keyboard())
        return

    if text == MENU_HELP:
        await _reply_html(update, _help_text())
        return

    if state == STATE_WAITING_POSITION:
        await db.update_search_query(user_id, text)
        _set_state(context, None)

        user = await db.get_user(user_id)
        if user and user.area_id is None:
            await _reply_html(update, "🏙 <b>Выберите город</b> для поиска:", reply_markup=get_city_keyboard())
        elif user and user.salary_min is None:
            _set_state(context, STATE_WAITING_SALARY)
            await _reply_html(
                update,
                "💰 <b>Укажите желаемую зарплату</b> от (в рублях):\n"
                "<i>Например: 180000</i>",
            )
        else:
            await _reply_html(update, "✅ <b>Должность обновлена!</b>", reply_markup=get_main_menu())
        return

    if state == STATE_WAITING_SALARY:
        salary_min = _parse_salary_input(text)
        if salary_min is None:
            await _reply_html(update, "⚠️ Введите корректное число, например: <b>180000</b>")
            return

        await db.update_salary_min(user_id, salary_min)
        _set_state(context, None)

        user = await db.get_user(user_id)
        is_onboarding = user and user.search_query and user.area_id
        if is_onboarding and context.user_data.get("onboarding"):
            context.user_data.pop("onboarding", None)
            await _reply_html(update, onboarding_complete(), reply_markup=get_main_menu())
        else:
            await _reply_html(update, "✅ <b>Зарплата обновлена!</b>", reply_markup=get_main_menu())
        return

    await _reply_html(update, "👇 Используйте кнопки меню ниже.", reply_markup=get_main_menu())


async def show_cabinet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: UserDB,
    user_id: int,
) -> None:
    user = await db.get_user(user_id)
    if not user:
        await db.upsert_user(user_id)
        user = await db.get_user(user_id)
    if not user:
        return

    await _reply_html(
        update,
        cabinet_text(user),
        reply_markup=get_cabinet_keyboard(user.notifications_enabled),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query or not update.effective_user:
        return

    query = update.callback_query
    await query.answer()
    data = query.data or ""
    db: UserDB = context.application.bot_data["db"]
    user_id = update.effective_user.id

    if data.startswith("city:"):
        await _handle_city_selection(query, context, db, user_id, data)
        return

    if data == "cabinet:show":
        user = await db.get_user(user_id)
        if user:
            await query.edit_message_text(
                cabinet_text(user),
                parse_mode=ParseMode.HTML,
                reply_markup=get_cabinet_keyboard(user.notifications_enabled),
            )
        return

    if data == "cabinet:position":
        _set_state(context, STATE_WAITING_POSITION)
        await query.message.reply_text(
            "💼 <b>Введите новую должность:</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "cabinet:city":
        await query.message.reply_text(
            "🏙 <b>Выберите город:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_city_keyboard(),
        )
        return

    if data == "cabinet:salary":
        _set_state(context, STATE_WAITING_SALARY)
        await query.message.reply_text(
            "💰 <b>Укажите желаемую зарплату</b> от (в рублях):",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "cabinet:toggle_notif":
        user = await db.get_user(user_id)
        if user:
            new_state = not user.notifications_enabled
            await db.set_notifications(user_id, new_state)
            user = await db.get_user(user_id)
            if user:
                status = "включены" if new_state else "выключены"
                await query.edit_message_text(
                    cabinet_text(user) + f"\n\n✅ Уведомления <b>{status}</b>.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_cabinet_keyboard(user.notifications_enabled),
                )
        return

    if data == "cabinet:pause":
        await db.set_active(user_id, False)
        await query.message.reply_text(
            "⏸ <b>Рассылка приостановлена.</b>\nВы можете возобновить в личном кабинете.",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "cabinet:resume":
        await db.set_active(user_id, True)
        await query.message.reply_text(
            "▶️ <b>Рассылка возобновлена!</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "sub:menu":
        await query.message.reply_text(
            subscription_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=get_subscription_keyboard(),
        )
        return

    if data == "sub:buy_basic":
        await db.activate_subscription(user_id, TIER_BASIC)
        await query.message.reply_text(
            subscription_activated(TIER_BASIC),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu(),
        )
        return

    if data == "sub:buy_premium":
        await db.activate_subscription(user_id, TIER_PREMIUM)
        await query.message.reply_text(
            subscription_activated(TIER_PREMIUM),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu(),
        )
        return

    if data in ("sub:info_basic", "sub:info_premium"):
        text = subscription_basic_text() if data == "sub:info_basic" else subscription_premium_text()
        await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_subscription_keyboard())


async def _handle_city_selection(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    db: UserDB,
    user_id: int,
    data: str,
) -> None:
    area_id = int(data.split(":", maxsplit=1)[1])
    await db.update_area_id(user_id, area_id)

    user = await db.get_user(user_id)
    if user and user.salary_min is None:
        _set_state(context, STATE_WAITING_SALARY)
        context.user_data["onboarding"] = True
        await query.message.reply_text(
            "💰 <b>Укажите желаемую зарплату</b> от (в рублях):\n"
            "<i>Например: 180000</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        _set_state(context, None)
        await query.message.reply_text(
            "✅ <b>Город обновлён!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu(),
        )


async def _check_scan_access(
    update: Update,
    db: UserDB,
    user_id: int,
) -> bool:
    user = await db.get_user(user_id)
    if not user:
        return False
    if db.can_scan(user):
        return True
    await _reply_html(update, scan_limit_reached(), reply_markup=get_subscription_keyboard())
    return False


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
        await _reply_html(update, profile_incomplete())
        return

    if not await _check_scan_access(update, db, user_id):
        return

    try:
        vacancies = await parser.fetch_and_filter_today(
            search_query=user.search_query,
            area_id=user.area_id,
            salary_min=user.salary_min,
        )
    except Exception as exc:
        logger.exception("Failed to fetch vacancies for user %s: %s", user_id, exc)
        await _reply_html(update, "❌ <b>Не удалось получить вакансии.</b> Попробуйте позже.")
        return

    if not vacancies:
        await _reply_html(update, no_vacancies_today())
        return

    await db.increment_scans(user_id)
    await _deliver_vacancy_results(update, vacancies, "Вакансии за сегодня")


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
        await _reply_html(update, profile_incomplete())
        return

    if not await _check_scan_access(update, db, user_id):
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
        await _reply_html(update, "❌ <b>Не удалось получить вакансии.</b> Попробуйте позже.")
        return

    if not vacancies:
        await _reply_html(update, no_vacancies_all())
        return

    await db.increment_scans(user_id)
    await _deliver_vacancy_results(update, vacancies, "Все вакансии")


async def _deliver_vacancy_results(update: Update, vacancies, header: str) -> None:
    chunks = format_vacancies_message(vacancies, header=header)
    for chunk in chunks:
        await _reply_html(update, chunk)
    await _reply_html(update, market_stats(vacancies))


def _parse_salary_input(value: str) -> Optional[int]:
    digits_only = "".join(ch for ch in value if ch.isdigit())
    if not digits_only:
        return None
    salary = int(digits_only)
    if salary <= 0:
        return None
    return salary


def _help_text() -> str:
    return (
        "❓ <b>Справка StepOne</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔥 Вакансии за сегодня</b> — свежие предложения за текущий день\n"
        "<b>📋 Все вакансии</b> — полный список по вашим фильтрам\n"
        "<b>👤 Личный кабинет</b> — профиль, настройки, подписка\n"
        "<b>💎 Подписка</b> — тарифы и активация\n\n"
        "<b>Тарифы:</b>\n"
        "• 🆓 Бесплатно — 3 скана для знакомства\n"
        "• ⭐ Базовый (249 ₽/мес) — безлимит + ежедневная подборка\n"
        "• 💎 Премиум (499 ₽/мес) — автоуведомления каждые 5 ч\n\n"
        "<i>По вопросам: /start для перезапуска бота</i>"
    )
