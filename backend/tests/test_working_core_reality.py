import base64
import json
from urllib.parse import urlencode

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app import working_core
from app.core.working_core import Draft, VlessImport, VlessImportError
from app.models import Settings
from app.network_agent.models import IPCProtocolError
from app.network_agent.xray_profile import parse_xray_request
from tests.test_working_core import VLESS_ID
from tests.test_working_core_providers import authenticate, catalog_http  # noqa: F401 - pytest fixture
from tests.test_xray_agent import plan

PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("=")
SHORT_ID = "a1b2c3d4e5f60718"
UPSTREAM = {
    "address": "192.0.2.20", "port": 8443, "client_id": VLESS_ID, "server_name": "egress.poc.test",
}
REALITY = {
    "public_key": PUBLIC_KEY, "short_id": SHORT_ID, "fingerprint": "chrome", "flow": "xtls-rprx-vision",
}


def reality_uri(host="192.0.2.20", **changes):
    query = {**{
        "type": "tcp", "security": "reality", "sni": "egress.poc.test",
        "pbk": PUBLIC_KEY, "sid": SHORT_ID, "fp": "chrome", "flow": "xtls-rprx-vision",
    }, **changes}
    return f"vless://{VLESS_ID}@{host}:8443?{urlencode(query)}"


def test_reality_config_preserves_routing_and_existing_tls_wire_shape():
    tls = plan(vless=UPSTREAM)
    assert "reality" not in tls.model_dump(mode="json")["vless"]
    reality = plan(vless={**UPSTREAM, "reality": REALITY})
    request = {
        "protocol_version": 1, "method": "stage", "request_id": str(reality.generation_id),
        "plan": reality.model_dump(mode="json"),
    }
    assert parse_xray_request(json.dumps(request).encode()).plan == reality
    config = json.loads(reality.config_bytes())
    tls_config = json.loads(tls.config_bytes())
    assert config["routing"] == tls_config["routing"]
    assert config["outbounds"][:2] == tls_config["outbounds"][:2]
    assert config["outbounds"][0]["tag"] == config["routing"]["rules"][-1]["outboundTag"] == "block"
    vpn = config["outbounds"][2]
    assert vpn["settings"]["vnext"][0]["users"] == [{
        "id": VLESS_ID, "encryption": "none", "flow": "xtls-rprx-vision",
    }]
    assert vpn["streamSettings"] == {
        "network": "raw", "security": "reality",
        "realitySettings": {
            "fingerprint": "chrome", "publicKey": PUBLIC_KEY,
            "serverName": "egress.poc.test", "shortId": SHORT_ID,
        },
    }

    request["plan"]["vless"]["reality"]["show"] = True
    with pytest.raises(IPCProtocolError) as error:
        parse_xray_request(json.dumps(request).encode())
    assert PUBLIC_KEY not in str(error.value)


@pytest.mark.parametrize("changes", [
    {"address": "127.0.0.1"}, {"address": "192.0.2.1"}, {"address": "::ffff:192.0.2.20"},
    {"address": "2001:db8::20%lo"}, {"server_name": "192.0.2.20"}, {"certificate_sha256": "ab" * 32},
])
def test_reality_cannot_bypass_dial_address_or_tls_contract(changes):
    with pytest.raises(ValidationError):
        plan(vless={**UPSTREAM, "reality": REALITY, **changes})


@pytest.mark.parametrize("query", [
    {"type": "xhttp"}, {"flow": "xtls-rprx-vision-udp443"}, {"encryption": "unsafe"},
    {"pbk": ""}, {"pbk": "A" * 43}, {"pbk": PUBLIC_KEY + "="},
    {"sid": "a"}, {"sid": "ab" * 9}, {"sid": "abxz"},
    {"fp": "hellogolang"}, {"fp": "randomized"}, {"sni": ""},
    {"pcs": "ab" * 32}, {"spx": "/?c=4"}, {"spx": "//other.example"},
    {"pqv": "unsupported"}, {"headerType": "http"},
])
def test_unsupported_reality_uri_returns_only_safe_diagnostics(query):
    with pytest.raises(VlessImportError) as error:
        VlessImport(uri=reality_uri(**query)).vless()
    assert error.value.status == "incompatible_reality"
    assert all(secret not in str(error.value) for secret in (VLESS_ID, PUBLIC_KEY, SHORT_ID))


