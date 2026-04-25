import logging

from app.prompts.extraction import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt
from app.services.episodic_service import EpisodicService
from app.services.profile_service import ProfileService

logger = logging.getLogger(__name__)

DEDUP_SYSTEM_PROMPT = """你是一个数据去重助手。比较"已有事实"和"新提取事实"，执行以下操作：

1. 如果新事实和已有事实语义完全相同（比如"城市:肇庆"和"居住地:肇庆"），标记为 SKIP
2. 如果新事实是对已有事实的更新（值不同但同一个概念），标记为 UPDATE 并标准化 field 名称
3. 如果新事实是全新信息，标记为 ADD

对保留的事实，将 field 名称标准化为以下之一：
name, age, occupation, city, company, education, interests, game, game_character, game_id, nickname, tech_stack, current_project
如果不属于以上任何类别，用简短英文 snake_case。

只输出 JSON：
{"deduplicated_facts": [{"field": "标准化字段名", "value": "值", "confidence": 0.0-1.0, "source": "direct|inferred", "action": "ADD|UPDATE|SKIP"}]}
如果所有新事实都是重复的，返回空数组：{"deduplicated_facts": []}"""


class MemoryWriter:
    def __init__(
        self,
        llm_service,
        profile_service: ProfileService,
        episodic_service: EpisodicService,
        vector_service=None,
        procedural_service=None,
    ):
        self.llm_service = llm_service
        self.profile_service = profile_service
        self.episodic_service = episodic_service
        self.vector_service = vector_service
        self.procedural_service = procedural_service

    async def process_turn(
        self,
        user_id: str,
        turn_id: int,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Main entry point, called in BackgroundTasks after chat response."""
        try:
            extraction = await self._extract(user_message, assistant_message)
        except Exception as e:
            logger.error(f"Extraction failed for turn {turn_id}: {e}")
            extraction = {
                "facts": [],
                "preferences": [],
                "tags": [],
                "summary": "",
                "has_open_question": False,
            }

        facts = extraction.get("facts", [])
        preferences = extraction.get("preferences", [])
        tags = extraction.get("tags", [])
        summary = extraction.get("summary", "")
        has_open_question = extraction.get("has_open_question", False)

        # Phase 1.5: LLM deduplication — 用 LLM 对新 facts 和已有 facts 做语义去重
        try:
            facts = await self._deduplicate_facts(user_id, facts)
        except Exception as e:
            logger.error(f"Deduplication failed, falling back to raw facts: {e}")
            # 兜底：同归一化 field 只保留 confidence 最高的
            facts = self._dedupe_within_list(facts)

        # 2a. Merge facts
        for fact in facts:
            try:
                await self.profile_service.merge_fact(user_id, fact, turn_id)
            except Exception as e:
                logger.error(f"merge_fact failed: {e}")

        # 2b. Update preferences
        for pref in preferences:
            try:
                await self.profile_service.update_preference(user_id, pref)
            except Exception as e:
                logger.error(f"update_preference failed: {e}")

        # 2c. Update turn metadata
        try:
            await self.episodic_service.update_turn_metadata(
                turn_id, summary, tags, has_open_question
            )
        except Exception as e:
            logger.error(f"update_turn_metadata failed: {e}")

        # 2d. 向量索引
        if self.vector_service is not None:
            try:
                text = f"{user_message} {assistant_message}"
                await self.vector_service.index_turn(turn_id, text)
            except Exception as e:
                logger.error(f"vector indexing failed: {e}")

        # 2e. 检查是否需要压缩
        try:
            await self.episodic_service.check_and_compress(user_id, self.llm_service)
        except Exception as e:
            logger.error(f"compress check failed: {e}")

        # 2f. 每 5 轮触发行为规则提取
        if self.procedural_service:
            try:
                turn_count = await self.episodic_service.get_turn_count(user_id)
                if turn_count > 0 and turn_count % 5 == 0:
                    recent = await self.episodic_service.get_recent_turns_raw(user_id, limit=10)
                    await self.procedural_service.extract_rules(user_id, self.llm_service, recent)
            except Exception as e:
                logger.error(f"procedural extraction failed: {e}")

    async def _extract(self, user_message: str, assistant_message: str) -> dict:
        """Call LLM to extract information from the turn."""
        prompt = build_extraction_prompt(user_message, assistant_message)
        return await self.llm_service.generate_extraction(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_content=prompt,
        )

    async def _deduplicate_facts(self, user_id: str, new_facts: list[dict]) -> list[dict]:
        """
        用 LLM 将新提取的 facts 与已有 facts 做语义去重和合并。
        返回去重后的 facts 列表（已过滤掉 action==SKIP 的记录）。
        """
        if not new_facts:
            return []

        # 先做一轮内部去重（同一归一化 field 只保留 confidence 最高的）
        new_facts = self._dedupe_within_list(new_facts)

        # 获取已有 confirmed facts
        profile = await self.profile_service.get_profile_snapshot(user_id)
        existing_facts = profile.get("facts", [])

        if not existing_facts:
            return new_facts

        # 构造 LLM 去重 prompt
        existing_str = "\n".join([f"- {f['field']}: {f['value']}" for f in existing_facts])
        new_str = "\n".join([f"- {f.get('field', '')}: {f.get('value', '')}" for f in new_facts])

        user_content = f"已有事实：\n{existing_str}\n\n新提取事实：\n{new_str}"

        try:
            result = await self.llm_service.generate_extraction(
                system_prompt=DEDUP_SYSTEM_PROMPT,
                user_content=user_content,
            )
            deduped = result.get("deduplicated_facts", [])
            # 过滤掉 SKIP 的
            return [f for f in deduped if f.get("action") != "SKIP"]
        except Exception as e:
            logger.warning(f"LLM dedup failed, falling back to deduplicated facts: {e}")
            return new_facts

    def _dedupe_within_list(self, facts: list[dict]) -> list[dict]:
        """对同一批 facts 内部去重（同一个归一化 field 只保留 confidence 最高的）"""
        seen: dict = {}
        for fact in facts:
            field = fact.get("field", "")
            normalized = ProfileService.FIELD_ALIASES.get(field, field)
            conf = fact.get("confidence", 0)
            if normalized not in seen or conf > seen[normalized].get("confidence", 0):
                seen[normalized] = {**fact, "field": normalized}
        return list(seen.values())
