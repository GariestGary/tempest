"""Working Core egress catalog tests using real SQLite and an in-process API."""

import asyncio

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import working_core
from app.core.auth import get_current_user
from app.database import get_session
from app.models import Settings
from app.network_agent.client import NetworkAgentUnavailable
from tests.test_working_core import deployment


@pytest.mark.asyncio
async def test_saved_egress_workflow_preserves_secrets_and_requires_revision(engine, session, monkeypatch):
    app = working_core.app

    async def db():
        async with AsyncSession(engine[1]) as db_session:
            yield db_session

    class UnavailableAgent:
        async def request(self, *args, **kwargs):
            raise NetworkAgentUnavailable("test")

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(app.state, "cipher", Fernet(Fernet.generate_key()), raising=False)
    monkeypatch.setattr(app.state, "deployment", deployment(), raising=False)
    monkeypatch.setattr(app.state, "change_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(app.state, "agent", UnavailableAgent(), raising=False)
    secret = "569e7092-22ee-4931-aec9-2cb20ae71cfb"
    uri = f"vless://{secret}@egress.poc.test:8443?security=tls&type=tcp"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            def body(revision, name):
                return {"revision": revision, "name": name, "uri": {"uri": uri, "dial_address": "192.0.2.20"}}

            first = await http.post("/api/core/egresses", json=body(0, "VPS NL"))
            assert first.status_code == 201 and secret not in first.text
            first_id = first.json()["id"]
            assert (await http.post("/api/core/egresses", json=body(0, "stale"))).status_code == 409
            second = await http.post("/api/core/egresses", json=body(1, "Premium PL"))
            assert second.status_code == 201
            second_id = second.json()["id"]
            before = (await http.get("/api/core")).json()
            assert before["vless"] is None and len(before["saved_egresses"]) == 2
            assert (await http.post(f"/api/core/egresses/{first_id}/select", json={"revision": 2})).status_code == 204
            current = await http.get("/api/core")
            assert current.json()["selected_egress_id"] == first_id
            assert current.json()["vless"]["address"] == "192.0.2.20"
            assert secret not in current.text
            assert (await http.request("DELETE", f"/api/core/egresses/{first_id}", json={"revision": 3})).status_code == 409
            assert (await http.request("DELETE", f"/api/core/egresses/{second_id}", json={"revision": 3})).status_code == 204
            row = session.exec(select(Settings).where(Settings.key == working_core._DRAFT_KEY)).one()
            assert secret not in row.value and "vless://" not in row.value
    finally:
        app.dependency_overrides.clear()
