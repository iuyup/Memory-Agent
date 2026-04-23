from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth_deps import get_current_user
from app.database import get_db
from app.models.schemas import CurrentUser

router = APIRouter()


class ConfirmationResponse(BaseModel):
    id: int
    field_name: str
    old_value: str | None
    new_value: str
    question: str
    status: str
    created_at: str


class ResolveRequest(BaseModel):
    action: str  # "confirm" | "reject" | "dismiss"


@router.get("/", response_model=list[ConfirmationResponse])
async def list_confirmations(
    current_user: CurrentUser = Depends(get_current_user),
):
    """返回当前用户所有 status=pending 的待确认项"""
    async with get_db() as db:
        rows = await db.execute_fetchall(
            """
            SELECT id, field_name, old_value, new_value, question, status, created_at
            FROM pending_confirmations
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            """,
            (current_user.user_id,),
        )

    return [
        {
            "id": row["id"],
            "field_name": row["field_name"],
            "old_value": row["old_value"],
            "new_value": row["new_value"],
            "question": row["question"],
            "status": row["status"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.post("/{confirmation_id}/resolve")
async def resolve_confirmation(
    confirmation_id: int,
    body: ResolveRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    处理确认请求：

    - confirm: 将对应的 pending fact 改为 confirmed，旧的 confirmed 标记 superseded
    - reject:  将 pending fact 标记为 superseded，保留原 confirmed 不变
    - dismiss: 将 confirmation 状态改为 dismissed，pending fact 也标记 superseded
    """
    action = body.action
    if action not in ("confirm", "reject", "dismiss"):
        raise HTTPException(status_code=400, detail="action must be confirm/reject/dismiss")

    now = datetime.utcnow().isoformat()

    async with get_db() as db:
        # 验证 confirmation 属于当前用户且处于 pending 状态
        rows = await db.execute_fetchall(
            """
            SELECT id, user_id, field_name, old_value, new_value, status
            FROM pending_confirmations
            WHERE id = ? AND user_id = ?
            """,
            (confirmation_id, current_user.user_id),
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Confirmation not found")
        confirmation = rows[0]

        if confirmation["status"] != "pending":
            raise HTTPException(status_code=400, detail="Confirmation already resolved")

        if action == "confirm":
            # 旧的 confirmed → superseded
            if confirmation["old_value"] is not None:
                await db.execute(
                    """
                    UPDATE profile_facts
                    SET status = 'superseded', updated_at = ?
                    WHERE user_id = ? AND field_name = ? AND status = 'confirmed'
                    """,
                    (now, current_user.user_id, confirmation["field_name"]),
                )
            # pending fact → confirmed
            await db.execute(
                """
                UPDATE profile_facts
                SET status = 'confirmed', updated_at = ?
                WHERE user_id = ? AND field_name = ? AND field_value = ? AND status = 'pending'
                """,
                (now, current_user.user_id, confirmation["field_name"], confirmation["new_value"]),
            )

        elif action == "reject":
            # pending fact → superseded
            await db.execute(
                """
                UPDATE profile_facts
                SET status = 'superseded', updated_at = ?
                WHERE user_id = ? AND field_name = ? AND field_value = ? AND status = 'pending'
                """,
                (now, current_user.user_id, confirmation["field_name"], confirmation["new_value"]),
            )

        elif action == "dismiss":
            # pending fact → superseded
            await db.execute(
                """
                UPDATE profile_facts
                SET status = 'superseded', updated_at = ?
                WHERE user_id = ? AND field_name = ? AND field_value = ? AND status = 'pending'
                """,
                (now, current_user.user_id, confirmation["field_name"], confirmation["new_value"]),
            )

        # 更新 confirmation 状态
        await db.execute(
            """
            UPDATE pending_confirmations
            SET status = ?, resolved_at = ?
            WHERE id = ?
            """,
            (action, now, confirmation_id),
        )

        await db.commit()

    return {"status": "ok"}
