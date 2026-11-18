"""Unprivileged Working Core API. All dataplane changes go through Xray IPC."""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import auth
from app.config import settings
from app.core.auth import get_current_user, hash_password
from app.core.working_core import ClientName, Deployment, Draft, VlessImport
from app.database import create_db_and_tables, get_async_engine, get_session
from app.models import Settings as DBSetting, User
from app.network_agent.client import NetworkAgentRejected, NetworkAgentUnavailable
from app.network_agent.models import AgentStatus, ContractModel, NetworkDiff
from app.network_agent.xray_client import DEFAULT_SOCKET, XrayAgentClient
from app.network_agent.xray_profile import MAX_PEERS, MAX_RULES, DestinationRule, VlessTLS, XrayPlan

logger = logging.getLogger(__name__)
_DRAFT_KEY = "tempest_working_core_v1"
_NO_CACHE = {"Cache-Control": "no-store"}


def read_private_file(variable: str, max_length: int) -> str:
    try:
        with Path(os.environ[variable]).open("rb") as stream:
            data = stream.read(max_length + 1)
        if len(data) > max_length:
            raise ValueError
        return data.decode("utf-8").strip()
    except (KeyError, OSError, ValueError):
        raise RuntimeError(f"Working Core requires a valid {variable} file") from None


@asynccontextmanager
async def lifespan(api: FastAPI):
    if settings.control_plane_mode != "agent" or settings.dev_safe_mode:
        raise RuntimeError("Working Core requires isolated Agent mode")
    try:
        api.state.deployment = Deployment.model_validate_json(
            read_private_file("TEMPEST_CORE_DEPLOYMENT_FILE", 16384),
        )
    except ValidationError:
        raise RuntimeError("Working Core deployment profile is invalid") from None
    secret = read_private_file("TEMPEST_CORE_SECRET_FILE", 1024)
    if len(secret.encode()) < 32:
        raise RuntimeError("Working Core requires a random application secret of at least 32 bytes")
    settings.secret_key = secret
    key = hmac.digest(secret.encode(), b"TEMPEST Working Core VLESS at rest v1", hashlib.sha256)
    api.state.cipher = Fernet(base64.urlsafe_b64encode(key))
    api.state.agent = XrayAgentClient(os.getenv("TEMPEST_XRAY_AGENT_SOCKET", DEFAULT_SOCKET), timeout=65.0)
    api.state.change_lock = asyncio.Lock()

    await asyncio.to_thread(create_db_and_tables)
    async with AsyncSession(get_async_engine()) as session:
        if (await session.exec(select(User.id).limit(1))).first() is None:
            password = read_private_file("TEMPEST_CORE_BOOTSTRAP_PASSWORD_FILE", 128)
            if not 16 <= len(password.encode()) <= 72:
                raise RuntimeError("Working Core bootstrap password requires 16–72 bytes")
            session.add(User(username="admin", password_hash=await asyncio.to_thread(hash_password, password)))
            await session.commit()
    yield


app = FastAPI(title="TEMPEST Working Core", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth.router, prefix="/api")
_authenticated = [Depends(get_current_user)]


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _exc):
    return JSONResponse({"detail": "Invalid request fields"}, status_code=422, headers=_NO_CACHE)


@app.get("/health")
def health():
    return {"status": "ok"}


def _load_vless(draft: Draft) -> VlessTLS | None:
    if draft.encrypted_vless is None:
        return None
    try:
        return VlessTLS.model_validate_json(app.state.cipher.decrypt(draft.encrypted_vless.encode()))
    except (InvalidToken, ValidationError):
        raise HTTPException(503, "Stored VLESS credentials cannot be read") from None


async def _draft(session: AsyncSession) -> tuple[Draft, DBSetting | None]:
    row = (await session.exec(select(DBSetting).where(DBSetting.key == _DRAFT_KEY))).one_or_none()
    if row is None:
        return Draft(), None
    try:
        return Draft.model_validate_json(row.value), row
    except ValidationError:
        raise HTTPException(503, "Stored Working Core draft is invalid") from None


