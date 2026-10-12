from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResponsePlan:
    explanation: bool = False
    recommendation: bool = False
    comparison: bool = False
    scenario: bool = False
    deterministic: bool = False


def plan_response(intent) -> ResponsePlan:
    kind = getattr(intent, "question_kind", "lookup")

    return ResponsePlan(
        explanation=kind in {"explanation", "compound"},
        recommendation=kind in {"recommendation", "compound"},
        comparison=kind == "comparison",
        scenario=kind == "scenario",
        deterministic=kind in {
            "lookup",
            "ranking",
            "explanation",
            "comparison",
            "scenario",
            "compound",
        },
    )
