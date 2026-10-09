from datetime import timedelta

from httpx import ASGITransport, AsyncClient

from app.db import now

from .conftest import account, setup_team


async def test_auth_validation_hashing_and_logout(client, services):
    response = await client.post(
        "/api/v1/auth/register", json={"name": "A", "email": "invalid", "password": "secret"}
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    user = await account(client)
    stored = await services[0].users.find_one({"_id": user["id"]})
    assert stored["password_hash"].startswith("$argon2")
    assert "password_hash" not in user
    assert (await client.get("/api/v1/users/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/users/me")).status_code == 401


async def test_csrf_origin_and_expired_session(client, services):
    await account(client)
    assert (
        await client.post("/api/v1/teams", json={"name": "T"}, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/teams", json={"name": "T"}, headers={"Origin": "https://evil.example"}
        )
    ).status_code == 403
    await services[0].sessions.update_many(
        {}, {"$set": {"expires_at": now() - timedelta(seconds=1)}}
    )
    assert (await client.get("/api/v1/users/me")).status_code == 401


async def test_duplicate_email_and_wrong_password(client):
    user = await account(client)
    result = await client.post(
        "/api/v1/auth/register",
        json={"name": "B", "email": user["email"].upper(), "password": "Another-safe-pass-123"},
    )
    assert result.status_code == 409
    assert (
        await client.post("/api/v1/auth/login", json={"email": user["email"], "password": "wrong"})
    ).status_code == 401


async def test_member_account_cannot_create_teams_or_change_role(client, app):
    _, team, _, base = await setup_team(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member", role="member")
        assert user["role"] == "member"
        assert (await member.get("/api/v1/auth/session")).json()["user"]["role"] == "member"
        assert (await member.post("/api/v1/teams", json={"name": "Denied"})).status_code == 403
        assert (await member.patch("/api/v1/users/me", json={"name": "Member", "role": "owner"})).status_code == 422
        assert (await client.post(base + "/members", json={"email": user["email"]})).status_code == 201
        assert (await member.get("/api/v1/teams")).json()["items"][0]["id"] == team["id"]
        assert (await member.post("/api/v1/teams", json={"name": "Still denied"})).status_code == 403


async def test_legacy_account_roles_preserve_existing_owners(client, services, app):
    owner, _, _, _ = await setup_team(client)
    await services[0].users.update_one({"_id": owner["id"]}, {"$unset": {"role": ""}})
    assert (await client.get("/api/v1/auth/session")).json()["user"]["role"] == "owner"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member", role="member")
        await services[0].users.update_one({"_id": user["id"]}, {"$unset": {"role": ""}})
        assert (await member.get("/api/v1/auth/session")).json()["user"]["role"] == "member"
        assert (await member.post("/api/v1/teams", json={"name": "Denied"})).status_code == 403


async def test_cross_tenant_and_member_permissions(client, app):
    owner, team, project, base = await setup_team(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member")
        assert (await member.get(base + "/tasks")).status_code == 404
        added = await client.post(
            base + "/members", json={"email": user["email"], "job_title": "Engineer"}
        )
        assert added.status_code == 201
        assert (await member.get(base + "/tasks")).status_code == 200
        assert (await member.post(base + "/projects", json={"name": "Denied"})).status_code == 403
        task = (
            await client.post(
                base + "/tasks",
                json={"title": "Build", "project_id": project["id"], "assignee_id": user["id"]},
            )
        ).json()
        path = base + "/tasks/" + task["id"]
        assert (
            await member.patch(path, json={"status": "completed", "version": 1})
        ).status_code == 200
        assert (
            await member.patch(path, json={"assignee_id": owner["id"], "version": 2})
        ).status_code == 403
        other = (
            await client.post(base + "/tasks", json={"title": "Other", "project_id": project["id"]})
        ).json()
        assert (
            await member.patch(
                base + "/tasks/" + other["id"], json={"status": "completed", "version": 1}
            )
        ).status_code == 403
        listing = (await member.get(base + "/members")).json()
        assert listing["total"] == 2
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as outsider:
        _, _, foreign_project, _ = await setup_team(outsider)
        result = await client.post(
            base + "/tasks", json={"title": "Cross tenant", "project_id": foreign_project["id"]}
        )
        assert result.status_code == 404
        assert (
            await outsider.get(
                base + "/analytics",
                params={"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"},
            )
        ).status_code == 404


async def test_task_version_completion_and_filters(client):
    _, _, project, base = await setup_team(client)
    task = (
        await client.post(
            base + "/tasks",
            json={"title": "Build [UI]", "project_id": project["id"], "priority": "high"},
        )
    ).json()
    path = base + "/tasks/" + task["id"]
    completed = await client.patch(path, json={"status": "completed", "version": 1})
    assert completed.status_code == 200
    stamp = completed.json()["completed_at"]
    assert stamp is not None
    assert (await client.patch(path, json={"title": "Stale", "version": 1})).status_code == 409
    repeat = await client.patch(path, json={"status": "completed", "version": 2})
    assert repeat.json()["completed_at"] == stamp
    reopened = await client.patch(path, json={"status": "in_progress", "version": 3})
    assert reopened.json()["completed_at"] is None
    listing = await client.get(
        base + "/tasks", params={"active": "true", "q": "[UI]", "priority": "high", "limit": 1}
    )
    assert listing.json()["total"] == 1
    assert (await client.get(base + "/tasks", params={"limit": 101})).status_code == 422
    assert (await client.get(base + "/tasks", params={"sort": "$where"})).status_code == 422
    assert (await client.patch(path, json={"status": None, "version": 4})).status_code == 422


async def test_events_validation_and_overlap(client):
    _, _, _, base = await setup_team(client)
    bad = {
        "title": "Meeting",
        "starts_at": "2026-01-01T12:00:00Z",
        "ends_at": "2026-01-01T11:00:00Z",
    }
    assert (await client.post(base + "/events", json=bad)).status_code == 422
    good = {**bad, "ends_at": "2026-01-01T14:00:00Z"}
    assert (await client.post(base + "/events", json=good)).status_code == 201
    data = (
        await client.get(
            base + "/events", params={"from": "2026-01-01T13:00:00Z", "to": "2026-01-01T15:00:00Z"}
        )
    ).json()
    assert data["total"] == 1
    assert (
        await client.post(base + "/events", json={**good, "starts_at": "2026-01-01T12:00:00"})
    ).status_code == 422


async def test_timer_uniqueness_and_stop(client):
    _, _, project, base = await setup_team(client)
    task = (
        await client.post(base + "/tasks", json={"title": "Build", "project_id": project["id"]})
    ).json()
    first = await client.post(base + "/time-entries/start", json={"task_id": task["id"]})
    assert first.status_code == 201
    assert (
        await client.post(base + "/time-entries/start", json={"task_id": task["id"]})
    ).status_code == 409
    stopped = await client.post(base + "/time-entries/" + first.json()["id"] + "/stop")
    assert stopped.json()["running"] is False
    assert stopped.json()["ended_at"] is not None


async def test_health_and_openapi(client):
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/teams/{team_id}/analytics" in response.json()["paths"]
