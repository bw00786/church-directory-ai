"""Aggregated component health for /health/ready and the operator status strip."""

from .registry import component_health, overall_status

__all__ = ["component_health", "overall_status"]