async def _save(session: AsyncSession, draft: Draft, row: DBSetting | None, **changes) -> None:
    stored = draft.model_copy(update={**changes, "revision": draft.revision + 1})
    data = stored.model_dump_json()
    row = row if row is not None else DBSetting(key=_DRAFT_KEY, value=data)
    row.value = data
    session.add(row)
    await session.commit()


async def _agent(method: str, expected: type, **args):
    try:
        client = app.state.agent
        if method == "revoke_peer":
            response = await client.revoke_peer(args["public_key"])
        else:
            response = await client.request(method, **args)
    except NetworkAgentRejected as exc:
        logger.info("Xray Agent rejected %s, category %s", method, exc.code)
        raise HTTPException(409, f"Xray Agent rejected {method}. Check the active generation and Agent status.") from None
    except NetworkAgentUnavailable:
        raise HTTPException(503, "Xray Agent unavailable. Recheck the active generation before retrying.") from None
    if not isinstance(response.result, expected):
        raise HTTPException(503, "Xray Agent returned an unexpected result")
    return response.result


def _plan(draft: Draft, vless: VlessTLS, status: AgentStatus) -> XrayPlan:
    try:
        return app.state.deployment.plan(draft, vless, status)
    except ValidationError:
        raise HTTPException(422, "The egress, peer addresses or destination rules conflict with the deployment") from None


async def _candidate(session: AsyncSession):
    draft, _ = await _draft(session)
    vless = _load_vless(draft)
    if vless is None:
        raise HTTPException(422, "Configure a VLESS egress first")
    status = await _agent("read_status", AgentStatus)
    return draft, _plan(draft, vless, status)


@app.get("/api/core", dependencies=_authenticated)
async def current(response: Response, session: AsyncSession = Depends(get_session)):
    response.headers.update(_NO_CACHE)
    draft, _ = await _draft(session)
    vless = _load_vless(draft)
    agent = None
    agent_error = None
    try:
        agent = await _agent("read_status", AgentStatus)
    except HTTPException as exc:
        agent_error = exc.detail
    return {
        "revision": draft.revision,
        "vless": vless.model_dump(exclude={"client_id", "certificate_sha256"}) if vless else None,
        "clients": [
            {"id": client.id, "name": client.name, "addresses": client.peer.addresses}
            for client in draft.clients
        ],
        "rules": draft.rules,
        "resolvers": app.state.deployment.resolvers,
        "agent": agent,
        "agent_error": agent_error,
    }


async def _save_egress(session: AsyncSession, vless: VlessTLS) -> None:
    async with app.state.change_lock:
        draft, row = await _draft(session)
        _plan(draft, vless, AgentStatus(state="idle", active_generation=0, last_known_good_generation=0))
        encrypted = app.state.cipher.encrypt(vless.model_dump_json().encode()).decode()
        await _save(session, draft, row, encrypted_vless=encrypted)


@app.put("/api/core/egress", status_code=204, dependencies=_authenticated)
async def save_egress(egress: VlessTLS, session: AsyncSession = Depends(get_session)):
    await _save_egress(session, egress)


@app.post("/api/core/egress/import", status_code=204, dependencies=_authenticated)
async def import_egress(body: VlessImport, session: AsyncSession = Depends(get_session)):
    try:
        vless = body.vless()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    await _save_egress(session, vless)


class DraftRevision(ContractModel):
    revision: int = Field(strict=True, ge=0)


class RuleEdit(DraftRevision):
    rules: tuple[DestinationRule, ...] = Field(max_length=MAX_RULES)


def _check_revision(revision: int, draft: Draft) -> None:
    if revision != draft.revision:
        raise HTTPException(409, "The draft changed. Refresh the panel and review the new draft.")


@app.put("/api/core/rules", status_code=204, dependencies=_authenticated)
async def save_rules(body: RuleEdit, session: AsyncSession = Depends(get_session)):
    async with app.state.change_lock:
        draft, row = await _draft(session)
        _check_revision(body.revision, draft)
        vless = _load_vless(draft)
        if vless is None:
            raise HTTPException(422, "Configure a VLESS egress first")
        _plan(draft.model_copy(update={"rules": body.rules}), vless, AgentStatus(
            state="idle", active_generation=0, last_known_good_generation=0,
        ))
        await _save(session, draft, row, rules=body.rules)


