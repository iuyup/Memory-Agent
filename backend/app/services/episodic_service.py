import json
import logging
from datetime import datetime
from typing import AsyncIterator

from app.database import get_db

logger = logging.getLogger(__name__)


class EpisodicService:
    def __init__(self, db=None):
        self._db_factory = db

    async def _db(self) -> AsyncIterator:
        if self._db_factory is not None:
            async with self._db_factory() as db:
                yield db
        else:
            async with get_db() as db:
                yield db

    async def update_turn_metadata(
        self,
        turn_id: int,
        summary: str | None,
        tags: list[str] | None,
        has_open_question: bool,
    ) -> None:
        """更新 conversation_turns 的 summary、tags、has_open_question 字段"""
        if not summary and not tags and has_open_question is None:
            return

        tags_str = json.dumps(tags, ensure_ascii=False) if tags else None
        has_open = 1 if has_open_question else 0

        async with self._db() as db:
            await db.execute(
                """
                UPDATE conversation_turns
                SET turn_summary = ?, tags = ?, has_open_question = ?
                WHERE turn_id = ?
                """,
                (summary or None, tags_str, has_open, turn_id),
            )
            await db.commit()

    async def get_recent_turns(self, user_id: str, limit: int = 10) -> list[dict]:
        """
        获取最近 N 轮原始对话，按时间正序返回。
        返回 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        async with self._db() as db:
            rows = await db.execute_fetchall(
                """
                SELECT user_message, assistant_message, created_at
                FROM conversation_turns
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )

        result = []
        for row in reversed(rows):
            result.append({"role": "user", "content": row["user_message"]})
            result.append({"role": "assistant", "content": row["assistant_message"]})
        return result

    async def get_turn_count(self, user_id: str) -> int:
        """获取用户总对话轮次数"""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM conversation_turns WHERE user_id = ?",
                (user_id,),
            )
        return rows[0]["cnt"] if rows else 0

    async def get_last_conversation_time(self, user_id: str) -> str | None:
        """获取最后一次对话时间"""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                """
                SELECT created_at FROM conversation_turns
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
        return rows[0]["created_at"] if rows else None

    async def has_unresolved_questions(self, user_id: str) -> bool:
        """
        检查是否有未闭环问题（最近一轮的 has_open_question = True）
        """
        async with self._db() as db:
            rows = await db.execute_fetchall(
                """
                SELECT has_open_question FROM conversation_turns
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
        if not rows:
            return False
        return rows[0]["has_open_question"] == 1

    async def is_first_turn_in_session(self, user_id: str, session_id: str) -> bool:
        """检查是否是当前 session 的第一轮"""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                """
                SELECT COUNT(*) as cnt FROM conversation_turns
                WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
            )
        count = rows[0]["cnt"] if rows else 0
        return count == 0
