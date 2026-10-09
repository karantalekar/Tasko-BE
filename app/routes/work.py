import re
from datetime import date
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pymongo import ReturnDocument

from ..cache import invalidate
from ..db import now, public, record
from ..schemas import (
    ID,
    EventCreate,
    EventOut,
    Page,
    Priority,
    ProjectCreate,
    ProjectOut,
    Status,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    TimerOut,
    TimerStart,
    TimesheetCreate,
    TimesheetOut,
    Timestamp,
)
from ..security import require_owner, team_access

router = APIRouter(prefix="/teams/{team_id}", tags=["Projects, tasks, calendar and timer"])


@router.post("/timesheets", response_model=TimesheetOut, status_code=201)
async def submit_timesheet(
    team_id: ID, body: TimesheetCreate, request: Request, access=Depends(team_access)
):
    if access["owner"] or access["user"]["role"] != "member":
        raise HTTPException(403, "Only members can submit timesheets")
    db = request.app.state.db
    task = await fetch(db, "tasks", team_id, body.task_id)
    if task["assignee_id"] != access["user"]["_id"]:
        raise HTTPException(403, "Timesheets must be for a task assigned to you")
    project = await fetch(db, "projects", team_id, task["project_id"])
    doc = record(
        {**body.model_dump(), "work_date": body.work_date.isoformat()},
        team_id=team_id,
        user_id=access["user"]["_id"],
        member_name=access["user"]["name"],
        task_title=task["title"],
        project_name=project["name"],
    )
    await db.timesheets.insert_one(doc)
    await invalidate(request.app.state.redis, team_id)
    return public(doc)


@router.get("/timesheets", response_model=Page[TimesheetOut])
async def timesheets(
    team_id: ID,
    request: Request,
    member_id: ID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    access=Depends(team_access),
):
    query = {"team_id": team_id}
    if not access["owner"]:
        if member_id is not None and member_id != access["user"]["_id"]:
            raise HTTPException(403, "Members can view only their own timesheets")
        query["user_id"] = access["user"]["_id"]
    elif member_id is not None:
        query["user_id"] = member_id
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "End date must be on or after start date")
    if date_from or date_to:
        query["work_date"] = {}
        if date_from:
            query["work_date"]["$gte"] = date_from.isoformat()
        if date_to:
            query["work_date"]["$lte"] = date_to.isoformat()
    return await page(request.app.state.db.timesheets, query, limit, offset, "work_date", "desc")


async def fetch(db, collection, team_id, item_id):
    doc = await db[collection].find_one({"_id": item_id, "team_id": team_id})
    if not doc:
        raise HTTPException(404, "Resource not found")
    return doc


async def validate_assignee(db, access, assignee_id):
    if assignee_id is None or assignee_id == access["team"]["owner_id"]:
        return
    if not await db.memberships.find_one(
        {"team_id": access["team"]["_id"], "user_id": assignee_id}
    ):
        raise HTTPException(422, "Assignee must belong to this team")


async def page(collection, query, limit, offset, sort, direction):
    order = 1 if direction == "asc" else -1
    docs = (
        await collection.find(query)
        .sort([(sort, order), ("_id", order)])
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "items": [public(d) for d in docs],
        "total": await collection.count_documents(query),
        "limit": limit,
        "offset": offset,
    }


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    team_id: ID, body: ProjectCreate, request: Request, access=Depends(team_access)
):
    require_owner(access)
    doc = record(body.model_dump(), team_id=team_id)
    await request.app.state.db.projects.insert_one(doc)
    await invalidate(request.app.state.redis, team_id)
    return public(doc)


@router.get("/projects", response_model=Page[ProjectOut])
async def projects(
    team_id: ID,
    request: Request,
    status: Status | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    sort: Literal["created_at", "due_at", "name"] = "created_at",
    direction: Literal["asc", "desc"] = "desc",
    access=Depends(team_access),
):
    query = {"team_id": team_id}
    if status:
        query["status"] = status
    return await page(request.app.state.db.projects, query, limit, offset, sort, direction)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def project(team_id: ID, project_id: ID, request: Request, access=Depends(team_access)):
    return public(await fetch(request.app.state.db, "projects", team_id, project_id))


