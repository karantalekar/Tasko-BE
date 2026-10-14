from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, HttpUrl

from ..db import public, record
from ..schemas import ID, EventCreate, Page, Timestamp
from ..security import current_user

router = APIRouter(tags=["Meetings"])


class MeetingCreate(EventCreate):
    url: HttpUrl
    team_ids: list[ID] = Field(min_length=1, max_length=100)


class MeetingOut(EventCreate):
    id: ID
    url: str
    team_ids: list[ID]
    created_by: ID
    created_at: Timestamp
    updated_at: Timestamp
    read: bool = False


async def accessible_teams(db, user_id):
    memberships = await db.memberships.distinct("team_id", {"user_id": user_id})
    return await db.teams.distinct("_id", {"$or": [
        {"owner_id": user_id}, {"_id": {"$in": memberships}},
    ]})


@router.post("/meetings", response_model=MeetingOut, status_code=201)
async def create_meeting(body: MeetingCreate, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    ids = list(dict.fromkeys(body.team_ids))
    owned = await db.teams.count_documents({"_id": {"$in": ids}, "owner_id": user["_id"]})
    if user["role"] != "owner" or owned != len(ids):
        raise HTTPException(403, "Select only teams you own")
    data = body.model_dump()
    data.update(url=str(body.url), team_ids=ids)
    doc = record(data, created_by=user["_id"], read_by=[])
    await db.meetings.insert_one(doc)
    return meeting_view(doc, user["_id"])


def meeting_view(doc, user_id):
    value = public(doc)
    value["read"] = user_id in value.pop("read_by", [])
    return value


@router.get("/meetings", response_model=Page[MeetingOut])
async def meetings(
    request: Request, team_id: ID | None = None,
    from_: Timestamp | None = Query(None, alias="from"), to: Timestamp | None = None,
    unread: bool = False, limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000), user=Depends(current_user),
):
    db = request.app.state.db
    ids = await accessible_teams(db, user["_id"])
    if team_id and team_id not in ids:
        raise HTTPException(404, "Team not found")
    query = {"team_ids": {"$in": [team_id] if team_id else ids}}
    if from_ and to and to <= from_:
        raise HTTPException(422, "to must be after from")
    if from_:
        query["ends_at"] = {"$gt": from_}
    if to:
        query["starts_at"] = {"$lt": to}
    if unread:
        query["read_by"] = {"$ne": user["_id"]}
    docs = await db.meetings.find(query).sort([("starts_at", -1), ("_id", -1)]).skip(offset).limit(limit).to_list(length=limit)
    return {"items": [meeting_view(d, user["_id"]) for d in docs],
            "total": await db.meetings.count_documents(query), "limit": limit, "offset": offset}


@router.post("/meetings/{meeting_id}/read", status_code=204)
async def read_meeting(meeting_id: ID, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    ids = await accessible_teams(db, user["_id"])
    result = await db.meetings.update_one(
        {"_id": meeting_id, "team_ids": {"$in": ids}},
        {"$addToSet": {"read_by": user["_id"]}},
    )
    if not result.matched_count:
        raise HTTPException(404, "Meeting not found")
