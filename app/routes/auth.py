from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from ..db import now, record
from ..schemas import Login, Preferences, ProfileUpdate, Register, SessionOut, UserOut
from ..security import (
    COOKIE,
    DUMMY_HASH,
    current_user,
    digest,
    ensure_account_role,
    make_session,
    passwords,
    user_view,
    verify_password,
)

router = APIRouter(tags=["Authentication and profile"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
async def register(body: Register, request: Request):
    doc = record(
        {
            "name": body.name,
            "role": body.role,
            "email": str(body.email).lower(),
            "password_hash": await run_in_threadpool(passwords.hash, body.password),
            "preferences": Preferences().model_dump(),
        }
    )
    await request.app.state.db.users.insert_one(doc)
    return user_view(doc)


@router.post("/auth/login", response_model=SessionOut)
async def login(body: Login, request: Request, response: Response):
    user = await request.app.state.db.users.find_one({"email": str(body.email).lower()})
    valid = await verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not valid or not user:
        raise HTTPException(401, "Invalid email or password")
    await ensure_account_role(request, user)
    return await make_session(request, response, user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, user=Depends(current_user)):
    await request.app.state.db.sessions.delete_one({"_id": digest(request.cookies[COOKIE])})
    response.delete_cookie(
        COOKIE,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite=request.app.state.settings.cookie_samesite,
    )


@router.get("/auth/session", response_model=SessionOut)
async def session(request: Request, user=Depends(current_user)):
    return {
        "user": user_view(user),
        "csrf_token": request.state.session["csrf"],
        "expires_at": request.state.session["expires_at"],
    }


@router.get("/users/me", response_model=UserOut)
async def me(user=Depends(current_user)):
    return user_view(user)


@router.patch("/users/me", response_model=UserOut)
async def update_me(body: ProfileUpdate, request: Request, user=Depends(current_user)):
    changes = {"name": body.name, "updated_at": now()}
    await request.app.state.db.users.update_one({"_id": user["_id"]}, {"$set": changes})
    return user_view({**user, **changes})


@router.put("/users/me/preferences", response_model=Preferences)
async def preferences(body: Preferences, request: Request, user=Depends(current_user)):
    await request.app.state.db.users.update_one(
        {"_id": user["_id"]}, {"$set": {"preferences": body.model_dump(), "updated_at": now()}}
    )
    return body