class ClientCreation(ContractModel):
    name: ClientName


@app.post("/api/core/clients", status_code=201, dependencies=_authenticated, response_class=Response)
async def create_client(body: ClientCreation, session: AsyncSession = Depends(get_session)):
    async with app.state.change_lock:
        draft, row = await _draft(session)
        if len(draft.clients) >= MAX_PEERS:
            raise HTTPException(409, "WireGuard client limit reached")
        try:
            client, profile = app.state.deployment.create_client(body.name, draft)
        except ValueError:
            raise HTTPException(409, "WireGuard client addresses could not be allocated") from None
        await _save(session, draft, row, clients=(*draft.clients, client))
    return Response(profile, status_code=201, media_type="application/octet-stream", headers={
        **_NO_CACHE,
        "Content-Disposition": f'attachment; filename="tempest-{client.id.hex[:6]}.conf"',
    })


@app.delete("/api/core/clients/{client_id}", dependencies=_authenticated, response_model=AgentStatus)
async def revoke_client(client_id: UUID, session: AsyncSession = Depends(get_session)):
    async with app.state.change_lock:
        draft, row = await _draft(session)
        client = next((client for client in draft.clients if client.id == client_id), None)
        if client is None:
            raise HTTPException(404, "WireGuard client not found")
        status = await _agent("revoke_peer", AgentStatus, public_key=client.peer.public_key)
        await _save(session, draft, row, clients=tuple(entry for entry in draft.clients if entry.id != client_id))
        return status


@app.post("/api/core/preview", dependencies=_authenticated)
async def preview(session: AsyncSession = Depends(get_session)):
    async with app.state.change_lock:
        draft, plan = await _candidate(session)
        diff = await _agent("diff", NetworkDiff, plan=plan)
        return {"revision": draft.revision, "base_generation": plan.base_generation,
                "sections": [*diff.added, *diff.changed, *diff.removed]}


class ApplyDraft(DraftRevision):
    base_generation: int = Field(strict=True, ge=0)


@app.post("/api/core/apply", dependencies=_authenticated, response_model=AgentStatus)
async def apply(body: ApplyDraft, session: AsyncSession = Depends(get_session)):
    async with app.state.change_lock:
        draft, plan = await _candidate(session)
        _check_revision(body.revision, draft)
        if plan.base_generation != body.base_generation:
            raise HTTPException(409, "The running configuration changed. Review the new diff.")
        generation = plan.generation_id
        await _agent("stage", NetworkDiff, plan=plan)
        try:
            await _agent("apply", AgentStatus, generation_id=generation)
            await _agent("verify", AgentStatus, generation_id=generation)
            return await _agent("confirm", AgentStatus, generation_id=generation)
        except HTTPException:
            # A lost reply is ambiguous. Only roll back our own pending generation.
            try:
                current = await _agent("read_status", AgentStatus)
                if current.pending_generation_id == generation:
                    await _agent("rollback", AgentStatus, generation_id=generation)
                elif (current.active_generation_id == generation
                      and current.last_known_good_generation == plan.generation):
                    return current
            except HTTPException:
                logger.info("Working Core apply could not reconcile generation %s", generation)
            raise


@app.post("/api/core/pending/{generation_id}/rollback", dependencies=_authenticated, response_model=AgentStatus)
async def rollback(generation_id: UUID):
    async with app.state.change_lock:
        status = await _agent("read_status", AgentStatus)
        if status.pending_generation_id != generation_id:
            raise HTTPException(409, "This generation is no longer pending")
        return await _agent("rollback", AgentStatus, generation_id=generation_id)


@app.post("/api/core/recover", dependencies=_authenticated, response_model=AgentStatus)
async def recover():
    async with app.state.change_lock:
        return await _agent("recover_lkg", AgentStatus)
