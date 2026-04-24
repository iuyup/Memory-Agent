import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncIterator

from app.database import get_db

logger = logging.getLogger(__name__)


class ProactiveService:
    def __init__(self, db_factory, profile_service, episodic_service):
        self._db_factory = db_factory
        self.profile_service = profile_service
        self.episodic_service = episodic_service

    @asynccontextmanager
    async def _db(self) -> AsyncIterator:
        if self._db_factory is not None:
            async with self._db_factory() as db:
                yield db
        else:
            async with get_db() as db:
                yield db

    async def check(self, user_id: str, session_id: str) -> str | None:
        """
        主入口：检查是否需要主动交互。
        按优先级顺序检查 4 类 hook，返回第一个触发的 hint 文本，或 None。
        每次最多触发一个 hook。
        """

        # P0: 冲突确认 — 有待确认的事实冲突
        hint = await self._check_conflict(user_id)
        if hint:
            return hint

        # P1: 画像空缺 — 核心字段缺失（至少聊过 3 轮才触发）
        hint = await self._check_profile_gap(user_id)
        if hint:
            return hint

        # P2: 长间隔回访 — 超过 3 天没来 + 本 session 第一轮
        hint = await self._check_long_absence(user_id, session_id)
        if hint:
            return hint

        # P3: 未闭环话题 — 上次有未回答的问题
        hint = await self._check_open_loop(user_id)
        if hint:
            return hint

        return None

    async def _check_conflict(self, user_id: str) -> str | None:
        """
        检查是否有未解决的待确认项。
        冷静期：同一 hook_type="conflict_confirmation" 24 小时内不重复触发。
        """
        if await self._is_in_cooldown(user_id, "conflict_confirmation", hours=24):
            return None

        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT question FROM pending_confirmations WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            )
        if not rows:
            return None

        await self._log_trigger(user_id, "conflict_confirmation", rows[0]["question"])
        return f"本轮回复中，请自然地向用户确认以下信息：{rows[0]['question']}"

    async def _check_profile_gap(self, user_id: str) -> str | None:
        """
        检查核心字段是否缺失。
        条件：至少聊过 3 轮 + 冷静期 48 小时。
        """
        if await self._is_in_cooldown(user_id, "profile_gap", hours=48):
            return None

        turn_count = await self.episodic_service.get_turn_count(user_id)
        if turn_count < 3:
            return None

        missing = await self.profile_service.get_missing_core_fields(user_id)
        if not missing:
            return None

        # 每次只问一个字段
        field = missing[0]
        field_labels = {
            "name": "名字",
            "occupation": "职业",
            "city": "所在城市",
            "interests": "兴趣爱好",
            "age": "年龄",
            "education": "教育背景",
        }
        label = field_labels.get(field, field)

        await self._log_trigger(user_id, "profile_gap", field)
        return f"如果对话中自然的话，试着了解用户的{label}。不要生硬地直接询问，而是在回复相关话题时自然带出。"

    async def _check_long_absence(self, user_id: str, session_id: str) -> str | None:
        """
        检查是否长时间未来。
        条件：距上次对话 > 3 天 + 本 session 第一轮。
        """
        is_first = await self.episodic_service.is_first_turn_in_session(user_id, session_id)
        if not is_first:
            return None

        last_time_str = await self.episodic_service.get_last_conversation_time(user_id)
        if not last_time_str:
            return None

        last_time = datetime.fromisoformat(last_time_str)
        days_since = (datetime.utcnow() - last_time).total_seconds() / 86400
        if days_since < 3:
            return None

        # 获取上次对话的摘要
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT turn_summary FROM conversation_turns WHERE user_id = ? AND turn_summary IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            )
        last_topic = rows[0]["turn_summary"] if rows else "之前的对话"

        await self._log_trigger(user_id, "long_absence", f"{int(days_since)} days")
        return f"用户已经 {int(days_since)} 天没来了。上次聊的内容是：{last_topic}。可以自然地问候并回顾之前的话题。"

    async def _check_open_loop(self, user_id: str) -> str | None:
        """
        检查是否有未闭环问题。
        冷静期：72 小时。
        """
        if await self._is_in_cooldown(user_id, "open_loop", hours=72):
            return None

        has_open = await self.episodic_service.has_unresolved_questions(user_id)
        if not has_open:
            return None

        # 获取最近一个有 open question 的对话摘要
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT turn_summary FROM conversation_turns WHERE user_id = ? AND has_open_question = 1 ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            )
        topic = rows[0]["turn_summary"] if rows else "上次的问题"

        await self._log_trigger(user_id, "open_loop", topic)
        return f"上次对话中有一个未回答的问题，话题是：{topic}。如果合适的话可以追问一下。"

    # ─── 工具方法 ────────────────────────────────────────────────────────────

    async def _is_in_cooldown(self, user_id: str, hook_type: str, hours: int) -> bool:
        """检查某类 hook 是否在冷静期内"""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT triggered_at FROM proactive_log WHERE user_id = ? AND hook_type = ? ORDER BY triggered_at DESC LIMIT 1",
                (user_id, hook_type),
            )
        if not rows:
            return False
        last_trigger = datetime.fromisoformat(rows[0]["triggered_at"])
        return (datetime.utcnow() - last_trigger).total_seconds() < hours * 3600

    async def _log_trigger(self, user_id: str, hook_type: str, topic: str) -> None:
        """记录触发日志"""
        async with self._db() as db:
            await db.execute(
                "INSERT INTO proactive_log (user_id, hook_type, topic, triggered_at) VALUES (?, ?, ?, ?)",
                (user_id, hook_type, topic, datetime.utcnow().isoformat()),
            )
            await db.commit()
