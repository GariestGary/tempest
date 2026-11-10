"""Typed, privilege-free NetworkPlan contract and mock agent."""

from .mock import MockNetworkAgent
from .models import NetworkPlan

__all__ = ["MockNetworkAgent", "NetworkPlan"]
