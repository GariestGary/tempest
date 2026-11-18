"""Exercise the authenticated panel workflow with SQLite and the Agent state machine."""

import base64
import json
import os
import socket
import subprocess
from threading import Thread

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app import database, working_core
from app.core.working_core import Deployment, Draft
from app.models import Settings as DBSetting
from app.network_agent.models import AgentStatus
from app.network_agent.xray_agent import XrayJournal
from app.network_agent.service import AgentServer, PeerPolicy
from app.network_agent.xray_profile import parse_xray_request
from tests.test_xray_agent import Runtime, make_agent, plan

VLESS_ID = "569e7092-22ee-4931-aec9-2cb20ae71cfb"
PASSWORD = "working-core-test-password"
MANUAL = {"address": "192.0.2.20", "port": 8443,
          "client_id": VLESS_ID, "server_name": "egress.poc.test"}


def deployment() -> Deployment:
    base = plan()
    return Deployment(
        target_namespace_id=base.target_namespace_id,
        listen_address=base.listen_address,
        listen_port=base.listen_port,
        client_endpoint_address="2001:db8::1",
        client_endpoint_port=base.listen_port,
        tunnel_addresses=base.tunnel_addresses,
        client_pools=("10.88.0.0/24", "fd42:88::/64"),
        private_key=base.private_key,
        resolvers=base.resolvers,
        probe={
            "peer": base.peers[0],
            "rules": [*base.rules, {"destination": "198.51.100.30/32", "port": 18080, "egress": "block"}],
        },
    )


@pytest_asyncio.fixture
async def core(tmp_path, engine, monkeypatch):
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("requires private POSIX journal locking and Linux peer credentials")
    settings = working_core.settings
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "secret_key", settings.secret_key)
    monkeypatch.setattr(database, "_async_engine", engine[1])
    monkeypatch.setattr(working_core, "create_db_and_tables", lambda: None)
    for env, content in {
        "TEMPEST_CORE_DEPLOYMENT_FILE": deployment().model_dump_json(),
        "TEMPEST_CORE_BOOTSTRAP_PASSWORD_FILE": PASSWORD,
        "TEMPEST_CORE_SECRET_FILE": "test-only-secret-used-to-encrypt-egress",
    }.items():
        path = tmp_path / env
        path.write_text(content)
        monkeypatch.setenv(env, str(path))

    state = tmp_path / "journal"
    state.mkdir(mode=0o700)
    runtime = Runtime()
    path = tmp_path / "agent.sock"
    monkeypatch.setenv("TEMPEST_XRAY_AGENT_SOCKET", str(path))
    with XrayJournal(state) as journal:
        agent = make_agent(runtime, journal)
        with AgentServer(
            path,
            agent=agent,
            peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
            parse_request=parse_xray_request,
            maintain=agent.maintain,
        ) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                async with (
                    working_core.app.router.lifespan_context(working_core.app),
                    AsyncClient(transport=ASGITransport(app=working_core.app), base_url="http://test") as http,
                ):
                    yield http, runtime, journal
            finally:
                server.shutdown()
                thread.join()


async def login(http):
    assert (await http.get("/api/core")).status_code == 401
    assert (await http.post("/api/auth/login", json={"username": "admin", "password": "password"})).status_code == 401
    authenticated = await http.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert authenticated.status_code == 200
    http.headers["Authorization"] = "Bearer " + authenticated.json()["access_token"]


async def apply_draft(http):
    draft = await http.post("/api/core/preview")
    assert draft.status_code == 200
    fields = ("revision", "base_generation")
    return await http.post("/api/core/apply", json={key: draft.json()[key] for key in fields})


