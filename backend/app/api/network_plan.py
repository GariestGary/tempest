"""Typed, non-applying NetworkPlan inspection through the local Agent."""

from fastapi import APIRouter, HTTPException

from app.network_agent.client import NetworkAgentUnavailable, network_agent_client
from app.network_agent.models import NetworkPlan

router = APIRouter(prefix="/network-plan", tags=["network-plan"])


@router.post("/validate")
async def validate_plan(plan: NetworkPlan):
    try:
        return await network_agent_client.validate(plan)
    except NetworkAgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/diff")
async def diff_plan(plan: NetworkPlan):
    try:
        return await network_agent_client.diff(plan)
    except NetworkAgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def agent_status():
    try:
        return await network_agent_client.read_status()
    except NetworkAgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
