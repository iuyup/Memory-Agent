import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from app.database import get_db

logger = logging.getLogger(__name__)


class ProceduralService:
    def __init__(self, db_factory=None):
        self._db_factory = db_factory

    @asynccontextmanager
    async def _get_db(self) -> AsyncIterator:
        if self._db_factory is not None:
            async with self._db_factory() as conn:
                yield conn
        else:
            async with get_db() as conn:
                yield conn

    async def extract_rules(self, user_id: str, llm_service, recent_turns: list[dict]) -> None:
        """
        从最近的对话中提取行为规则。

        调用时机：每 5 轮对话触发一次（在 memory_writer 里检查）。
        """
        if not recent_turns:
            return

        # 格式化对话
        conversation_text = ""
        for t in recent_turns[-10:]:
            conversation_text += f"用户: {t.get('user_message', '')}\n助手: {t.get('assistant_message', '')}\n---\n"

        # 调用 LLM 提取规则
        system_prompt = """分析以下用户与助手的对话历史，提取用户的交互偏好和行为规则。

规则应该描述"助手应该怎么和这个用户交互"，例如：
- "用户偏好简洁回复，不喜欢长篇大论"
- "技术话题时可以使用英文术语"
- "用户喜欢先看结论再看分析"
- "不要在一次回复中问超过一个问题"

只输出 JSON，格式：
{"rules": [{"rule": "规则描述", "confidence": 0.0-1.0}]}

如果没有明显的行为模式，返回空数组：{"rules": []}
不要编造规则，只提取对话中有明确证据的模式。"""

        try:
            result = await llm_service.generate_extraction(
                system_prompt=system_prompt,
                user_content=conversation_text,
            )
        except Exception as e:
            logger.error(f"LLM rule extraction failed: {e}")
            return

        rules = result.get("rules", [])
        if not rules:
            return

        # 获取已有规则做去重
        existing_rules = await self.get_active_rules(user_id)
        existing_texts = {r["rule_text"].lower().strip() for r in existing_rules}

        for rule in rules:
            rule_text = rule.get("rule", "").strip()
            confidence = rule.get("confidence", 0.5)
            if not rule_text:
                continue
            # 简单去重：完全相同或包含关系
            if rule_text.lower() in existing_texts:
                continue
            is_duplicate = False
            for existing in existing_texts:
                if rule_text.lower() in existing or existing in rule_text.lower():
                    is_duplicate = True
                    break
            if is_duplicate:
                continue

            await self._insert_rule(user_id, rule_text, confidence)

    async def get_active_rules(self, user_id: str) -> list[dict]:
        """获取所有活跃的行为规则"""
        async with self._get_db() as db:
            rows = await db.execute_fetchall(
                "SELECT id, rule_text, confidence, created_at FROM procedural_rules WHERE user_id = ? AND active = 1 ORDER BY confidence DESC",
                (user_id,),
            )
        return [
            {
                "id": r["id"],
                "rule_text": r["rule_text"],
                "confidence": r["confidence"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def _insert_rule(self, user_id: str, rule_text: str, confidence: float) -> None:
        now = datetime.utcnow().isoformat()
        async with self._get_db() as db:
            await db.execute(
                "INSERT INTO procedural_rules (user_id, rule_text, confidence, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (user_id, rule_text, confidence, now, now),
            )
            await db.commit()
