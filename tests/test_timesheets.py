from httpx import ASGITransport, AsyncClient

from .conftest import account, setup_team


async def add_member(client, app, base, name):
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    user = await account(member, name, role="member")
    response = await client.post(base + "/members", json={"email": user["email"]})
    assert response.status_code == 201
    return member, user


async def test_member_submission_and_owner_history_are_scoped(client, app):
    _, _, project, base = await setup_team(client)
    member, user = await add_member(client, app, base, "Alice")
    other, other_user = await add_member(client, app, base, "Bob")
    try:
        task = (await member.post(base + "/tasks", json={
            "title": "Write documentation", "project_id": project["id"],
        })).json()
        body = {
            "task_id": task["id"], "work_date": "2026-10-08",
            "minutes": 90, "notes": "Updated setup instructions",
        }
        created = await member.post(base + "/timesheets", json=body)
        assert created.status_code == 201, created.text
        entry = created.json()
        assert entry["user_id"] == user["id"]
        assert entry["member_name"] == "Alice"
        assert entry["task_title"] == "Write documentation"
        assert entry["project_name"] == project["name"]
        assert (await member.get(base + "/timesheets")).json()["total"] == 1
        assert (await other.get(base + "/timesheets")).json()["total"] == 0
        assert (await other.get(base + "/timesheets", params={
            "member_id": user["id"],
        })).status_code == 403
        assert (await other.post(base + "/timesheets", json=body)).status_code == 403
        assert (await client.post(base + "/timesheets", json=body)).status_code == 403
        assert (await member.post(base + "/timesheets", json={
            **body, "user_id": other_user["id"],
        })).status_code == 422
        history = await client.get(base + "/timesheets")
        assert history.status_code == 200
        assert history.json()["items"][0]["id"] == entry["id"]
        assert (await client.get(base + "/timesheets", params={
            "member_id": other_user["id"],
        })).json()["total"] == 0
        # Keep the original names in history after a task is renamed/reassigned.
        await client.patch(base + "/tasks/" + task["id"], json={
            "version": 1, "title": "Renamed", "assignee_id": other_user["id"],
        })
        history = (await client.get(base + "/timesheets")).json()
        assert history["items"][0]["task_title"] == "Write documentation"
        assert (await member.post(base + "/timesheets", json=body)).status_code == 403
        _, _, other_project, other_base = await setup_team(client)
        other_task = (await client.post(other_base + "/tasks", json={
            "title": "Other team task", "project_id": other_project["id"],
        })).json()
        assert (await client.get(base + "/timesheets")).status_code == 404
        assert (await client.get(other_base + "/timesheets")).json()["total"] == 0
        assert (await member.get(other_base + "/timesheets")).status_code == 404
        assert (await member.post(base + "/timesheets", json={
            **body, "task_id": other_task["id"],
        })).status_code == 404
    finally:
        await member.aclose()
        await other.aclose()


async def test_timesheet_validation_date_filters_and_pagination(client, app):
    _, _, project, base = await setup_team(client)
    member, _ = await add_member(client, app, base, "Member")
    try:
        task = (await member.post(base + "/tasks", json={
            "title": "Work", "project_id": project["id"],
        })).json()
        body = {
            "task_id": task["id"], "work_date": "2026-10-08",
            "minutes": 60, "notes": "Completed work",
        }
        for changes in (
            {"minutes": 0}, {"minutes": -1}, {"minutes": 1441},
            {"minutes": 1.5}, {"notes": "   "}, {"notes": "x" * 2001},
            {"work_date": "2026-02-30"},
        ):
            assert (await member.post(base + "/timesheets", json={
                **body, **changes,
            })).status_code == 422
        for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
            assert (await member.post(base + "/timesheets", json={
                **body, "work_date": day,
            })).status_code == 201
        history = (await client.get(base + "/timesheets", params={
            "limit": 1, "offset": 1,
        })).json()
        assert history["total"] == 3
        assert len(history["items"]) == 1
        assert history["items"][0]["work_date"] == "2026-10-08"
        filtered = (await client.get(base + "/timesheets", params={
            "date_from": "2026-10-08", "date_to": "2026-10-08",
        })).json()
        assert filtered["total"] == 1
        assert (await client.get(base + "/timesheets", params={
            "date_from": "2026-10-09", "date_to": "2026-10-08",
        })).status_code == 422
        # An authenticated write still requires the session's CSRF token.
        del member.headers["X-CSRF-Token"]
        assert (await member.post(base + "/timesheets", json=body)).status_code == 403
    finally:
        await member.aclose()
