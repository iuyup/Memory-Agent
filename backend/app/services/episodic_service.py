import logging
from datetime import datetime

from app.database import get_db

logger = logging.getLogger(__name__)


async def update_turn_metadata(
    turn_id: int,
    summary: str,
    tags: list[str],
    has_open_question: bool,
) -> None:
    if not summary and not tags:
        return

    tags_str = ",".join(tags) if tags else None
    async with get_db() as db:
        await db.execute(
            """
            UPDATE conversation_turns
            SET turn_summary = ?, tags = ?, has_open_question = ?
            WHERE turn_id = ?
            """,
            (summary or None, tags_str, 1 if has_open_question else 0, turn_id),
        )
        await db.commit()