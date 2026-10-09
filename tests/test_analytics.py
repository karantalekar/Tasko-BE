from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from .conftest import account, setup_team

PARAMS = {"from": "2026-01-01T00:00:00Z", "to": "2026-03-01T00:00:00Z"}


async def test_empty_analytics(client):
    _, _, _, base = await setup_team(client)
    result = await client.get(base + "/analytics", params=PARAMS)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["tasks_completed"] == 0
    assert data["average_completion_seconds"] is None
    assert data["monthly_task_completion"] == []
    assert data["daily_task_completion"] == []
    assert data["total_working_minutes"] == 0
    assert data["active_projects"] == 1
    assert data["team_members"] == 1


async def test_real_calculations_boundaries_tenant_and_cache(client, services):
    _, team, project, base = await setup_team(client)
    db, redis = services
    jan = datetime(2026, 1, 1, tzinfo=UTC)
    feb = datetime(2026, 2, 1, tzinfo=UTC)
    mar = datetime(2026, 3, 1, tzinfo=UTC)
    for i, (completed, duration, tenant) in enumerate(
        [
            (jan, 86400, team["id"]),
            (feb, 259200, team["id"]),
            (mar, 10, team["id"]),
            (jan, 1, "other"),
        ]
    ):
        await db.tasks.insert_one(
            {
                "_id": str(i),
                "team_id": tenant,
                "status": "completed",
                "completed_at": completed,
                "created_at": completed - timedelta(seconds=duration),
            }
        )
    data = (await client.get(base + "/analytics", params=PARAMS)).json()
    assert data["tasks_completed"] == 2
    assert data["average_completion_seconds"] == 172800
    assert data["monthly_task_completion"] == [
        {"month": "2026-01", "count": 1},
        {"month": "2026-02", "count": 1},
    ]
    assert data["daily_task_completion"] == [
        {"date": "2026-01-01", "count": 1},
        {"date": "2026-02-01", "count": 1},
    ]
    assert data["project_status_distribution"] == {
        "pending": 0,
        "in_progress": 1,
        "completed": 0,
        "on_hold": 0,
    }
    again = (await client.get(base + "/analytics", params=PARAMS)).json()
    assert again == data
    await client.put(
        base + "/projects/" + project["id"], json={"name": "Done", "status": "completed"}
    )
    changed = (await client.get(base + "/analytics", params=PARAMS)).json()
    assert changed["active_projects"] == 0
    assert changed["project_status_distribution"]["completed"] == 1


async def test_invalid_ranges(client):
    _, _, _, base = await setup_team(client)
    for start, end in [
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2020-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2026-01-01", "2026-02-01"),
    ]:
        assert (
            await client.get(base + "/analytics", params={"from": start, "to": end})
        ).status_code == 422


async def test_member_analytics_hours_dates_and_cache_are_personal(client, app, services):
    _, _, project, base = await setup_team(client)
    db, _ = services
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as alice,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob,
    ):
        alice_user = await account(alice, "Alice", role="member")
        bob_user = await account(bob, "Bob", role="member")
        for user in (alice_user, bob_user):
            assert (await client.post(base + "/members", json={"email": user["email"]})).status_code == 201
        tasks = []
        for member, day, minutes in ((alice, 5, 90), (bob, 12, 120)):
            task = (await member.post(base + "/tasks", json={
                "title": "Assigned work", "project_id": project["id"],
            })).json()
            tasks.append(task)
            stamp = datetime(2026, 1, day, tzinfo=UTC)
            await db.tasks.update_one({"_id": task["id"]}, {"$set": {
                "status": "completed", "completed_at": stamp,
                "created_at": stamp - timedelta(days=1),
            }})
            assert (await member.post(base + "/timesheets", json={
                "task_id": task["id"], "work_date": "2025-12-15",
                "minutes": minutes, "notes": "Work summary",
            })).status_code == 201
        await db.timesheets.insert_one({
            "team_id": "other-team", "user_id": alice_user["id"], "minutes": 999,
        })
        owner_data = (await client.get(base + "/analytics", params=PARAMS)).json()
        assert owner_data["tasks_completed"] == 2
        assert owner_data["total_working_minutes"] == 210
        alice_data = (await alice.get(base + "/analytics", params=PARAMS)).json()
        assert alice_data["tasks_completed"] == 1
        assert alice_data["total_working_minutes"] == 90
        assert alice_data["daily_task_completion"] == [{"date": "2026-01-05", "count": 1}]
        assert alice_data["monthly_task_completion"] == [{"month": "2026-01", "count": 1}]
        bob_data = (await bob.get(base + "/analytics", params=PARAMS)).json()
        assert bob_data["total_working_minutes"] == 120
        assert bob_data["daily_task_completion"] == [{"date": "2026-01-12", "count": 1}]
        assert (await alice.post(base + "/timesheets", json={
            "task_id": tasks[0]["id"], "work_date": "2026-01-05", "minutes": 30,
            "notes": "More work",
        })).status_code == 201
        assert (await alice.get(base + "/analytics", params=PARAMS)).json()["total_working_minutes"] == 120
        assert (await client.get(base + "/analytics", params=PARAMS)).json()["total_working_minutes"] == 240
