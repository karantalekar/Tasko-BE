import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from redis.exceptions import RedisError

from ..cache import generation
from ..db import now
from ..schemas import ID, AnalyticsOut, Timestamp
from ..security import team_access

router = APIRouter(prefix="/teams/{team_id}", tags=["Analytics"])


async def calculate(db, team_id, start, end, member_id=None):
    task_query = {
        "team_id": team_id,
        "status": "completed",
        "completed_at": {"$gte": start, "$lt": end},
    }
    if member_id:
        task_query["assignee_id"] = member_id
    cursor = await db.tasks.aggregate(
        [
            {
                "$match": task_query
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$completed_at"}},
                    "count": {"$sum": 1},
                    "duration_ms": {"$sum": {"$subtract": ["$completed_at", "$created_at"]}},
                }
            },
            {"$sort": {"_id": 1}},
        ],
        maxTimeMS=5000,
    )
    days = {row["_id"]: row async for row in cursor}
    count = sum(row["count"] for row in days.values())
    duration = sum(row["duration_ms"] for row in days.values())
    months = {}
    for day, row in days.items():
        month = day[:7]
        months[month] = months.get(month, 0) + row["count"]
    time_query = {"team_id": team_id}
    if member_id:
        time_query["user_id"] = member_id
    time_cursor = await db.timesheets.aggregate([
        {"$match": time_query},
        {"$group": {"_id": None, "minutes": {"$sum": "$minutes"}}},
    ], maxTimeMS=5000)
    total_minutes = 0
    async for row in time_cursor:
        total_minutes += row["minutes"]
    project_cursor = await db.projects.aggregate(
        [
            {"$match": {"team_id": team_id}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ],
        maxTimeMS=5000,
    )
    distribution = {s: 0 for s in ("pending", "in_progress", "completed", "on_hold")}
    async for row in project_cursor:
        if row["_id"] in distribution:
            distribution[row["_id"]] = row["count"]
    team = await db.teams.find_one({"_id": team_id})
    owner_id = team["owner_id"]
    member_cursor = await db.memberships.aggregate([
        {"$match": {"team_id": team_id, "user_id": {"$ne": owner_id}}},
        {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "_id", "as": "user"}},
        {"$unwind": "$user"},
        {"$count": "count"},
    ], maxTimeMS=5000)
    member_rows = await member_cursor.to_list(length=1)
    member_count = (member_rows[0]["count"] if member_rows else 0) + await db.users.count_documents({"_id": owner_id})
    return AnalyticsOut.model_validate(
        {
            "from": start,
            "to": end,
            "generated_at": now(),
            "tasks_completed": count,
            "active_projects": distribution["in_progress"],
            "team_members": member_count,
            "average_completion_seconds": duration / count / 1000 if count else None,
            "monthly_task_completion": [
                {"month": month, "count": value} for month, value in sorted(months.items())
            ],
            "daily_task_completion": [
                {"date": day, "count": row["count"]} for day, row in days.items()
            ],
            "total_working_minutes": total_minutes,
            "project_status_distribution": distribution,
        }
    )


@router.get("/analytics", response_model=AnalyticsOut)
async def analytics(
    team_id: ID,
    request: Request,
    from_: Timestamp = Query(alias="from"),
    to: Timestamp = Query(),
    access=Depends(team_access),
):
    if to <= from_ or (to - from_).total_seconds() > 366 * 86400:
        raise HTTPException(422, "Date range must be positive and at most 366 days")
    redis = request.app.state.redis
    key = None
    try:
        version = await generation(redis, team_id)
        scope = "owner" if access["owner"] else access["user"]["_id"]
        key = f"tasko:analytics:v3:{team_id}:{scope}:{version}:{from_.isoformat()}:{to.isoformat()}"
        cached = await redis.get(key)
        if cached:
            return AnalyticsOut.model_validate(json.loads(cached))
    except RedisError:
        pass  # Cache failure can fall back to MongoDB; rate limiting remains fail-closed.
    member_id = None if access["owner"] else access["user"]["_id"]
    result = await calculate(request.app.state.db, team_id, from_, to, member_id)
    if key:
        try:
            await redis.set(
                key,
                result.model_dump_json(by_alias=True),
                ex=request.app.state.settings.cache_seconds,
            )
        except RedisError:
            pass
    return result
