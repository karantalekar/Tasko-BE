from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from .conftest import account, setup_team


async def test_member_listing_skips_missing_users_before_pagination(client, app, services):
    owner, team, project, base = await setup_team(client)
    db, _ = services
    await db.memberships.insert_one({
        "_id": "0" * 32, "team_id": team["id"], "user_id": uuid4().hex,
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member", role="member")
        assert (await client.post(base + "/members", json={"email": user["email"]})).status_code == 201
        # Legacy membership documents may also omit optional job titles.
        await db.memberships.update_one({"user_id": user["id"]}, {"$unset": {"job_title": ""}})
        await client.post(base + "/tasks", json={
            "title": "Assigned", "project_id": project["id"], "assignee_id": user["id"],
        })
        response = await client.get(base + "/members", params={"limit": 1, "offset": 0})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert response.json()["items"][0]["user_id"] == owner["id"]
        page = (await member.get(base + "/members", params={"limit": 1, "offset": 1})).json()
        assert page["total"] == 2
        assert len(page["items"]) == 1
        assert page["items"][0]["user_id"] == user["id"]
        assert page["items"][0]["job_title"] == ""
        assert page["items"][0]["active_tasks"] == 1
        assert (await client.get(base + "/members", params={"offset": 2})).json()["items"] == []
        assert await db.memberships.count_documents({"team_id": team["id"]}) == 2
        analytics = await client.get(base + "/analytics", params={
            "from": "2026-01-01T00:00:00Z", "to": "2027-01-01T00:00:00Z",
        })
        assert analytics.status_code == 200, analytics.text
        assert analytics.json()["team_members"] == response.json()["total"] == 2
