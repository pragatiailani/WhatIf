import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import inkitt
from app.auth import hash_password, issue_token, verify_password, verify_token
from app.constants import (
    MIN_PASSWORD_LENGTH,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    REFRESH_TOKEN_TTL_SECONDS,
)
from app.repo import refresh_tokens, users
from app.repo.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: str
    inkitt_connected: bool = False


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


class InkittToken(BaseModel):
    token: str = Field(min_length=1, max_length=500)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        created_at=user.created_at.isoformat(),
        inkitt_connected=user.inkitt_pat is not None,
    )


def _session(user: User, request: Request, response: Response) -> SessionOut:
    """A new access token for the body, and a new refresh token in an httpOnly cookie."""
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_tokens.issue(user.id),
        max_age=REFRESH_TOKEN_TTL_SECONDS,
        path=REFRESH_COOKIE_PATH,  # sent to /auth/* only
        httponly=True,  # not readable from JavaScript
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return SessionOut(token=issue_token(user.id), user=_user_out(user))


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


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
def sign_up(body: SignUp, request: Request, response: Response) -> SessionOut:
    user = User(
        name=body.name.strip(),
        email=body.email.strip().lower(),
        password_hash=hash_password(body.password),
    )
    try:
        users.create_user(user)
    except users.EmailTaken as exc:
        log.info("signup rejected: email already registered")
        raise HTTPException(409, "An account with that email already exists.") from exc
    log.info("signup user=%s", user.id)
    return _session(user, request, response)


@router.post("/signin")
def sign_in(body: SignIn, request: Request, response: Response) -> SessionOut:
    user = users.find_by_email(body.email.strip().lower())
    if user is None or not verify_password(body.password, user.password_hash):
        log.warning("signin failed: %s", "unknown email" if user is None else "wrong password")
        raise HTTPException(401, "Email or password is incorrect.")
    log.info("signin user=%s", user.id)
    return _session(user, request, response)


@router.post("/refresh", response_model=SessionOut)
def refresh(request: Request, response: Response) -> SessionOut | JSONResponse:
    """Trade the refresh cookie for a new access token. The cookie is single use: it is replaced."""
    cookie = request.cookies.get(REFRESH_COOKIE)
    user_id = refresh_tokens.consume(cookie) if cookie else None
    user = users.get_user(user_id) if user_id else None
    if user is None:
        rejected = JSONResponse({"detail": "sign in required"}, status_code=401)
        _clear_cookie(rejected)  # a raised HTTPException would drop this header
        return rejected
    return _session(user, request, response)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    """Forget this browser's refresh token. The access token simply expires."""
    if cookie := request.cookies.get(REFRESH_COOKIE):
        refresh_tokens.revoke(cookie)
    _clear_cookie(response)


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
    return _user_out(user)


@router.put("/me/inkitt")
async def connect_inkitt(
    body: InkittToken, user: Annotated[User, Depends(current_user)]
) -> UserOut:
    """Check the Inkitt access token with Inkitt, then save it encrypted."""
    token = body.token.strip()
    if not await inkitt.verify(token):
        raise HTTPException(400, "Inkitt did not accept that token. Check it and try again.")
    users.set_inkitt_pat(user.id, token)
    return _user_out(users.get_user(user.id) or user)


@router.delete("/me/inkitt")
def disconnect_inkitt(user: Annotated[User, Depends(current_user)]) -> UserOut:
    users.set_inkitt_pat(user.id, None)
    return _user_out(users.get_user(user.id) or user)
