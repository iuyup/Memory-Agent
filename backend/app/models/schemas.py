from pydantic import BaseModel


class UserRegister(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class UserRegisterResponse(BaseModel):
    user_id: str
    username: str


class UserLoginResponse(BaseModel):
    access_token: str
    user_id: str
    username: str


class TokenPayload(BaseModel):
    user_id: str
    username: str
    exp: int


class CurrentUser(BaseModel):
    user_id: str
    username: str