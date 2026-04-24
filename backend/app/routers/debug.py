from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth_deps import get_current_user
from app.database import get_db
from app.models.schemas import CurrentUser

router = APIRouter()


class MemoryStatusResponse(BaseModel):
    user_id: str
    profile: dict
    episodic: dict
    proactive: dict
    procedural: dict
    vector: dict
    context_preview: str


@router.get("/memory-status/{user_id}", response_model=MemoryStatusResponse)
async def get_memory_status(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """记忆系统 debug 端点——返回完整的记忆状态快照，供调试面板展示。"""
    if current_user.user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot view other user's memory")

    profile_data = {"facts_count": 0, "confirmed_facts": [], "pending_facts": [], "superseded_facts_count": 0, "preferences": [], "missing_core_fields": []}
    episodic_data = {"total_turns": 0, "total_summaries": 0, "last_conversation_at": None, "sessions_count": 0}
    proactive_data = {"pending_confirmations": [], "recent_triggers": [], "last_hint": None}
    procedural_data = {"rules": []}
    vector_data = {"indexed_turns": 0}
    context_preview = ""

    async with get_db() as db:
        # ── Profile ────────────────────────────────────────────────────────────
        try:
            fact_rows = await db.execute_fetchall(
                """
                SELECT field_name, field_value, confidence, source, status, updated_at
                FROM profile_facts
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            )
            confirmed_facts = [
                {
                    "field": r["field_name"],
                    "value": r["field_value"],
                    "confidence": r["confidence"],
                    "source": r["source"],
                    "updated_at": r["updated_at"],
                }
                for r in fact_rows
                if r["status"] == "confirmed"
            ]
            pending_facts = [
                {
                    "field": r["field_name"],
                    "value": r["field_value"],
                    "confidence": r["confidence"],
                    "source": r["source"],
                    "updated_at": r["updated_at"],
                }
                for r in fact_rows
                if r["status"] == "pending"
            ]
            superseded_count = sum(1 for r in fact_rows if r["status"] == "superseded")

            pref_rows = await db.execute_fetchall(
                """
                SELECT category, value, weight, mention_count, last_mentioned_at
                FROM profile_preferences
                WHERE user_id = ?
                """,
                (user_id,),
            )
            preferences = [
                {
                    "category": r["category"],
                    "value": r["value"],
                    "weight": r["weight"],
                    "mention_count": r["mention_count"],
                    "last_mentioned_at": r["last_mentioned_at"],
                }
                for r in pref_rows
            ]

            confirmed_fields = {r["field_name"] for r in fact_rows if r["status"] == "confirmed"}
            core_fields = ["name", "occupation", "city", "interests", "age", "education"]
            missing_core_fields = [f for f in core_fields if f not in confirmed_fields]

            profile_data = {
                "facts_count": len(confirmed_facts),
                "confirmed_facts": confirmed_facts,
                "pending_facts": pending_facts,
                "superseded_facts_count": superseded_count,
                "preferences": preferences,
                "missing_core_fields": missing_core_fields,
            }
        except Exception:
            pass

        # ── Episodic ────────────────────────────────────────────────────────────
        try:
            turn_rows = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM conversation_turns WHERE user_id = ?",
                (user_id,),
            )
            total_turns = turn_rows[0]["cnt"] if turn_rows else 0

            summary_rows = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM conversation_summaries WHERE user_id = ?",
                (user_id,),
            )
            total_summaries = summary_rows[0]["cnt"] if summary_rows else 0

            last_conv_rows = await db.execute_fetchall(
                """
                SELECT created_at FROM conversation_turns
                WHERE user_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,),
            )
            last_conversation_at = last_conv_rows[0]["created_at"] if last_conv_rows else None

            session_rows = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM sessions WHERE user_id = ?",
                (user_id,),
            )
            sessions_count = session_rows[0]["cnt"] if session_rows else 0

            episodic_data = {
                "total_turns": total_turns,
                "total_summaries": total_summaries,
                "last_conversation_at": last_conversation_at,
                "sessions_count": sessions_count,
            }
        except Exception:
            pass

        # ── Proactive ───────────────────────────────────────────────────────────
        try:
            confirm_rows = await db.execute_fetchall(
                """
                SELECT id, field_name, old_value, new_value, question, status, created_at
                FROM pending_confirmations
                WHERE user_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            pending_confirmations = [
                {
                    "id": r["id"],
                    "field_name": r["field_name"],
                    "old_value": r["old_value"],
                    "new_value": r["new_value"],
                    "question": r["question"],
                    "created_at": r["created_at"],
                }
                for r in confirm_rows
            ]

            proactive_rows = await db.execute_fetchall(
                """
                SELECT hook_type, topic, triggered_at
                FROM proactive_log
                WHERE user_id = ?
                ORDER BY triggered_at DESC
                LIMIT 5
                """,
                (user_id,),
            )
            recent_triggers = [
                {
                    "hook_type": r["hook_type"],
                    "topic": r["topic"],
                    "triggered_at": r["triggered_at"],
                }
                for r in proactive_rows
            ]

            # last hint from most recent trigger
            last_hint = proactive_rows[0]["topic"] if proactive_rows else None

            proactive_data = {
                "pending_confirmations": pending_confirmations,
                "recent_triggers": recent_triggers,
                "last_hint": last_hint,
            }
        except Exception:
            pass

        # ── Procedural ───────────────────────────────────────────────────────────
        try:
            rules_rows = await db.execute_fetchall(
                """
                SELECT id, rule_text, confidence, created_at
                FROM procedural_rules
                WHERE user_id = ? AND active = 1
                ORDER BY confidence DESC
                """,
                (user_id,),
            )
            procedural_data = {
                "rules": [
                    {
                        "id": r["id"],
                        "rule_text": r["rule_text"],
                        "confidence": r["confidence"],
                        "created_at": r["created_at"],
                    }
                    for r in rules_rows
                ]
            }
        except Exception:
            pass

        # ── Vector ─────────────────────────────────────────────────────────────
        try:
            vec_rows = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM turn_embeddings",
            )
            vector_data = {"indexed_turns": vec_rows[0]["cnt"] if vec_rows else 0}
        except Exception:
            vector_data = {"indexed_turns": 0}

        # ── Context preview ─────────────────────────────────────────────────────
        context_assembler = getattr(request.app.state, "context_assembler", None)
        if context_assembler:
            try:
                system_prompt, _ = await context_assembler.build(
                    user_id,
                    "[debug preview - no user message]",
                    session_id="__debug__",
                )
                context_preview = system_prompt[:500] + ("..." if len(system_prompt) > 500 else "")
            except Exception:
                context_preview = "(context assembly failed)"
        else:
            context_preview = "(context_assembler not initialized)"

        return MemoryStatusResponse(
            user_id=user_id,
            profile=profile_data,
            episodic=episodic_data,
            proactive=proactive_data,
            procedural=procedural_data,
            vector=vector_data,
            context_preview=context_preview,
        )
