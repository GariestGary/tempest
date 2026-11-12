"""Typed, non-applying NetworkPlan inspection through the local Agent."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import Node, Settings as DBSettings
from app.network_agent.client import (
    NetworkAgentRejected,
    NetworkAgentUnavailable,
    network_agent_client,
)
from app.network_agent.models import AgentStatus, NetworkPlan
from app.network_agent.plan_builder import (
    NetworkPlanBuildConflict,
    NetworkPlanBuildError,
    build_network_plan,
)

router = APIRouter(prefix="/network-plan", tags=["network-plan"])


def _raise_agent_error(exc: Exception) -> None:
    if isinstance(exc, NetworkAgentRejected):
        status = 422 if exc.code == "invalid_plan" else 409 if exc.code in {
            "conflict", "ownership_conflict", "deadline_expired",
        } else 502
    else:
        status = 503
    raise HTTPException(status_code=status, detail=str(exc)) from exc


async def _current_plan(session: AsyncSession) -> NetworkPlan:
    if settings.control_plane_mode != "agent" or settings.dev_safe_mode:
        raise HTTPException(status_code=501, detail="NetworkPlan inspection requires Agent mode")
    status_response = await network_agent_client.read_status()
    agent_status = status_response.result
    if not isinstance(agent_status, AgentStatus):
        raise HTTPException(status_code=503, detail="Agent returned an invalid status result")

    nodes = (await session.exec(select(Node))).all()
    rows = (await session.exec(select(DBSettings))).all()
    settings_map = {row.key: row.value for row in rows}
    try:
        return build_network_plan(
            nodes,
            settings_map,
            agent_status,
            settings.network_agent_namespace_id,
        )
    except NetworkPlanBuildConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NetworkPlanBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/validate")
async def validate_plan(plan: NetworkPlan):
    try:
        return await network_agent_client.validate(plan)
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)


@router.post("/diff")
async def diff_plan(plan: NetworkPlan):
    try:
        return await network_agent_client.diff(plan)
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)


@router.get("/status")
async def agent_status():
    try:
        return await network_agent_client.read_status()
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)


@router.get("/current")
async def current_plan(session: AsyncSession = Depends(get_session)):
    """Return a secret-free, DB-derived plan for review; never stage or apply."""
    try:
        return await _current_plan(session)
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)


@router.post("/current/validate")
async def validate_current_plan(session: AsyncSession = Depends(get_session)):
    try:
        plan = await _current_plan(session)
        result = await network_agent_client.validate(plan)
        return {"plan": plan, "agent_response": result}
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)


@router.post("/current/diff")
async def diff_current_plan(session: AsyncSession = Depends(get_session)):
    try:
        plan = await _current_plan(session)
        result = await network_agent_client.diff(plan)
        return {"plan": plan, "agent_response": result}
    except (NetworkAgentUnavailable, NetworkAgentRejected) as exc:
        _raise_agent_error(exc)