@pytest.mark.asyncio
async def test_panel_auth_import_provision_apply_revoke_and_recover(core, session):
    http, runtime, journal = core
    await login(http)
    uri = f"vless://{VLESS_ID}@egress.poc.test:8443?type=raw&security=tls&encryption=none"
    invalid = await http.post("/api/core/egress/import", json={"uri": uri.replace("tls", "reality")})
    assert invalid.status_code == 422 and VLESS_ID not in invalid.text
    assert (await http.post("/api/core/egress/import", json={"uri": uri})).status_code == 422
    assert (await http.post("/api/core/egress/import", json={"uri": uri, "dial_address": "192.0.2.20"})).status_code == 204

    created = await http.post("/api/core/clients", json={"name": "Телефон"})
    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    assert created.headers["Content-Disposition"].endswith('.conf"')
    assert "Address = 10.88.0.3/32, fd42:88::3/128" in created.text
    assert "DNS = 198.51.100.53, 2001:db8:2::53" in created.text
    assert "Endpoint = [2001:db8::1]:51871" in created.text
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in created.text
    private_key = next(line.split(" = ")[1] for line in created.text.splitlines() if line.startswith("PrivateKey"))
    assert len(base64.b64decode(private_key)) == 32

    current = await http.get("/api/core")
    assert all(secret not in current.text for secret in (VLESS_ID, private_key, "public_key"))
    client_id = current.json()["clients"][0]["id"]
    rule = {"destination": "198.51.100.10/32", "port": 18080, "egress": "vpn"}
    assert (await http.put("/api/core/rules", json={"revision": 1, "rules": [rule]})).status_code == 409
    assert (await http.put("/api/core/rules", json={"revision": 2, "rules": [rule]})).status_code == 204
    assert runtime.active is None
    assert (await apply_draft(http)).status_code == 200
    confirmed = journal.read().lkg
    assert confirmed and confirmed.plan.rules[0].egress == "vpn"
    assert json.loads(runtime.active)["inbounds"][0]["settings"]["peers"][0]["publicKey"] == (
        confirmed.plan.peers[0].public_key
    )
    row = session.exec(select(DBSetting).where(DBSetting.key == working_core._DRAFT_KEY)).one()
    assert VLESS_ID not in row.value and private_key not in row.value

    # Revocation uses the running policy independently of an edited draft.
    rule["egress"] = "block"
    assert (await http.put("/api/core/rules", json={"revision": 3, "rules": [rule]})).status_code == 204
    assert (await http.delete(f"/api/core/clients/{client_id}")).status_code == 200
    current = (await http.get("/api/core")).json()
    assert not current["clients"] and current["rules"][0]["egress"] == "block"
    revoked = journal.read()
    assert confirmed.plan.peers[0].public_key in revoked.revoked
    assert revoked.lkg and not revoked.lkg.plan.peers and revoked.lkg.plan.rules[0].egress == "vpn"
    make_agent(runtime, journal)
    assert runtime.active == revoked.lkg.payload()


@pytest.mark.asyncio
async def test_working_core_rejects_invalid_or_stale_changes_and_rolls_back(core):
    http, runtime, journal = core
    await login(http)
    invalid = await http.put("/api/core/egress", json={**MANUAL, "address": "not-an-ip"})
    assert invalid.status_code == 422 and VLESS_ID not in invalid.text
    assert (await http.put("/api/core/egress", json=MANUAL)).status_code == 204
    assert (await apply_draft(http)).status_code == 200
    lkg = journal.read().lkg
    assert lkg and runtime.active == lkg.payload()

    blocked = {"destination": "198.51.100.20/32", "port": 18080, "egress": "block"}
    assert (await http.put("/api/core/rules", json={"revision": 1, "rules": [blocked]})).status_code == 204
    assert (await http.post("/api/core/apply", json={"revision": 1, "base_generation": 0})).status_code == 409
    status = AgentStatus(state="idle", active_generation=1, last_known_good_generation=1)
    runtime.failure = deployment().plan(Draft(rules=[blocked]), lkg.plan.vless, status).config_bytes()
    assert (await apply_draft(http)).status_code == 409
    assert runtime.active == lkg.payload()
    assert journal.read().high_water == 2
    runtime.failure = None
    assert (await apply_draft(http)).json()["active_generation"] == 3

    runtime.stop()
    await http.get("/api/core")  # Checking invocation identity surfaces drift.
    assert (await http.get("/api/core")).json()["agent"]["active_generation"] == 0
    recovery = await http.post("/api/core/recover")
    assert recovery.status_code == 200 and recovery.json()["active_generation"] == 3
    assert runtime.active == journal.read().lkg.payload()


def test_probe_direct_is_scoped_and_wireguard_default_is_block(tmp_path):
    status = AgentStatus(state="idle", active_generation=0, last_known_good_generation=0)
    candidate = deployment().plan(Draft(), plan().vless, status)
    config = json.loads(candidate.config_bytes())
    assert config["outbounds"][0]["protocol"] == "blackhole"
    routes = config["routing"]["rules"]
    direct = [route for route in routes if route["outboundTag"] == "direct"]
    assert direct[0]["sourceIP"] == ["10.88.0.2/32", "fd42:88::2/128"]
    assert routes[-1] == {"type": "field", "inboundTag": ["wg-mvp"], "outboundTag": "block"}
    binary = os.environ.get("TEMPEST_TEST_XRAY")
    if binary:
        target = tmp_path / "candidate.json"
        target.write_bytes(candidate.config_bytes())
        assert subprocess.run([binary, "run", "-test", "-config", str(target)],
                              capture_output=True, timeout=5).returncode == 0
