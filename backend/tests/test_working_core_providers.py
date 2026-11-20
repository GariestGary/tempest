"""Provider catalog workflow; Agent is replaced, no traffic or IPC is exercised."""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app import database, working_core
from app.core.working_core import Draft
from app.models import Settings
from tests.test_working_core import PASSWORD, VLESS_ID, deployment


@pytest.mark.asyncio
async def test_provider_catalog_preserves_legacy_exits_and_checks_revisions(tmp_path, engine, session, monkeypatch):
    monkeypatch.setattr(working_core.settings, "control_plane_mode", "agent")
    monkeypatch.setattr(working_core.settings, "dev_safe_mode", False)
    monkeypatch.setattr(working_core.settings, "secret_key", working_core.settings.secret_key)
    monkeypatch.setattr(database, "_async_engine", engine[1])
    monkeypatch.setattr(working_core, "create_db_and_tables", lambda: None)
    for key, content in {
        "TEMPEST_CORE_DEPLOYMENT_FILE": deployment().model_dump_json(),
        "TEMPEST_CORE_BOOTSTRAP_PASSWORD_FILE": PASSWORD,
        "TEMPEST_CORE_SECRET_FILE": "test-only-secret-used-to-encrypt-egress",
    }.items():
        path = tmp_path / key
        path.write_text(content)
        monkeypatch.setenv(key, str(path))

    async def unavailable(*args, **kwargs):
        raise working_core.HTTPException(503, "Agent unavailable")
    monkeypatch.setattr(working_core, "_agent", unavailable)
    async with working_core.app.router.lifespan_context(working_core.app):
        async with AsyncClient(transport=ASGITransport(app=working_core.app), base_url="http://test") as http:
            assert (await http.post('/api/core/providers', json={'revision': 0, 'name': 'P'})).status_code == 401
            token = (await http.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD})).json()['access_token']
            http.headers['Authorization'] = f'Bearer {token}'
            uri = f'vless://{VLESS_ID}@egress.poc.test:8443?security=tls&type=tcp'
            exit_response = await http.post('/api/core/egresses', json={'revision': 0, 'name': 'Old exit', 'uri': {'uri': uri, 'dial_address': '192.0.2.20'}})
            assert exit_response.status_code == 201
            exit_id = exit_response.json()['id']
            # Simulate a pre-provider persisted JSON document.
            row = session.exec(select(Settings).where(Settings.key == working_core._DRAFT_KEY)).one()
            legacy = Draft.model_validate_json(row.value).model_dump(mode='json', exclude={'providers'})
            legacy['saved_egresses'][0].pop('provider_id')
            row.value = json.dumps(legacy)
            session.add(row)
            session.commit()
            state = (await http.get('/api/core')).json()
            assert state['providers'] == [] and state['saved_egresses'][0]['provider_id'] is None
            provider = await http.post('/api/core/providers', json={'revision': 1, 'name': 'Provider'})
            assert provider.status_code == 201
            provider_id = provider.json()['id']
            assert (await http.post('/api/core/egresses', json={'revision': 2, 'name': 'Second', 'provider_id': '00000000-0000-0000-0000-000000000001', 'uri': {'uri': uri, 'dial_address': '192.0.2.20'}})).status_code == 404
            assert (await http.put(f'/api/core/egresses/{exit_id}/provider', json={'revision': 1, 'provider_id': provider_id})).status_code == 409
            assert (await http.put(f'/api/core/egresses/{exit_id}/provider', json={'revision': 2, 'provider_id': provider_id})).status_code == 204
            assert (await http.request('DELETE', f'/api/core/providers/{provider_id}', json={'revision': 3})).status_code == 409
            assert (await http.put(f'/api/core/providers/{provider_id}', json={'revision': 3, 'name': 'Renamed'})).status_code == 204
            assert (await http.post(f'/api/core/egresses/{exit_id}/select', json={'revision': 4})).status_code == 204
            response = await http.get('/api/core')
            assert response.json()['selected_egress_id'] == exit_id
            assert response.json()['providers'][0]['name'] == 'Renamed'
            assert VLESS_ID not in response.text
            session.expire_all()
            assert VLESS_ID not in session.exec(select(Settings).where(Settings.key == working_core._DRAFT_KEY)).one().value
            assert (await http.put(f'/api/core/egresses/{exit_id}/provider', json={'revision': 5, 'provider_id': None})).status_code == 204
            assert (await http.request('DELETE', f'/api/core/providers/{provider_id}', json={'revision': 6})).status_code == 204
