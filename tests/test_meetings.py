from datetime import timedelta

from httpx import ASGITransport, AsyncClient

from app.db import now

from .conftest import account, setup_team


async def test_meeting_team_access_notifications_and_validation(client, app):
    owner, team, _, base = await setup_team(client)
    second = (await client.post("/api/v1/teams", json={"name": "Design"})).json()
    start = now()
    body = {"title": "Planning", "url": "https://meet.google.com/abc-defg-hij",
            "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
            "team_ids": [team["id"], second["id"], team["id"]]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "MeetingMember", role="member")
        await client.post(base + "/members", json={"email": user["email"]})
        assert (await member.post("/api/v1/meetings", json=body)).status_code == 403
        created = await client.post("/api/v1/meetings", json=body)
        assert created.status_code == 201, created.text
        meeting = created.json()
        assert len(meeting["team_ids"]) == 2
        listed = (await member.get("/api/v1/meetings", params={"unread": True})).json()
        assert listed["items"][0]["url"] == body["url"]
        assert (await member.get("/api/v1/meetings", params={"team_id": second["id"]})).status_code == 404
        read = "/api/v1/meetings/" + meeting["id"] + "/read"
        assert (await member.post(read)).status_code == 204
        assert (await member.get("/api/v1/meetings", params={"unread": True})).json()["total"] == 0
        assert (await client.get("/api/v1/meetings", params={"unread": True})).json()["total"] == 1
        ranged = await member.get("/api/v1/meetings", params={"team_id": team["id"],
            "from": start.isoformat(), "to": body["ends_at"]})
        assert ranged.json()["total"] == 1
        await app.state.db.memberships.delete_one({"team_id": team["id"], "user_id": user["id"]})
        assert (await member.get("/api/v1/meetings")).json()["total"] == 0
        assert (await member.post(read)).status_code == 404
    for changes in ({"url": "javascript:alert(1)"}, {"team_ids": []},
                    {"ends_at": start.isoformat()}, {"team_ids": ["0" * 32]}):
        response = await client.post("/api/v1/meetings", json={**body, **changes})
        assert response.status_code in (403, 422), response.text
