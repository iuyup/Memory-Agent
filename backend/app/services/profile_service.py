import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from app.database import get_db

logger = logging.getLogger(__name__)


class ProfileService:
    CORE_FIELDS = ["name", "occupation", "city", "interests", "age", "education"]
    FIELD_ALIASES = {
        "姓名": "name", "名字": "name", "名称": "name",
        "职业": "occupation", "工作": "occupation", "职位": "occupation", "身份": "occupation",
        "城市": "city", "居住地": "city", "所在城市": "city", "地点": "city",
        "兴趣": "interests", "爱好": "interests", "兴趣爱好": "interests",
        "年龄": "age", "岁数": "age",
        "教育": "education", "学历": "education", "学校": "education", "教育背景": "education",
        "公司": "company", "当前项目": "current_project",
        "游戏": "game", "昵称": "nickname", "称呼": "nickname",
        "游戏角色": "game_character", "游戏ID": "game_id",
        # 追加覆盖截图变体
        "游戏兴趣": "game",
        "偏好角色": "game_character",
        "游戏本命角色": "game_character",
        "技术栈": "tech_stack",
        "编程语言": "tech_stack",
    }

    def __init__(self, db=None):
        """
        Args:
            db: 可选，传入 get_db context manager 工厂函数。
               如果不传，每个方法内部会自己创建连接。
        """
        self._db_factory = db

    @asynccontextmanager
    async def _db(self) -> AsyncIterator:
        """获取数据库连接。优先用注入的工厂，否则创建新连接。"""
        if self._db_factory is not None:
            async with self._db_factory() as conn:
                yield conn
        else:
            async with get_db() as db:
                yield db

    # ─── Public API ────────────────────────────────────────────────────────────

    async def merge_fact(self, user_id: str, fact: dict, source_turn_id: int) -> None:
        """
        Fact Merge 算法 - 四种 case：

        fact 结构: {"field": str, "value": str, "confidence": float, "source": "direct"|"inferred"}
        """
        field_name = fact.get("field", "")
        normalized_field = self.FIELD_ALIASES.get(field_name, field_name)
        field_name = normalized_field  # 后续全部使用归一化后的英文字段名
        new_value = fact.get("value", "")
        confidence = fact.get("confidence", 0.5)
        source = fact.get("source", "inferred")

        if not field_name or not new_value:
            return

        async with self._db() as db:
            # 防重复：在所有记录里查同 user_id + 同归一化 field_name + 同归一化 value
            existing = await self._get_existing_fact_by_normalized(db, user_id, field_name, new_value)
            if existing:
                if self._normalize(new_value) == self._normalize(existing["field_value"]):
                    # 值相同 → 增强置信度
                    new_conf = min(existing["confidence"] + 0.1, 1.0)
                    await self._update_fact_confidence(db, existing["id"], new_conf, datetime.utcnow().isoformat())
                    await db.commit()
                    return
                # 值不同则让后续逻辑处理冲突

            old_fact = await self._get_latest_confirmed(db, user_id, field_name)
            now = datetime.utcnow().isoformat()

            # Case 1: 全新字段
            if old_fact is None:
                if confidence >= 0.7:
                    status = "confirmed"
                else:
                    status = "pending"
                    await self._create_confirmation(
                        db,
                        user_id,
                        field_name,
                        None,
                        new_value,
                        f"你提到你的{field_name}是{new_value}，对吗？",
                    )
                await self._insert_fact(
                    db, user_id, field_name, new_value,
                    confidence, source, source_turn_id, status, now,
                )
                await db.commit()
                return

            # Case 2: 值相同 → 增强置信度
            if self._normalize(new_value) == self._normalize(old_fact["field_value"]):
                new_confidence = min(old_fact["confidence"] + 0.1, 1.0)
                await self._update_fact_confidence(db, old_fact["id"], new_confidence, now)
                await db.commit()
                return

            # Case 3: 值不同，高置信度 + direct source → 替代
            if confidence > old_fact["confidence"] + 0.2 and source == "direct":
                await self._mark_superseded(db, old_fact["id"], now)
                await self._insert_fact(
                    db, user_id, field_name, new_value,
                    confidence, source, source_turn_id, "confirmed", now,
                )
                await db.commit()
                return

            # Case 4: 值不同但不确定 → 入待确认队列
            await self._insert_fact(
                db, user_id, field_name, new_value,
                confidence, source, source_turn_id, "pending", now,
            )
            await self._create_confirmation(
                db,
                user_id,
                field_name,
                old_fact["field_value"],
                new_value,
                f"你之前提到{field_name}是「{old_fact['field_value']}」，现在是「{new_value}」吗？",
            )
            await db.commit()

    async def update_preference(self, user_id: str, pref: dict) -> None:
        """
        偏好更新 - 权重累加 + 指数衰减。

        pref 结构: {"category": str, "value": str}
        """
        category = pref.get("category", "")
        value = pref.get("value", "")

        if not category or not value:
            return

        LAMBDA = 0.05  # 约14天半衰期
        BOOST = 0.3
        INITIAL_WEIGHT = 0.5

        async with self._db() as db:
            existing = await self._get_preference(db, user_id, category, value)
            now = datetime.utcnow().isoformat()

            if existing:
                last_mentioned = datetime.fromisoformat(existing["last_mentioned_at"])
                days_since = (datetime.utcnow() - last_mentioned).total_seconds() / 86400
                decayed_weight = existing["weight"] * math.exp(-LAMBDA * days_since)
                new_weight = min(decayed_weight + BOOST, 1.0)
                await self._update_preference(
                    db,
                    existing["id"],
                    new_weight,
                    existing["mention_count"] + 1,
                    now,
                )
            else:
                await self._insert_preference(
                    db, user_id, category, value, INITIAL_WEIGHT, now,
                )
            await db.commit()

    async def get_profile_snapshot(self, user_id: str) -> dict:
        """
        获取用户画像快照（用于 context assembly）。

        返回 {"facts": [...], "preferences": [...]}
        - facts: 只返回 status=confirmed 且 confidence >= 0.5 的
        - preferences: 先做衰减计算，只返回 weight >= 0.3 的，按 weight 降序
        """
        LAMBDA = 0.05

        async with self._db() as db:
            # 获取 confirmed facts
            rows = await db.execute_fetchall(
                """
                SELECT field_name, field_value, confidence, source, updated_at
                FROM profile_facts
                WHERE user_id = ? AND status = 'confirmed' AND confidence >= 0.5
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            # 同名字段只保留最新一条（防止 race condition 产生多条 confirmed）
            seen: set = set()
            filtered = []
            for row in rows:
                if row["field_name"] not in seen:
                    seen.add(row["field_name"])
                    filtered.append(row)
            rows = filtered
            facts = [
                {
                    "field": row["field_name"],
                    "value": row["field_value"],
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]

            # 获取所有偏好，做衰减计算，过滤
            rows = await db.execute_fetchall(
                """
                SELECT id, category, value, weight, mention_count, last_mentioned_at
                FROM profile_preferences
                WHERE user_id = ?
                """,
                (user_id,),
            )

            preferences = []
            for row in rows:
                last_mentioned = datetime.fromisoformat(row["last_mentioned_at"])
                days_since = (datetime.utcnow() - last_mentioned).total_seconds() / 86400
                decayed = row["weight"] * math.exp(-LAMBDA * days_since)
                if decayed >= 0.3:
                    preferences.append(
                        {
                            "category": row["category"],
                            "value": row["value"],
                            "weight": round(decayed, 3),
                            "mention_count": row["mention_count"],
                        }
                    )

            preferences.sort(key=lambda x: x["weight"], reverse=True)
            return {"facts": facts, "preferences": preferences}

    async def get_missing_core_fields(self, user_id: str) -> list[str]:
        """返回缺失的核心字段列表（用于 Profile Gap hook）"""
        async with self._db() as db:
            confirmed_fields = await self._get_confirmed_field_names(db, user_id)
        return [f for f in self.CORE_FIELDS if f not in confirmed_fields]

    async def get_profile_timeline(self, user_id: str) -> list[dict]:
        """返回画像变更时间线（包括 superseded 的历史值，用于 debug/展示）"""
        async with self._db() as db:
            rows = await self._get_all_facts_for_timeline(db, user_id)
        return [
            {
                "id": row["id"],
                "field_name": row["field_name"],
                "field_value": row["field_value"],
                "confidence": row["confidence"],
                "source": row["source"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    # ─── 私有方法：数据库操作 ───────────────────────────────────────────────────

    def _normalize(self, value: str) -> str:
        """归一化比较（去空格、转小写、去标点）"""
        return re.sub(r"[^\w\u4e00-\u9fff]", "", value.strip().lower())

    async def _get_latest_confirmed(self, db, user_id: str, field_name: str):
        """查询某字段最新的 confirmed 记录"""
        rows = await db.execute_fetchall(
            """
            SELECT id, field_value, confidence, source, status, created_at, updated_at
            FROM profile_facts
            WHERE user_id = ? AND field_name = ? AND status = 'confirmed'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, field_name),
        )
        return rows[0] if rows else None

    async def _get_existing_fact_by_normalized(self, db, user_id: str, field_name: str, value: str):
        """查询某字段、某归一化值的事实记录（不限 status）"""
        rows = await db.execute_fetchall(
            """
            SELECT id, field_name, field_value, confidence, source, status
            FROM profile_facts
            WHERE user_id = ? AND field_name = ?
            """,
            (user_id, field_name),
        )
        for row in rows:
            if self._normalize(value) == self._normalize(row["field_value"]):
                return row
        return None

    async def _insert_fact(
        self,
        db,
        user_id,
        field_name,
        value,
        confidence,
        source,
        turn_id,
        status,
        now,
    ):
        """插入一条 fact 记录"""
        await db.execute(
            """
            INSERT INTO profile_facts
            (user_id, field_name, field_value, confidence, source, source_turn_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, field_name, value, confidence, source, turn_id, status, now, now),
        )

    async def _update_fact_confidence(self, db, fact_id: int, new_confidence: float, now: str):
        """更新 confidence 和 updated_at"""
        await db.execute(
            """
            UPDATE profile_facts SET confidence = ?, updated_at = ? WHERE id = ?
            """,
            (new_confidence, now, fact_id),
        )

    async def _mark_superseded(self, db, fact_id: int, now: str):
        """标记为 superseded（不删除）"""
        await db.execute(
            """
            UPDATE profile_facts SET status = 'superseded', updated_at = ? WHERE id = ?
            """,
            (now, fact_id),
        )

    async def _create_confirmation(
        self, db, user_id, field_name, old_value, new_value, question
    ):
        """创建待确认记录，同一字段已有 pending 则自动 dismiss"""
        now = datetime.utcnow().isoformat()
        # 先把同一用户同一字段的旧 pending 标记为 dismissed
        await db.execute(
            """
            UPDATE pending_confirmations
            SET status = 'dismissed', resolved_at = ?
            WHERE user_id = ? AND field_name = ? AND status = 'pending'
            """,
            (now, user_id, field_name),
        )
        await db.execute(
            """
            INSERT INTO pending_confirmations
            (user_id, field_name, old_value, new_value, question, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, field_name, old_value, new_value, question, now),
        )

    async def _get_preference(self, db, user_id: str, category: str, value: str):
        """查询偏好记录"""
        rows = await db.execute_fetchall(
            """
            SELECT id, weight, mention_count, last_mentioned_at
            FROM profile_preferences
            WHERE user_id = ? AND category = ? AND value = ?
            LIMIT 1
            """,
            (user_id, category, value),
        )
        return rows[0] if rows else None

    async def _insert_preference(
        self, db, user_id: str, category: str, value: str, weight: float, now: str
    ):
        """插入新偏好记录"""
        await db.execute(
            """
            INSERT INTO profile_preferences
            (user_id, category, value, weight, mention_count, last_mentioned_at, created_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (user_id, category, value, weight, now, now),
        )

    async def _update_preference(
        self, db, pref_id: int, weight: float, mention_count: int, now: str
    ):
        """更新偏好权重和提及次数"""
        await db.execute(
            """
            UPDATE profile_preferences
            SET weight = ?, mention_count = ?, last_mentioned_at = ?
            WHERE id = ?
            """,
            (weight, mention_count, now, pref_id),
        )

    async def _get_confirmed_field_names(self, db, user_id: str) -> set[str]:
        """获取用户所有已确认的字段名"""
        rows = await db.execute_fetchall(
            """
            SELECT DISTINCT field_name FROM profile_facts
            WHERE user_id = ? AND status = 'confirmed'
            """,
            (user_id,),
        )
        return {row["field_name"] for row in rows}

    async def _get_all_facts_for_timeline(self, db, user_id: str):
        """获取所有事实记录（包括 superseded），按创建时间排序"""
        return await db.execute_fetchall(
            """
            SELECT id, field_name, field_value, confidence, source, status, created_at, updated_at
            FROM profile_facts
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
