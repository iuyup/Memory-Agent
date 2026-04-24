from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth_deps import get_current_user
from app.models.schemas import CurrentUser

router = APIRouter()


class ProfileSnapshotResponse(BaseModel):
    facts: list[dict]
    preferences: list[dict]
    missing_fields: list[str]


@router.get("/{user_id}", response_model=ProfileSnapshotResponse)
async def get_profile(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """获取用户画像快照（需要 auth，且只能查自己的）"""
    if current_user.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot view other user's profile",
        )

    profile_service = request.app.state.profile_service
    snapshot = await profile_service.get_profile_snapshot(user_id)
    missing_fields = await profile_service.get_missing_core_fields(user_id)

    return ProfileSnapshotResponse(
        facts=snapshot["facts"],
        preferences=snapshot["preferences"],
        missing_fields=missing_fields,
    )


@router.get("/{user_id}/timeline")
async def get_profile_timeline(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """获取完整的画像变更历史（包含 superseded 记录）"""
    if current_user.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot view other user's timeline",
        )

    profile_service = request.app.state.profile_service
    return await profile_service.get_profile_timeline(user_id)
