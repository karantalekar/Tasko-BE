from datetime import UTC, datetime
from uuid import uuid4


def now():
    return datetime.now(UTC)


def record(data: dict, **extra):
    return {"_id": uuid4().hex, **data, **extra, "created_at": now(), "updated_at": now()}


def public(doc):
    return {"id": doc["_id"], **{k: v for k, v in doc.items() if k != "_id"}}


async def ensure_indexes(db):
    await db.meetings.create_index([("team_ids", 1), ("starts_at", -1), ("_id", -1)])
    await db.users.create_index("email", unique=True)
    await db.teams.create_index("owner_id")
    await db.memberships.create_index([("team_id", 1), ("user_id", 1)], unique=True)
    await db.memberships.create_index([("user_id", 1), ("team_id", 1)])
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.sessions.create_index("user_id")
    for name in ("projects", "tasks", "events"):
        await db[name].create_index([("team_id", 1), ("created_at", -1), ("_id", -1)])
    await db.projects.create_index([("team_id", 1), ("status", 1)])
    await db.tasks.create_index([("team_id", 1), ("status", 1), ("completed_at", 1)])
    await db.tasks.create_index([("team_id", 1), ("project_id", 1), ("status", 1)])
    await db.tasks.create_index([("team_id", 1), ("assignee_id", 1), ("due_at", 1)])
    await db.events.create_index([("team_id", 1), ("starts_at", 1), ("ends_at", 1)])
    await db.time_entries.create_index(
        [("user_id", 1)], unique=True, partialFilterExpression={"running": True}
    )
    await db.time_entries.create_index([("team_id", 1), ("user_id", 1), ("started_at", -1)])
    await db.timesheets.create_index([("team_id", 1), ("work_date", -1), ("_id", -1)])
    await db.timesheets.create_index(
        [("team_id", 1), ("user_id", 1), ("work_date", -1), ("_id", -1)]
    )
