import logging
from datetime import datetime

from app.database import get_db

logger = logging.getLogger(__name__)


class ProfileService:
    async def merge_fact(self, user_id: str, fact: dict, turn_id: int) -> None:
        """Upsert a profile fact. If field already exists, update if new confidence is higher."""
        field_name = fact.get("field", "")
        field_value = fact.get("value", "")
        confidence = fact.get("confidence", 0.5)
        source = fact.get("source", "inferred")

        if not field_name or not field_value:
            return

        now = datetime.utcnow().isoformat()

        async with get_db() as db:
            # Check existing
            existing = await db.execute_fetchall(
                """
                SELECT id, confidence FROM profile_facts
                WHERE user_id = ? AND field_name = ? AND status = 'confirmed'
                """,
                (user_id, field_name),
            )

            if existing and existing[0]["confidence"] >= confidence:
                # Skip if existing is more confident
                return

            await db.execute(
                """
                INSERT OR REPLACE INTO profile_facts
                (user_id, field_name, field_value, confidence, source, source_turn_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
                """,
                (user_id, field_name, field_value, confidence, source, turn_id, now, now),
            )
            await db.commit()

    async def update_preference(self, user_id: str, pref: dict) -> None:
        """Upsert a preference. Increase mention_count if already exists."""
        category = pref.get("category", "")
        value = pref.get("value", "")

        if not category or not value:
            return

        now = datetime.utcnow().isoformat()

        async with get_db() as db:
            existing = await db.execute_fetchall(
                """
                SELECT id, mention_count FROM profile_preferences
                WHERE user_id = ? AND category = ? AND value = ?
                """,
                (user_id, category, value),
            )

            if existing:
                new_count = existing[0]["mention_count"] + 1
                await db.execute(
                    """
                    UPDATE profile_preferences
                    SET mention_count = ?, last_mentioned_at = ?
                    WHERE id = ?
                    """,
                    (new_count, now, existing[0]["id"]),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO profile_preferences
                    (user_id, category, value, weight, mention_count, last_mentioned_at, created_at)
                    VALUES (?, ?, ?, 0.5, 1, ?, ?)
                    """,
                    (user_id, category, value, now, now),
                )
            await db.commit()