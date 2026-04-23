from pydantic import BaseModel


# TODO: Define Pydantic schemas
class UserBase(BaseModel):
    email: str


class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: int
    created_at: str