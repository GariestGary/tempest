"""Provider catalog workflow; Agent is replaced, no traffic or IPC is exercised."""
import asyncio
import base64
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app import database, working_core
from app.core.working_core import Draft
from app.models import Settings
from tests.test_working_core import PASSWORD, VLESS_ID, deployment


@pytest_asyncio.fixture
async def catalog_http(tmp_path, engine, monkeypatch):
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
            yield http


async def authenticate(http):
    response = await http.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD})
    assert response.status_code == 200
    http.headers['Authorization'] = 'Bearer ' + response.json()['access_token']


@pytest.mark.asyncio
async def test_provider_catalog_preserves_legacy_exits_and_checks_revisions(catalog_http, session):
    http = catalog_http
    assert (await http.post('/api/core/providers', json={'revision': 0, 'name': 'P'})).status_code == 401
    await authenticate(http)
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


@pytest.mark.asyncio
async def test_subscription_preview_commit_is_append_only_and_revision_bound(catalog_http, session, monkeypatch):
    http = catalog_http
    await authenticate(http)
    provider = await http.post('/api/core/providers', json={'revision': 0, 'name': 'Provider'})
    provider_id = provider.json()['id']
    uri = f'vless://{VLESS_ID}@egress.poc.test:8443?security=tls&type=tcp'
    saved = await http.post('/api/core/egresses', json={
        'revision': 1, 'name': 'Existing', 'provider_id': provider_id,
        'uri': {'uri': uri, 'dial_address': '192.0.2.20'},
    })
    exit_id = saved.json()['id']
    assert (await http.post(f'/api/core/egresses/{exit_id}/select', json={'revision': 2})).status_code == 204
    original = (await http.get('/api/core')).json()
    assert original['revision'] == 3

    new_id = '7c345c99-1d82-4c8a-a6c0-80f71ffbc5ab'
    new_uri = uri.replace(VLESS_ID, new_id)
    raw = '\n'.join([
        uri.replace('egress.poc.test', 'EGRESS.POC.TEST').replace('type=tcp', 'type=raw') + '#fragment-secret',
        new_uri + '#fragment-secret',
        new_uri,
        'ss://unsupported-secret',
        uri.replace('security=tls', 'security=reality'),
        uri.replace('egress.poc.test', 'unmapped.poc.test'),
    ])
    subscription_url = f'/api/core/providers/{provider_id}/subscription'

    async def fake_fetch(url):
        assert url == 'https://provider.example/sub?token=subscription-secret'
        return base64.b64encode(raw.encode()).decode()

    monkeypatch.setattr(working_core, 'fetch_subscription', fake_fetch)
    payload = {
        'revision': 3, 'url': 'https://provider.example/sub?token=subscription-secret',
        'dial_ips': {'egress.poc.test': '192.0.2.20'},
    }
    response = await http.post(f'{subscription_url}/preview', json=payload)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert [item['status'] for item in response.json()['results']] == [
        'duplicate', 'ready', 'duplicate', 'unsupported_protocol', 'incompatible_reality', 'incompatible_vless',
    ]
    secrets = (VLESS_ID, new_id, 'fragment-secret', 'subscription-secret', 'unsupported-secret', 'encrypted_vless')
    assert all(secret not in response.text for secret in secrets)
    assert (await http.get('/api/core')).json() == original

    stale = {'revision': 3, 'preview_id': response.json()['preview_id']}
    assert (await http.put(f'/api/core/providers/{provider_id}', json={'revision': 3, 'name': 'Renamed'})).status_code == 204
    assert (await http.post(f'{subscription_url}/commit', json=stale)).status_code == 409
    assert (await http.post(f'{subscription_url}/commit', json={**stale, 'revision': 4})).status_code == 409

    preview = await http.post(f'{subscription_url}/preview', json={
        'revision': 4, 'content': raw, 'dial_ips': payload['dial_ips'],
    })
    commit = {'revision': 4, 'preview_id': preview.json()['preview_id']}
    claims = await asyncio.gather(*(http.post(f'{subscription_url}/commit', json=commit) for _ in range(2)))
    assert sorted(response.status_code for response in claims) == [200, 409]
    after = (await http.get('/api/core')).json()
    assert after['revision'] == 5
    assert after['selected_egress_id'] == exit_id and after['vless'] == original['vless']
    assert after['saved_egresses'][0] == original['saved_egresses'][0]
    assert len(after['saved_egresses']) == 2
    assert all(item['provider_id'] == provider_id for item in after['saved_egresses'])

    repeat = await http.post(f'{subscription_url}/preview', json={**payload, 'revision': 5})
    assert repeat.json()['preview_id'] is None
    assert (await http.get('/api/core')).json() == after
    row = session.exec(select(Settings).where(Settings.key == working_core._DRAFT_KEY)).one()
    draft = Draft.model_validate_json(row.value)
    assert len(draft.saved_egresses) == 2
    assert working_core._decrypt_vless(draft.saved_egresses[1].encrypted_vless).client_id.hex == new_id.replace('-', '')
    assert all(secret not in row.value for secret in secrets[:-1])
