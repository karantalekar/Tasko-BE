from datetime import timedelta

from httpx import ASGITransport, AsyncClient

from app.db import now

from .conftest import account, setup_team


async def test_member_creates_team_event_but_cannot_access_other_teams(client, app):
    _, _, _, base = await setup_team(client)
    start = now()
    end = start + timedelta(hours=1)
    body = {"title": "Member meeting", "starts_at": start.isoformat(), "ends_at": end.isoformat()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Member", role="member")
        assert (await member.post(base + "/events", json=body)).status_code == 404
        await client.post(base + "/members", json={"email": user["email"]})
        created = await member.post(base + "/events", json=body)
        assert created.status_code == 201
        event = created.json()
        listed = await member.get(base + "/events", params={"from": start.isoformat(), "to": end.isoformat()})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == event["id"]
        assert (await member.post(base + "/events", json={**body, "ends_at": start.isoformat()})).status_code == 422
        assert (await member.put(base + "/events/" + event["id"], json=body)).status_code == 403
        assert (await client.put(base + "/events/" + event["id"], json={**body, "title": "Updated meeting"})).status_code == 200
