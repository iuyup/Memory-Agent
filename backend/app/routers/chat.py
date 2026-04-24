import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from app.auth_deps import get_current_user
from app.database import get_db
from app.models.schemas import CurrentUser
from app.services.llm import get_llm_service

router = APIRouter()


class ChatSendRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatSendResponse(BaseModel):
    session_id: str
    turn_id: int
    response: str


class TurnResponse(BaseModel):
    turn_id: int
    user_message: str
    assistant_message: str
    created_at: str


@router.post("/send", response_model=ChatSendResponse)
async def chat_send(
    request: Request,
    body: ChatSendRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user.user_id

    async with get_db() as db:
        # Get or create session
        if body.session_id:
            session_row = await db.execute_fetchall(
                "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
                (body.session_id, user_id),
            )
            if not session_row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
            session_id = body.session_id
        else:
            session_id = str(uuid.uuid4())
            started_at = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO sessions (session_id, user_id, started_at) VALUES (?, ?, ?)",
                (session_id, user_id, started_at),
            )
            await db.commit()

    # Call LLM with full context assembly
    context_assembler = request.app.state.context_assembler
    proactive_service = request.app.state.proactive_service
    proactive_hint = await proactive_service.check(user_id, session_id)
    system_prompt, recent_turns = await context_assembler.build(
        user_id, body.message, session_id, proactive_hint=proactive_hint
    )
    messages = recent_turns + [{"role": "user", "content": body.message}]
    try:
        response_text = await get_llm_service().generate_chat(system_prompt, messages)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM service error: {type(e).__name__}: {e}",
        )

    # Store turn
    created_at = datetime.utcnow().isoformat()
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO conversation_turns
            (user_id, session_id, user_message, assistant_message, turn_summary, tags, has_open_question, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, 0, ?)
            """,
            (user_id, session_id, body.message, response_text, created_at),
        )
        await db.commit()
        turn_id = cursor.lastrowid

    # Enqueue async memory writing (does not block response)
    background_tasks.add_task(
        request.app.state.memory_writer.process_turn,
        user_id,
        turn_id,
        body.message,
        response_text,
    )

    return ChatSendResponse(session_id=session_id, turn_id=turn_id, response=response_text)


@router.get("/history/{session_id}")
async def chat_history(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    async with get_db() as db:
        rows = await db.execute_fetchall(
            """
            SELECT turn_id, user_message, assistant_message, created_at
            FROM conversation_turns
            WHERE session_id = ? AND user_id = ?
            ORDER BY created_at ASC
            """,
            (session_id, current_user.user_id),
        )

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return [
        {
            "turn_id": row["turn_id"],
            "user_message": row["user_message"],
            "assistant_message": row["assistant_message"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]