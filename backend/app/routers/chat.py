import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth_deps import get_current_user
from app.database import get_db
from app.models.schemas import CurrentUser
from app.services.llm import get_llm_service

router = APIRouter()

SYSTEM_PROMPT = "你是一个个人 AI 助理，友好、有帮助。"
HISTORY_TURNS = 10


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

        # Get recent turns for context
        history_rows = await db.execute_fetchall(
            """
            SELECT user_message, assistant_message
            FROM conversation_turns
            WHERE user_id = ? AND session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, session_id, HISTORY_TURNS),
        )

    # Build messages list (reverse to get chronological order)
    # Each turn has both user and assistant messages - add both
    messages = []
    for row in reversed(history_rows):
        messages.append({"role": "user", "content": row["user_message"]})
        messages.append({"role": "assistant", "content": row["assistant_message"]})
    messages.append({"role": "user", "content": body.message})

    # Call LLM
    response_text = await get_llm_service().generate_chat(SYSTEM_PROMPT, messages)

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