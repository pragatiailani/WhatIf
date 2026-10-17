from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.auth import hash_password, issue_token, verify_password, verify_token
from app.constants import MIN_PASSWORD_LENGTH
from app.repo import users
from app.repo.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: str


class SessionOut(BaseModel):
    token: str
    user: UserOut


class SignUp(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^\S+@\S+\.\S+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class SignIn(BaseModel):
    email: str
    password: str


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, name=user.name, email=user.email, created_at=user.created_at.isoformat()
    )


def _session(user: User) -> SessionOut:
    return SessionOut(token=issue_token(user.id), user=_user_out(user))


def optional_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User | None:
    """The signed-in user, or None without a valid token."""
    user_id = verify_token(creds.credentials) if creds else None
    return users.get_user(user_id) if user_id else None


def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})
    return user


@router.post("/signup", status_code=201)
def sign_up(body: SignUp) -> SessionOut:
    user = User(
        name=body.name.strip(),
        email=body.email.strip().lower(),
        password_hash=hash_password(body.password),
    )
    try:
        users.create_user(user)
    except users.EmailTaken as exc:
        raise HTTPException(409, "An account with that email already exists.") from exc
    return _session(user)


@router.post("/signin")
def sign_in(body: SignIn) -> SessionOut:
    user = users.find_by_email(body.email.strip().lower())
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect.")
    return _session(user)


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    return _user_out(user)
