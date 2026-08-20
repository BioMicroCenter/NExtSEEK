"""Hermetic fake provider transport for V4-8 (no network)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["FakeProviderTransport", "ProviderCallResult"]


@dataclass
class ProviderCallResult:
    payload: str
    actual_cost_usd: Decimal


@dataclass
class FakeProviderTransport:
    unit_cost_usd: Decimal = Decimal("0.05")
    call_count: int = 0
    calls: list[str] = field(default_factory=list)

    def invoke(self, *, attempt_id: str, input_fingerprint: str) -> ProviderCallResult:
        self.call_count += 1
        self.calls.append(f"{attempt_id}:{input_fingerprint}")
        return ProviderCallResult(
            payload=f"fake:{attempt_id}",
            actual_cost_usd=self.unit_cost_usd,
        )