def test_raw_reality_with_empty_flow_and_short_id_requires_numeric_dial_ip():
    uri = reality_uri("vpn.example.test", type="raw", sid="", fp="", flow="", spx="/")
    with pytest.raises(VlessImportError):
        VlessImport(uri=uri).vless()
    egress = VlessImport(uri=uri, dial_address="2001:db8::20").vless()
    assert egress.reality.short_id == egress.reality.flow == ""
    assert egress.reality.fingerprint == "chrome"
    assert str(egress.address) == "2001:db8::20"
    assert egress.server_name == "egress.poc.test"


@pytest.mark.asyncio
async def test_reality_catalog_uses_revision_encryption_and_redacted_metadata(catalog_http, session, caplog):
    http = catalog_http
    await authenticate(http)
    response = await http.put("/api/core/egress", json=UPSTREAM)
    assert response.status_code == 204
    provider = (await http.post("/api/core/providers", json={"name": "Provider", "revision": 1})).json()["id"]
    before = (await http.get("/api/core")).json()
    source = {
        "revision": 2, "name": "Manual Reality", "provider_id": provider,
        "config": {**UPSTREAM, "reality": REALITY},
    }
    assert (await http.post("/api/core/egresses", json={**source, "revision": 1})).status_code == 409
    bad = {**source, "config": {**UPSTREAM, "reality": {**REALITY, "private_key": "unexpected-fixture"}}}
    rejected = await http.post("/api/core/egresses", json=bad)
    assert rejected.status_code == 422
    added = await http.post("/api/core/egresses", json=source)
    assert added.status_code == 201
    exit_id = added.json()["id"]
    current = (await http.get("/api/core")).json()
    assert current["vless"] == before["vless"] and current["selected_egress_id"] is None

    raw = "\n".join([
        reality_uri(sid=SHORT_ID.upper()), reality_uri(flow=""),
        f"vless://{VLESS_ID}@192.0.2.20:8443?security=tls&sni=egress.poc.test",
        reality_uri(type="ws"),
    ])
    url = f"/api/core/providers/{provider}/subscription"
    preview = await http.post(url + "/preview", json={"revision": 3, "content": raw})
    assert preview.status_code == 200
    results = preview.json()["results"]
    assert [entry["status"] for entry in results] == ["duplicate", "ready", "ready", "incompatible_reality"]
    assert [results[1]["security"], results[2]["security"]] == ["reality", "tls"]
    assert (await http.post(url + "/commit", json={
        "revision": 3, "preview_id": preview.json()["preview_id"],
    })).json() == {"added": 2}
    assert (await http.get("/api/core")).json()["vless"] == before["vless"]
    assert (await http.post(f"/api/core/egresses/{exit_id}/select", json={"revision": 4})).status_code == 204
    current = await http.get("/api/core")
    assert current.json()["vless"] == {
        "address": "192.0.2.20", "port": 8443, "server_name": "egress.poc.test", "security": "reality",
    }
    assert [item["security"] for item in current.json()["saved_egresses"]] == ["reality", "reality", "tls"]
    row = session.exec(select(Settings).where(Settings.key == working_core._DRAFT_KEY)).one()
    assert working_core._load_vless(Draft.model_validate_json(row.value)).reality.public_key == PUBLIC_KEY
    for output in (rejected.text, added.text, preview.text, current.text, row.value, caplog.text):
        assert all(secret not in output for secret in (PUBLIC_KEY, SHORT_ID, VLESS_ID, "unexpected-fixture"))
