from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

TIER_FREE = "free"
TIER_BASIC = "basic"
TIER_PREMIUM = "premium"

FREE_SCAN_LIMIT = 3
SUBSCRIPTION_DAYS = 30


@dataclass(slots=True)
class UserProfile:
    telegram_id: int
    search_query: Optional[str]
    area_id: Optional[int]
    salary_min: Optional[int]
    is_active: bool
    subscription_tier: str
    subscription_expires_at: Optional[datetime]
    scans_used: int
    notifications_enabled: bool


class UserDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    search_query TEXT,
                    area_id INTEGER,
                    salary_min INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    subscription_tier TEXT NOT NULL DEFAULT 'free',
                    subscription_expires_at TEXT,
                    scans_used INTEGER NOT NULL DEFAULT 0,
                    notifications_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sent_vacancies (
                    telegram_id INTEGER NOT NULL,
                    vacancy_link TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, vacancy_link)
                )
                """
            )
            await self._migrate_columns(conn)
            await conn.commit()

    async def _migrate_columns(self, conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in await cursor.fetchall()}
        migrations = {
            "subscription_tier": "TEXT NOT NULL DEFAULT 'free'",
            "subscription_expires_at": "TEXT",
            "scans_used": "INTEGER NOT NULL DEFAULT 0",
            "notifications_enabled": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in migrations.items():
            if column not in existing:
                await conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

    def _row_to_profile(self, row: aiosqlite.Row) -> UserProfile:
        expires_raw = row["subscription_expires_at"]
        expires_at: Optional[datetime] = None
        if expires_raw:
            expires_at = datetime.fromisoformat(expires_raw)

        return UserProfile(
            telegram_id=row["telegram_id"],
            search_query=row["search_query"],
            area_id=row["area_id"],
            salary_min=row["salary_min"],
            is_active=bool(row["is_active"]),
            subscription_tier=row["subscription_tier"] or TIER_FREE,
            subscription_expires_at=expires_at,
            scans_used=row["scans_used"] or 0,
            notifications_enabled=bool(row["notifications_enabled"]),
        )

    async def get_user(self, telegram_id: int) -> Optional[UserProfile]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT telegram_id, search_query, area_id, salary_min, is_active,
                       subscription_tier, subscription_expires_at, scans_used,
                       notifications_enabled
                FROM users
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_profile(row)

    async def upsert_user(
        self,
        telegram_id: int,
        search_query: Optional[str] = None,
        area_id: Optional[int] = None,
        salary_min: Optional[int] = None,
        is_active: bool = True,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    telegram_id, search_query, area_id, salary_min, is_active,
                    subscription_tier, scans_used, notifications_enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'free', 0, 1, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    search_query = COALESCE(excluded.search_query, users.search_query),
                    area_id = COALESCE(excluded.area_id, users.area_id),
                    salary_min = COALESCE(excluded.salary_min, users.salary_min),
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    search_query,
                    area_id,
                    salary_min,
                    int(is_active),
                    now_iso,
                    now_iso,
                ),
            )
            await conn.commit()

    async def update_search_query(self, telegram_id: int, search_query: str) -> None:
        await self._update_fields(telegram_id, search_query=search_query)

    async def update_area_id(self, telegram_id: int, area_id: int) -> None:
        await self._update_fields(telegram_id, area_id=area_id)

    async def update_salary_min(self, telegram_id: int, salary_min: int) -> None:
        await self._update_fields(telegram_id, salary_min=salary_min)

    async def set_active(self, telegram_id: int, is_active: bool) -> None:
        await self._update_fields(telegram_id, is_active=int(is_active))

    async def set_notifications(self, telegram_id: int, enabled: bool) -> None:
        await self._update_fields(telegram_id, notifications_enabled=int(enabled))

    async def increment_scans(self, telegram_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE users
                SET scans_used = scans_used + 1, updated_at = ?
                WHERE telegram_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), telegram_id),
            )
            await conn.commit()

    async def activate_subscription(self, telegram_id: int, tier: str) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)
        await self._update_fields(
            telegram_id,
            subscription_tier=tier,
            subscription_expires_at=expires_at.isoformat(),
            is_active=1,
        )

    def has_active_subscription(self, user: UserProfile) -> bool:
        if user.subscription_tier == TIER_FREE:
            return False
        if user.subscription_expires_at is None:
            return False
        return user.subscription_expires_at > datetime.now(timezone.utc)

    def can_scan(self, user: UserProfile) -> bool:
        if self.has_active_subscription(user):
            return True
        return user.scans_used < FREE_SCAN_LIMIT

    def is_premium(self, user: UserProfile) -> bool:
        return (
            user.subscription_tier == TIER_PREMIUM
            and self.has_active_subscription(user)
            and user.notifications_enabled
        )

    async def get_active_users(self) -> list[UserProfile]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT telegram_id, search_query, area_id, salary_min, is_active,
                       subscription_tier, subscription_expires_at, scans_used,
                       notifications_enabled
                FROM users
                WHERE is_active = 1
                """
            )
            rows = await cursor.fetchall()
            return [self._row_to_profile(row) for row in rows]

    async def get_premium_users(self) -> list[UserProfile]:
        users = await self.get_active_users()
        return [u for u in users if self.is_premium(u)]

    async def get_subscribed_users(self) -> list[UserProfile]:
        users = await self.get_active_users()
        return [u for u in users if self.has_active_subscription(u)]

    async def get_sent_links(self, telegram_id: int) -> set[str]:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT vacancy_link FROM sent_vacancies WHERE telegram_id = ?",
                (telegram_id,),
            )
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

    async def record_sent_vacancies(self, telegram_id: int, links: list[str]) -> None:
        if not links:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executemany(
                """
                INSERT OR IGNORE INTO sent_vacancies (telegram_id, vacancy_link, sent_at)
                VALUES (?, ?, ?)
                """,
                [(telegram_id, link, now_iso) for link in links],
            )
            await conn.commit()

    async def _update_fields(self, telegram_id: int, **kwargs: object) -> None:
        if not kwargs:
            return

        clauses = [f"{field} = ?" for field in kwargs.keys()]
        values = list(kwargs.values())
        clauses.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(telegram_id)

        query = f"UPDATE users SET {', '.join(clauses)} WHERE telegram_id = ?"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(query, tuple(values))
            await conn.commit()
