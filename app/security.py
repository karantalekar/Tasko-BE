import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from .db import now

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("non-user-dummy-password")
COOKIE = "tasko_session"


def digest(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def user_view(user):
    return {
        "id": user["_id"],
        "role": user["role"],
        **{k: user[k] for k in ("name", "email", "preferences", "created_at", "updated_at")},
    }


async def ensure_account_role(request: Request, user):
    # Preserve existing team owners; other legacy accounts become members.
    if "role" not in user:
        db = request.app.state.db
        role = "owner" if await db.teams.find_one({"owner_id": user["_id"]}) else "member"
        await db.users.update_one({"_id": user["_id"], "role": {"$exists": False}}, {"$set": {"role": role}})
        updated = await db.users.find_one({"_id": user["_id"]})
        user["role"] = updated["role"]


async def make_session(request: Request, response: Response, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = now() + timedelta(hours=request.app.state.settings.session_hours)
    # Rotate this browser's existing session at login.
    old = request.cookies.get(COOKIE)
    if old:
        await request.app.state.db.sessions.delete_one({"_id": digest(old)})
    await request.app.state.db.sessions.insert_one(
        {"_id": digest(token), "user_id": user["_id"], "csrf": csrf, "expires_at": expires}
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        max_age=request.app.state.settings.session_hours * 3600,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_view(user), "csrf_token": csrf, "expires_at": expires}


async def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 128:
        raise HTTPException(401, "Authentication required")
    session = await request.app.state.db.sessions.find_one(
        {"_id": digest(token), "expires_at": {"$gt": now()}}
    )
    if not session:
        raise HTTPException(401, "Session expired or invalid")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(supplied, session["csrf"]):
            raise HTTPException(403, "Invalid CSRF token")
    user = await request.app.state.db.users.find_one({"_id": session["user_id"]})
    if not user:
        raise HTTPException(401, "Session invalid")
    await ensure_account_role(request, user)
    request.state.session = session
    return user


async def team_access(team_id: str, request: Request, user=Depends(current_user)):
    team = await request.app.state.db.teams.find_one({"_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    owner = team["owner_id"] == user["_id"] and user["role"] == "owner"
    if not owner and not await request.app.state.db.memberships.find_one(
        {"team_id": team_id, "user_id": user["_id"]}
    ):
        raise HTTPException(404, "Team not found")
    return {"team": team, "user": user, "owner": owner}


def require_owner(access):
    if not access["owner"]:
        raise HTTPException(403, "Owner permission required")


async def verify_password(plain, hashed):
    return await run_in_threadpool(passwords.verify, plain, hashed)
