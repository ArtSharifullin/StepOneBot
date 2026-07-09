from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiosqlite


@dataclass(slots=True)
class UserProfile:
    telegram_id: int
    search_query: Optional[str]
    area_id: Optional[int]
    salary_min: Optional[int]
    is_active: bool


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await conn.commit()

    async def get_user(self, telegram_id: int) -> Optional[UserProfile]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT telegram_id, search_query, area_id, salary_min, is_active
                FROM users
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            return UserProfile(
                telegram_id=row["telegram_id"],
                search_query=row["search_query"],
                area_id=row["area_id"],
                salary_min=row["salary_min"],
                is_active=bool(row["is_active"]),
            )

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
                    telegram_id, search_query, area_id, salary_min, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
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

    async def get_active_users(self) -> list[UserProfile]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT telegram_id, search_query, area_id, salary_min, is_active
                FROM users
                WHERE is_active = 1
                """
            )
            rows = await cursor.fetchall()
            return [
                UserProfile(
                    telegram_id=row["telegram_id"],
                    search_query=row["search_query"],
                    area_id=row["area_id"],
                    salary_min=row["salary_min"],
                    is_active=bool(row["is_active"]),
                )
                for row in rows
            ]

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
