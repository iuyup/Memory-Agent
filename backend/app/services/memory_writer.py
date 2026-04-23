import logging

from app.prompts.extraction import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt
from app.services.profile_service import ProfileService
from app.services.episodic_service import update_turn_metadata

logger = logging.getLogger(__name__)


class MemoryWriter:
    def __init__(self, llm_service, profile_service: ProfileService):
        self.llm_service = llm_service
        self.profile_service = profile_service

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
            await update_turn_metadata(turn_id, summary, tags, has_open_question)
        except Exception as e:
            logger.error(f"update_turn_metadata failed: {e}")

    async def _extract(self, user_message: str, assistant_message: str) -> dict:
        """Call LLM to extract information from the turn."""
        prompt = build_extraction_prompt(user_message, assistant_message)
        return await self.llm_service.generate_extraction(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_content=prompt,
        )


def create_memory_writer():
    from app.database import get_db
    from app.services.llm import llm_service
    from app.services.profile_service import ProfileService
    return MemoryWriter(llm_service, ProfileService(get_db))


memory_writer = create_memory_writer()