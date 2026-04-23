import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
import bcrypt

from jose import jwt

from app.auth_deps import get_current_user
from app.database import get_db
from app.models.schemas import (
    CurrentUser,
    UserLogin,
    UserLoginResponse,
    UserRegister,
    UserRegisterResponse,
)
from app.config import settings

router = APIRouter()


@router.post("/register", response_model=UserRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister):
    user_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    created_at = datetime.utcnow().isoformat()

    async with get_db() as db:
        existing = await db.execute_fetchall(
            "SELECT user_id FROM users WHERE username = ?",
            (body.username,),
        )
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

        await db.execute(
            "INSERT INTO users (user_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, body.username, password_hash, created_at),
        )
        await db.commit()

    return UserRegisterResponse(user_id=user_id, username=body.username)


@router.post("/login", response_model=UserLoginResponse)
async def login(body: UserLogin):
    async with get_db() as db:
        row = await db.execute_fetchall(
            "SELECT user_id, username, password_hash FROM users WHERE username = ?",
            (body.username,),
        )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not bcrypt.checkpw(body.password.encode(), row[0]["password_hash"].encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    expire = datetime.utcnow() + timedelta(days=7)
    payload = {
        "user_id": row[0]["user_id"],
        "username": row[0]["username"],
        "exp": expire,
    }
    access_token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    return UserLoginResponse(
        access_token=access_token,
        user_id=row[0]["user_id"],
        username=row[0]["username"],
    )


@router.get("/me", response_model=CurrentUser)
async def get_me(current_user: CurrentUser = Depends(get_current_user)):
    return current_user