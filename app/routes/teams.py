import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..cache import invalidate
from ..db import now, public, record
from ..schemas import ID, MemberCreate, MemberOut, Page, TeamCreate, TeamOrganization, TeamOut
from ..security import current_user, require_owner, team_access

router = APIRouter(prefix="/teams", tags=["Teams"])


@router.post("", response_model=TeamOut, status_code=201)
async def create_team(body: TeamCreate, request: Request, user=Depends(current_user)):
    if user["role"] != "owner":
        raise HTTPException(403, "Only owner accounts can create teams")
    data = body.model_dump()
    data["organization"] = body.organization or body.name
    data["email_domain"] = body.email_domain or user["email"].rsplit("@", 1)[1].lower()
    doc = record(data, owner_id=user["_id"])
    await request.app.state.db.teams.insert_one(doc)
    return public(doc)


@router.get("", response_model=Page[TeamOut])
async def teams(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    user=Depends(current_user),
):
    db = request.app.state.db
    ids = await db.memberships.distinct("team_id", {"user_id": user["_id"]})
    query = {"$or": [{"owner_id": user["_id"]}, {"_id": {"$in": ids}}]}
    docs = (
        await db.teams.find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "items": [public(d) for d in docs],
        "total": await db.teams.count_documents(query),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{team_id}", response_model=TeamOut)
async def team(team_id: ID, access=Depends(team_access)):
    return public(access["team"])


@router.post("/{team_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    team_id: ID, body: MemberCreate, request: Request, access=Depends(team_access)
):
    require_owner(access)
    domain = access["team"].get("email_domain")
    if not domain:
        raise HTTPException(422, "Set the organisation email domain in Settings before adding members")
    if str(body.email).rsplit("@", 1)[1].lower() != domain.lower():
        raise HTTPException(422, f"Member email must use @{domain}")
    db = request.app.state.db
    user = await db.users.find_one({"email": str(body.email).lower()})
    if not user:
        raise HTTPException(404, "User must register before being added")
    if user["_id"] == access["team"]["owner_id"]:
        raise HTTPException(409, "User is already the owner")
    await db.memberships.insert_one(
        record({"team_id": team_id, "user_id": user["_id"], "job_title": body.job_title})
    )
    await invalidate(request.app.state.redis, team_id)
    return {
        "user_id": user["_id"],
        "name": user["name"],
        "email": user["email"],
        "job_title": body.job_title,
        "role": "member",
        "active_tasks": 0,
    }


@router.put("/{team_id}/organization", response_model=TeamOut)
async def update_organization(
    team_id: ID, body: TeamOrganization, request: Request, access=Depends(team_access)
):
    require_owner(access)
    db = request.app.state.db
    ids = await db.memberships.distinct("user_id", {"team_id": team_id})
    mismatches = await db.users.count_documents({
        "_id": {"$in": ids},
        "email": {"$not": re.compile("@" + re.escape(body.email_domain) + "$", re.IGNORECASE)},
    })
    if mismatches:
        raise HTTPException(422, "The domain must match the email addresses of all existing members")
    changes = {**body.model_dump(), "updated_at": now()}
    await db.teams.update_one({"_id": team_id}, {"$set": changes})
    return public({**access["team"], **changes})


@router.get("/{team_id}/members", response_model=Page[MemberOut])
async def members(
    team_id: ID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    access=Depends(team_access),
):
    db = request.app.state.db
    # Join before paging/counting: stale memberships must not crash the workspace
    # or create gaps and incorrect totals when a user record is missing.
    owner_id = access["team"]["owner_id"]
    owner = await db.users.find_one({"_id": owner_id})
    owner_count = int(owner is not None)
    rows = []
    if offset == 0 and owner:
        rows.append({"user": owner, "job_title": "", "role": "owner"})
    remaining = limit - len(rows)
    member_cursor = await db.memberships.aggregate([
        {"$match": {"team_id": team_id, "user_id": {"$ne": owner_id}}},
        {"$sort": {"_id": 1}},
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "user"}},
        {"$unwind": "$user"},
        {"$facet": {
            "items": [{"$skip": max(0, offset - owner_count)}, {"$limit": max(1, remaining)}],
            "total": [{"$count": "count"}],
        }},
    ], maxTimeMS=5000)
    result = (await member_cursor.to_list(length=1))[0]
    rows.extend(result["items"][:remaining])
    total = owner_count + (result["total"][0]["count"] if result["total"] else 0)
    ids = [row["user"]["_id"] for row in rows]
    cursor = await db.tasks.aggregate(
        [
            {
                "$match": {
                    "team_id": team_id,
                    "assignee_id": {"$in": ids},
                    "status": {"$ne": "completed"},
                }
            },
            {"$group": {"_id": "$assignee_id", "count": {"$sum": 1}}},
        ]
    )
    counts = {c["_id"]: c["count"] async for c in cursor}
    items = [
        {
            "user_id": row["user"]["_id"],
            "name": row["user"]["name"],
            "email": row["user"]["email"],
            "job_title": row.get("job_title", ""),
            "role": row.get("role", "member"),
            "active_tasks": counts.get(row["user"]["_id"], 0),
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
