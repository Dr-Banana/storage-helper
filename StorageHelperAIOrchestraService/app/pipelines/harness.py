"""
Harness layer for the chat pipeline.  Agent = Model + Harness.

Two control types (per Harness Engineering pattern):
  - Guides  (feedforward): deterministic routing BEFORE the LLM call
  - Sensors (feedback):    validation / anomaly detection AFTER the LLM response

Guides prevent bad calls from ever reaching the model.
Sensors catch model misbehavior and emit structured anomaly logs that
drive future guide improvements — closing the feedback loop.

Routing rules are NOT hardcoded here.  They live in tool_registry.TOOL_DECLARATIONS
under each tool's ``"routing"`` key, making them machine-readable, testable, and
maintainable in one place.  RoutingGuide interprets them generically.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Shared types ─────────────────────────────────────────────────────────────

class AnomalyKind(str):
    MIXED_RESPONSE = "mixed_response"   # LLM returned text + function_call together — auto-correct silently
    UNKNOWN_TOOL   = "unknown_tool"     # LLM called a tool not in the registry — ask user to rephrase
    MISSING_ARGS   = "missing_args"     # Required tool args absent — ask user for missing info
    EMPTY_RESPONSE = "empty_response"   # LLM returned neither text nor a tool call — ask user to clarify

# Anomaly kinds that should surface to the user as a clarifying question.
# MIXED_RESPONSE: excluded — internal LLM quirk, auto-corrected silently.
# EMPTY_RESPONSE: excluded — the chat.py plain_generate fallback already gives a
#   natural helpful response; a forced "clarifying question" is worse UX because
#   it disrupts the conversation context (e.g. asking "what do you need?" when the
#   user clearly said "帮我规划今晚晚餐" just causes extra round-trips).
CLARIFICATION_NEEDED = {AnomalyKind.UNKNOWN_TOOL, AnomalyKind.MISSING_ARGS}

# Prompt hint injected into the clarification LLM call, keyed by anomaly kind.
# Tells the LLM *why* it's asking, so it can phrase the question naturally.
CLARIFICATION_HINTS: Dict[str, str] = {
    AnomalyKind.UNKNOWN_TOOL: (
        "You could not determine what the user wants you to do. "
        "Politely ask them to rephrase or give more detail about their goal."
    ),
    AnomalyKind.MISSING_ARGS: (
        "You understood the user's intent but lacked a specific piece of information needed to act. "
        "Ask directly for that missing detail."
    ),
}


@dataclass
class Anomaly:
    kind: str
    detail: str
    raw: Optional[Dict] = None


@dataclass
class GuideResult:
    """Returned by a guide that decides to take over routing."""
    tool: str
    args: Dict[str, Any]
    reason: str


@dataclass
class SensorResult:
    """Returned by the sensor after inspecting an LLM response."""
    function_calls: List[Dict[str, Any]]
    text: str
    anomalies: List[Anomaly] = field(default_factory=list)

    @property
    def clarification_hint(self) -> Optional[str]:
        """
        Returns the hint for the first anomaly that requires user clarification,
        or None if all anomalies are auto-correctable.
        """
        for anomaly in self.anomalies:
            if anomaly.kind in CLARIFICATION_NEEDED:
                return CLARIFICATION_HINTS.get(anomaly.kind)
        return None


# ─── Guides (feedforward) ─────────────────────────────────────────────────────

class RoutingGuide:
    """
    Evaluates structural routing constraints declared in TOOL_DECLARATIONS.

    Each tool declaration may carry a ``"routing.force_when"`` list.  Each
    entry describes a state condition; if all conditions in an entry match,
    that tool is hard-routed and the LLM call is skipped entirely.

    No routing logic lives here — this class is a generic interpreter.
    To add or change a routing rule, edit the ``"routing"`` field of the
    relevant tool in tool_registry.TOOL_DECLARATIONS.
    """

    def __init__(self) -> None:
        from app.skills.tool_registry import TOOL_DECLARATIONS
        self._declarations = TOOL_DECLARATIONS

    def evaluate(
        self,
        user_input: str,
        plan_state: Dict[str, Any],
    ) -> Optional[GuideResult]:
        """
        Iterate tool declarations in order and return the first force_when match.
        Returns None if no guide rule fires (LLM decides).
        """
        for decl in self._declarations:
            routing = decl.get("routing") or {}
            for condition in routing.get("force_when", []):
                result = self._evaluate_condition(condition, user_input, plan_state, decl["name"])
                if result is not None:
                    logger.info(
                        "[HARNESS guide] Hard-routed to %s — %s",
                        result.tool, result.reason,
                    )
                    return result
        return None

    @staticmethod
    def _evaluate_condition(
        condition: Dict[str, Any],
        user_input: str,
        plan_state: Dict[str, Any],
        tool_name: str,
    ) -> Optional[GuideResult]:
        """Return GuideResult if condition matches, else None.

        Conditions are AND-evaluated:
          state_key        — plan_state[state_key] must be truthy (omit to skip check)
          input_contains_any — at least one string must appear in user_input (omit to skip)
          also_requires    — additional plan_state key/value checks (omit to skip)
        At least one of state_key / input_contains_any must be present.
        """
        # ── state_key check (optional) ────────────────────────────────────────
        state_key = condition.get("state_key")
        if state_key and not plan_state.get(state_key):
            return None

        # ── input_contains_any check (optional) ───────────────────────────────
        triggers: Optional[List[str]] = condition.get("input_contains_any")
        if triggers is not None and not any(kw in user_input for kw in triggers):
            return None

        # ── also_requires check (optional) ───────────────────────────────────
        for k, v in (condition.get("also_requires") or {}).items():
            if plan_state.get(k) != v:
                return None

        raw_args = condition.get("args") or {}
        resolved_args = {
            k: (user_input if v == "$user_input" else v)
            for k, v in raw_args.items()
        }
        return GuideResult(
            tool=tool_name,
            args=resolved_args,
            reason=condition.get("reason", f"guide rule matched for {tool_name}"),
        )


# ─── Sensors (feedback) ───────────────────────────────────────────────────────

class ResponseSensor:
    """
    Inspects the raw Gemini response dict and returns a cleaned SensorResult.

    Detects anomalies, logs them, and auto-corrects where possible so the
    caller always receives a well-formed result.  Anomaly logs are the raw
    material for writing new guide rules (closing the feedforward feedback loop).

    Known-tool metadata is derived from TOOL_DECLARATIONS — no separate list to
    keep in sync.
    """

    def __init__(self) -> None:
        from app.skills.tool_registry import TOOL_DECLARATIONS
        self._known: Dict[str, List[str]] = {
            decl["name"]: (decl.get("parameters") or {})
                          .get("required") or []
            for decl in TOOL_DECLARATIONS
        }

    def inspect(
        self,
        raw_parts: List[Dict],
        user_input: str,
        tokens: int = 0,
    ) -> SensorResult:
        function_calls: List[Dict] = []
        text_parts: List[str] = []
        anomalies: List[Anomaly] = []

        for part in raw_parts:
            if "functionCall" in part:
                function_calls.append({
                    "name": part["functionCall"].get("name", ""),
                    "args": part["functionCall"].get("args") or {},
                })
            elif "text" in part and not part.get("thought"):
                text_parts.append(part["text"])

        joined_text = "".join(text_parts)

        # ── Anomaly: mixed response ───────────────────────────────────────
        if function_calls and joined_text.strip():
            anomalies.append(Anomaly(
                kind=AnomalyKind.MIXED_RESPONSE,
                detail=(
                    f"LLM returned both text and a function_call for input: "
                    f"{user_input!r:.120}. Text discarded. "
                    "Consider adding a guide rule for this input pattern."
                ),
            ))
            joined_text = ""   # auto-correct: discard the spurious text

        # ── Anomaly: unknown tool ─────────────────────────────────────────
        for fc in function_calls:
            name = fc["name"]
            if name not in self._known:
                anomalies.append(Anomaly(
                    kind=AnomalyKind.UNKNOWN_TOOL,
                    detail=f"LLM called unknown tool '{name}' — dropping call.",
                    raw=fc,
                ))
            else:
                # ── Anomaly: missing required args ────────────────────────
                missing = [k for k in self._known[name] if k not in fc["args"]]
                if missing:
                    anomalies.append(Anomaly(
                        kind=AnomalyKind.MISSING_ARGS,
                        detail=f"Tool '{name}' missing required args: {missing}",
                        raw=fc,
                    ))

        # Drop calls to unknown tools after logging
        function_calls = [fc for fc in function_calls if fc["name"] in self._known]

        # ── Anomaly: empty response ───────────────────────────────────────
        if not function_calls and not joined_text.strip():
            anomalies.append(Anomaly(
                kind=AnomalyKind.EMPTY_RESPONSE,
                detail=f"LLM returned nothing for input: {user_input!r:.120}",
            ))

        # ── Emit anomaly log lines ────────────────────────────────────────
        for anomaly in anomalies:
            logger.warning(
                "[HARNESS sensor] anomaly=%s detail=%s",
                anomaly.kind, anomaly.detail,
            )

        return SensorResult(
            function_calls=function_calls,
            text=joined_text,
            anomalies=anomalies,
        )


# ─── Module-level singletons ──────────────────────────────────────────────────

routing_guide = RoutingGuide()
response_sensor = ResponseSensor()