@router.put("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    team_id: ID, project_id: ID, body: ProjectCreate, request: Request, access=Depends(team_access)
):
    require_owner(access)
    doc = await request.app.state.db.projects.find_one_and_update(
        {"_id": project_id, "team_id": team_id},
        {"$set": {**body.model_dump(), "updated_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(404, "Project not found")
    await invalidate(request.app.state.redis, team_id)
    return public(doc)


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(team_id: ID, body: TaskCreate, request: Request, access=Depends(team_access)):
    if not access["owner"]:
        if body.assignee_id not in (None, access["user"]["_id"]):
            raise HTTPException(403, "Members can create tasks only for themselves")
        body = body.model_copy(update={"assignee_id": access["user"]["_id"]})
    db = request.app.state.db
    await fetch(db, "projects", team_id, body.project_id)
    await validate_assignee(db, access, body.assignee_id)
    doc = record(body.model_dump(), team_id=team_id, status="pending", completed_at=None, version=1)
    await db.tasks.insert_one(doc)
    await invalidate(request.app.state.redis, team_id)
    return public(doc)


@router.get("/tasks", response_model=Page[TaskOut])
async def tasks(
    team_id: ID,
    request: Request,
    status: Status | None = None,
    active: bool | None = None,
    project_id: ID | None = None,
    assignee_id: ID | None = None,
    priority: Priority | None = None,
    q: str | None = Query(None, min_length=1, max_length=100),
    due_from: Timestamp | None = None,
    due_to: Timestamp | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    sort: Literal["created_at", "due_at", "title", "updated_at"] = "created_at",
    direction: Literal["asc", "desc"] = "desc",
    access=Depends(team_access),
):
    if status is not None and active is not None:
        raise HTTPException(422, "Use status or active, not both")
    query = {"team_id": team_id}
    for key, value in (
        ("status", status),
        ("project_id", project_id),
        ("assignee_id", assignee_id),
        ("priority", priority),
    ):
        if value is not None:
            query[key] = value
    if active is not None:
        query["status"] = {"$ne": "completed"} if active else "completed"
    if q:
        query["title"] = {"$regex": re.escape(q), "$options": "i"}
    if due_from and due_to and due_to <= due_from:
        raise HTTPException(422, "due_to must be after due_from")
    if due_from or due_to:
        query["due_at"] = {}
        if due_from:
            query["due_at"]["$gte"] = due_from
        if due_to:
            query["due_at"]["$lt"] = due_to
    return await page(request.app.state.db.tasks, query, limit, offset, sort, direction)


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def task(team_id: ID, task_id: ID, request: Request, access=Depends(team_access)):
    return public(await fetch(request.app.state.db, "tasks", team_id, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    team_id: ID, task_id: ID, body: TaskUpdate, request: Request, access=Depends(team_access)
):
    db = request.app.state.db
    old = await fetch(db, "tasks", team_id, task_id)
    if old["version"] != body.version:
        raise HTTPException(409, "Task changed; reload it and retry with its current version")
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if not access["owner"]:
        if old["assignee_id"] != access["user"]["_id"] or "assignee_id" in changes:
            raise HTTPException(
                403, "Members may edit only their assigned tasks and cannot reassign"
            )
    if "assignee_id" in changes:
        await validate_assignee(db, access, changes["assignee_id"])
    stamp = now()
    if "status" in changes and changes["status"] != old["status"]:
        changes["completed_at"] = stamp if changes["status"] == "completed" else None
    changes["updated_at"] = stamp
    doc = await db.tasks.find_one_and_update(
        {"_id": task_id, "team_id": team_id, "version": body.version},
        {"$set": changes, "$inc": {"version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(409, "Task changed; reload it and retry with its current version")
    await invalidate(request.app.state.redis, team_id)
    return public(doc)


@router.post("/events", response_model=EventOut, status_code=201)
async def create_event(
    team_id: ID, body: EventCreate, request: Request, access=Depends(team_access)
):
    doc = record(body.model_dump(), team_id=team_id)
    await request.app.state.db.events.insert_one(doc)
    return public(doc)


@router.get("/events", response_model=Page[EventOut])
async def events(
    team_id: ID,
    request: Request,
    from_: Timestamp = Query(alias="from"),
    to: Timestamp = Query(),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    access=Depends(team_access),
):
    if to <= from_ or (to - from_).total_seconds() > 366 * 86400:
        raise HTTPException(422, "Date range must be positive and at most 366 days")
    query = {"team_id": team_id, "starts_at": {"$lt": to}, "ends_at": {"$gt": from_}}
    return await page(request.app.state.db.events, query, limit, offset, "starts_at", "asc")


@router.put("/events/{event_id}", response_model=EventOut)
async def update_event(
    team_id: ID, event_id: ID, body: EventCreate, request: Request, access=Depends(team_access)
):
    require_owner(access)
    doc = await request.app.state.db.events.find_one_and_update(
        {"_id": event_id, "team_id": team_id},
        {"$set": {**body.model_dump(), "updated_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(404, "Event not found")
    return public(doc)


@router.post("/time-entries/start", response_model=TimerOut, status_code=201)
async def start_timer(team_id: ID, body: TimerStart, request: Request, access=Depends(team_access)):
    task = await fetch(request.app.state.db, "tasks", team_id, body.task_id)
    if not access["owner"] and task["assignee_id"] != access["user"]["_id"]:
        raise HTTPException(403, "Task is not assigned to you")
    doc = {
        "_id": uuid4().hex,
        "team_id": team_id,
        "user_id": access["user"]["_id"],
        "task_id": body.task_id,
        "started_at": now(),
        "ended_at": None,
        "running": True,
    }
    await request.app.state.db.time_entries.insert_one(doc)
    return public(doc)


@router.post("/time-entries/{entry_id}/stop", response_model=TimerOut)
async def stop_timer(team_id: ID, entry_id: ID, request: Request, access=Depends(team_access)):
    doc = await request.app.state.db.time_entries.find_one_and_update(
        {"_id": entry_id, "team_id": team_id, "user_id": access["user"]["_id"], "running": True},
        {"$set": {"running": False, "ended_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(404, "Running time entry not found")
    return public(doc)


@router.get("/time-entries", response_model=Page[TimerOut])
async def time_entries(
    team_id: ID,
    request: Request,
    running: bool | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    access=Depends(team_access),
):
    query = {"team_id": team_id, "user_id": access["user"]["_id"]}
    if running is not None:
        query["running"] = running
    return await page(request.app.state.db.time_entries, query, limit, offset, "started_at", "desc")
