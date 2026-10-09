from httpx import ASGITransport, AsyncClient

from .conftest import account, setup_team


async def test_organization_domain_validation_and_owner_permissions(client, app):
    _, _, _, base = await setup_team(client)
    response = await client.put(base + "/organization", json={
        "organization": "Whitecode", "email_domain": " WhiteCode.COM ",
    })
    assert response.status_code == 200, response.text
    assert response.json()["organization"] == "Whitecode"
    assert response.json()["email_domain"] == "whitecode.com"
    for domain in ("whitecode", "@whitecode.com", "https://whitecode.com", "white code.com"):
        assert (await client.put(base + "/organization", json={
            "organization": "Whitecode", "email_domain": domain,
        })).status_code == 422
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        user = await account(member, "Karan", role="member", email_domain="whitecode.com")
        added = await client.post(base + "/members", json={"email": user["email"].upper()})
        assert added.status_code == 201, added.text
        assert (await member.put(base + "/organization", json={
            "organization": "Changed", "email_domain": "other.com",
        })).status_code == 403
        # Domain changes cannot leave existing members outside the organisation rule.
        assert (await client.put(base + "/organization", json={
            "organization": "Other", "email_domain": "other.com",
        })).status_code == 422
    for email in (
        "member@other.com", "whitecode@other.com", "member@fakewhitecode.com",
        "member@whitecode.com.other.com", "member@sub.whitecode.com",
    ):
        response = await client.post(base + "/members", json={"email": email})
        assert response.status_code == 422, response.text
        assert "@whitecode.com" in response.text


async def test_new_team_and_legacy_team_domain_configuration(client, services):
    await account(client)
    created = await client.post("/api/v1/teams", json={
        "name": "Development", "organization": "Whitecode", "email_domain": "whitecode.com",
    })
    assert created.status_code == 201, created.text
    assert created.json()["email_domain"] == "whitecode.com"
    base = "/api/v1/teams/" + created.json()["id"]
    assert (await client.get(base)).json()["organization"] == "Whitecode"
    assert (await client.post("/api/v1/teams", json={
        "name": "Invalid", "organization": "Whitecode", "email_domain": "@whitecode.com",
    })).status_code == 422
    # Older clients still create a constrained team using the owner's email domain.
    fallback = (await client.post("/api/v1/teams", json={"name": "Legacy client"})).json()
    assert fallback["email_domain"] == "example.com"
    await services[0].teams.update_one({"_id": created.json()["id"]}, {
        "$unset": {"organization": "", "email_domain": ""},
    })
    assert (await client.get(base)).status_code == 200
    response = await client.post(base + "/members", json={"email": "member@whitecode.com"})
    assert response.status_code == 422
    assert "Settings" in response.text
    assert (await client.put(base + "/organization", json={
        "organization": "Whitecode", "email_domain": "whitecode.com",
    })).status_code == 200
