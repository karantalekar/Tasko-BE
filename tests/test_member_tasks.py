from httpx import ASGITransport, AsyncClient

from .conftest import account, setup_team


async def test_member_creates_own_task_but_cannot_assign_others(client, app):
    owner, _, project, base = await setup_team(client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member", role="member")
        body = {"title": "My task", "project_id": project["id"]}
        assert (await member.post(base + "/tasks", json=body)).status_code == 404
        assert (await client.post(base + "/members", json={"email": user["email"]})).status_code == 201
        created = await member.post(base + "/tasks", json=body)
        assert created.status_code == 201
        task = created.json()
        assert task["assignee_id"] == user["id"]
        assert (await member.patch(base + "/tasks/" + task["id"], json={"version": 1, "status": "completed"})).status_code == 200
        assert (await member.post(base + "/tasks", json={**body, "assignee_id": owner["id"]})).status_code == 403
        assert (await member.post(base + "/projects", json={"name": "Denied"})).status_code == 403
        assert (await member.post("/api/v1/teams", json={"name": "Denied"})).status_code == 403
