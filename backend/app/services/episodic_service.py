import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from app.database import get_db

logger = logging.getLogger(__name__)


class EpisodicService:
    def __init__(self, db=None):
        self._db_factory = db

    @asynccontextmanager
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

    async def get_mid_term_summaries(
        self, user_id: str, days: int = 3, max_tokens: int = 1000
    ) -> list[dict]:
        """
        获取中期记忆摘要。
        策略：取 max(最近 N 天, 最近 20 轮) 范围内的 conversation_summaries。
        如果没有压缩过的 summaries，则 fallback 到 conversation_turns 的 turn_summary 字段。
        返回 [{"summary": str, "covers": "turn 5-15", "level": 0, "created_at": str}, ...]
        按时间正序排列。
        """
        from datetime import timedelta

        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        async with self._db() as db:
            # 查压缩过的 summaries
            rows = await db.execute_fetchall(
                """
                SELECT covers_turn_start, covers_turn_end, summary_text, level, created_at
                FROM conversation_summaries
                WHERE user_id = ? AND created_at >= ?
                ORDER BY covers_turn_start ASC
                """,
                (user_id, cutoff),
            )

            if rows:
                return [
                    {
                        "summary": row["summary_text"],
                        "covers": f"turn {row['covers_turn_start']}-{row['covers_turn_end']}",
                        "level": row["level"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]

            # fallback: 取最近 20 轮的 turn_summary
            rows = await db.execute_fetchall(
                """
                SELECT turn_id, turn_summary, created_at
                FROM conversation_turns
                WHERE user_id = ? AND turn_summary IS NOT NULL AND created_at >= ?
                ORDER BY turn_id ASC
                LIMIT 20
                """,
                (user_id, cutoff),
            )
            return [
                {
                    "summary": row["turn_summary"],
                    "covers": f"turn {row['turn_id']}",
                    "level": -1,
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    async def check_and_compress(self, user_id: str, llm_service) -> None:
        """
        检查是否需要触发压缩，如果需要则执行。

        压缩规则：
        - 每积累 10 轮未压缩的 turn_summary，触发一次 level-0 压缩
        - 每积累 5 条 level-0 summary，触发一次 level-1 压缩（summary of summaries）
        """
        async with self._db() as db:
            # a. 查询最新 level-0 summary 的 covers_turn_end
            row = await db.execute_fetchall(
                """
                SELECT covers_turn_end FROM conversation_summaries
                WHERE user_id = ? AND level = 0
                ORDER BY covers_turn_end DESC LIMIT 1
                """,
                (user_id,),
            )
            last_compressed_end = row[0]["covers_turn_end"] if row else 0

            # b. 查询从 last_compressed_end+1 之后有多少轮有 turn_summary
            rows = await db.execute_fetchall(
                """
                SELECT turn_id, turn_summary FROM conversation_turns
                WHERE user_id = ? AND turn_id > ? AND turn_summary IS NOT NULL
                ORDER BY turn_id ASC
                """,
                (user_id, last_compressed_end),
            )
            pending_count = len(rows)

            # c. 达到 10 轮，触发 level-0 压缩
            if pending_count >= 10:
                summaries = [r["turn_summary"] for r in rows]
                compressed = await self._compress_summaries(
                    llm_service, summaries, level=0
                )
                turn_start = rows[0]["turn_id"]
                turn_end = rows[-1]["turn_id"]
                now = datetime.utcnow().isoformat()
                await db.execute(
                    """
                    INSERT INTO conversation_summaries
                    (user_id, covers_turn_start, covers_turn_end, summary_text, level, created_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (user_id, turn_start, turn_end, compressed, now),
                )
                await db.commit()
                last_compressed_end = turn_end

            # d. 检查 level-0 是否积累 >= 5 条
            l0_rows = await db.execute_fetchall(
                """
                SELECT id, covers_turn_start, covers_turn_end, summary_text FROM conversation_summaries
                WHERE user_id = ? AND level = 0
                ORDER BY covers_turn_start ASC
                """,
                (user_id,),
            )
            if len(l0_rows) >= 5:
                summaries = [r["summary_text"] for r in l0_rows]
                compressed = await self._compress_summaries(
                    llm_service, summaries, level=1
                )
                turn_start = l0_rows[0]["covers_turn_start"]
                turn_end = l0_rows[-1]["covers_turn_end"]
                now = datetime.utcnow().isoformat()
                await db.execute(
                    """
                    INSERT INTO conversation_summaries
                    (user_id, covers_turn_start, covers_turn_end, summary_text, level, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (user_id, turn_start, turn_end, compressed, now),
                )
                await db.commit()

                # Delete consumed level-0 summaries
                l0_ids = [r["id"] for r in l0_rows]
                placeholders = ",".join("?" * len(l0_ids))
                await db.execute(
                    f"DELETE FROM conversation_summaries WHERE id IN ({placeholders})",
                    l0_ids,
                )
                await db.commit()

    async def _compress_summaries(
        self, llm_service, summaries: list[str], level: int
    ) -> str:
        """调用 LLM 将多条摘要压缩为一段。"""
        system_prompt = "你是一个摘要压缩助手。将用户提供的多轮对话摘要压缩为一段 200 字以内的简洁总结，保留关键事实、用户意图和重要决策，去掉冗余细节。只输出压缩后的总结，不要其他内容。"
        joined = "\n---\n".join(summaries)
        user_content = f"请压缩以下摘要列表：\n\n{joined}"
        return await llm_service.generate_chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=512,
        )

    async def get_recent_turns_for_context(
        self, user_id: str, session_id: str, limit: int = 10
    ) -> list[dict]:
        """
        获取最近 N 轮原始对话，格式化为 LLM messages 格式。
        返回 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        按时间正序。
        """
        async with self._db() as db:
            rows = await db.execute_fetchall(
                """
                SELECT user_message, assistant_message
                FROM conversation_turns
                WHERE user_id = ? AND session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, session_id, limit),
            )
        result = []
        for row in reversed(rows):
            result.append({"role": "user", "content": row["user_message"]})
            result.append({"role": "assistant", "content": row["assistant_message"]})
        return result
