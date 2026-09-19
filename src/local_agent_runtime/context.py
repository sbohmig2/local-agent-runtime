"""Provider-neutral bounded conversation context.

The runtime retains a session's whole transcript. Before each provider call it
selects the prompt window that fits the model's reported capacity, keeping the
instructions, the entire current user turn with every tool group since it, and
then as many complete earlier turns as fit, newest first. Capacity is provider
evidence; without it the window is the whole transcript and nothing is claimed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_agent_runtime.contracts import ContextWindow, Limits, Message, ToolDefinition
from local_agent_runtime.errors import RuntimeFailure

# Deliberately pessimistic accounting: no installed route exposes a tokenizer.
# A loopback measurement of a Mistral-family tokenizer gave about 5 chars per
# token for prose, 4 for JSON and code, 1.5 for CJK text and 0.33 for emoji.
# Over-estimating prunes history earlier; it never widens the window.
ASCII_CHARS_PER_TOKEN = 3
ALPHABETIC_TOKENS_PER_CHAR = 1  # U+0080..U+1FFF: Latin, Greek, Cyrillic and similar
OTHER_TOKENS_PER_CHAR = 3  # CJK, symbols, emoji
MESSAGE_OVERHEAD_TOKENS = 8
TOOL_OVERHEAD_TOKENS = 16
TOOL_CALL_OVERHEAD_TOKENS = 12
# Chat-template framing, including a default system prompt a template may
# inject when the request carries none.
TEMPLATE_OVERHEAD_TOKENS = 640
OUTPUT_MARGIN_RATIO = 0.05
OUTPUT_MARGIN_MIN_TOKENS = 128
# A loaded context below this decimal count is "small": when its configured
# output allowance cannot coexist with the required prompt, one call may run
# with a smaller allocation instead of failing. Provider context sizes are plain
# counts, so a 128,000-token window already counts as large and keeps its
# configured allowance.
SMALL_CONTEXT_TOKENS = 128_000
# The least useful output allocation; below it the call fails explicitly.
OUTPUT_FLOOR_TOKENS = 2_048
CAPACITY_SOURCE_PROVIDER_LOADED = "provider_loaded"
CAPACITY_SOURCE_UNKNOWN = "unknown"
BASIS_ESTIMATE = "estimate"
BASIS_CALIBRATED = "calibrated"
IRREDUCIBLE_MESSAGE = (
    "The current request and its required context exceed the model's context window"
)
RESERVE_MESSAGE = "The model's loaded context window cannot hold the configured output limit"
INPUT_IRREDUCIBLE_MESSAGE = (
    "The current request and its required context exceed the profile's input limit"
)
CONTEXT_FAILURE_CODE = "context_window_exceeded"
INPUT_FAILURE_CODE = "input_limit_exceeded"
# The profile's character ceiling applies to what an adapter serializes for the
# provider. Planning prefers the adapter's own exact measure (PromptSizingProviderPort);
# without one it uses the larger of the two request shapes the shipped adapters
# use, plus an allowance for request framing, so a window that plans as fitting
# also fits the adapter's own final check.
PROMPT_FRAMING_CHARS = 512
PromptMeasure = Callable[[Sequence[Message]], int]


def estimate_text_tokens(text: str) -> int:
    if text.isascii():
        return math.ceil(len(text) / ASCII_CHARS_PER_TOKEN)
    ascii_chars = 0
    alphabetic = 0
    other = 0
    for char in text:
        code = ord(char)
        if code < 0x80:
            ascii_chars += 1
        elif code < 0x2000:
            alphabetic += 1
        else:
            other += 1
    return (
        math.ceil(ascii_chars / ASCII_CHARS_PER_TOKEN)
        + alphabetic * ALPHABETIC_TOKENS_PER_CHAR
        + other * OTHER_TOKENS_PER_CHAR
    )


def _json_tokens(value: Any) -> int:
    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def estimate_prompt_chars(
    messages: Sequence[Message],
    tools: Sequence[ToolDefinition],
    output_schema: Mapping[str, Any] | None,
) -> int:
    """Characters a request for this window occupies, whichever adapter shape is larger.

    The CLI shape embeds the public message and tool forms compactly. The chat shape
    spends more on framing: spaced separators, function wrappers around tools, tool-call
    arguments re-encoded as JSON strings, and the response-format wrapper around an
    output schema. Neither is assumed smaller; the framing allowance covers the
    request fields no window changes.
    """

    cli_payload = {
        "messages": [message.public_dict() for message in messages],
        "tools": [tool.public_dict() for tool in tools],
        "final_content_json_schema": None if output_schema is None else dict(output_schema),
    }
    chat_messages: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_requests:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, allow_nan=False),
                    },
                }
                for call in message.tool_requests
            ]
        if message.tool_request_id is not None:
            item["tool_call_id"] = message.tool_request_id
            item["name"] = message.tool_name
        chat_messages.append(item)
    chat_payload: dict[str, Any] = {
        "messages": chat_messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
            for tool in tools
        ],
        "stream_options": {"include_usage": True},
    }
    if output_schema is not None:
        chat_payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "runtime_output",
                "strict": True,
                "schema": dict(output_schema),
            },
        }
    compact = len(json.dumps(cli_payload, separators=(",", ":"), ensure_ascii=False))
    spaced = len(json.dumps(chat_payload, ensure_ascii=False, allow_nan=False))
    return PROMPT_FRAMING_CHARS + max(compact, spaced)


def estimate_message_tokens(message: Message) -> int:
    total = MESSAGE_OVERHEAD_TOKENS + estimate_text_tokens(message.content)
    for call in message.tool_requests:
        total += (
            TOOL_CALL_OVERHEAD_TOKENS
            + estimate_text_tokens(call.id)
            + estimate_text_tokens(call.name)
            + _json_tokens(call.arguments)
        )
    if message.tool_request_id is not None:
        total += estimate_text_tokens(message.tool_request_id)
        total += estimate_text_tokens(message.tool_name or "")
    return total


def estimate_tools_tokens(
    tools: Sequence[ToolDefinition], output_schema: Mapping[str, Any] | None
) -> int:
    total = 0
    for tool in tools:
        total += (
            TOOL_OVERHEAD_TOKENS
            + estimate_text_tokens(tool.name)
            + estimate_text_tokens(tool.description)
            + _json_tokens(tool.input_schema)
        )
    if output_schema is not None:
        total += TOOL_OVERHEAD_TOKENS + _json_tokens(output_schema)
    return total


def output_margin(capacity_tokens: int) -> int:
    return max(OUTPUT_MARGIN_MIN_TOKENS, math.ceil(capacity_tokens * OUTPUT_MARGIN_RATIO))


def input_budget(
    window: ContextWindow, limits: Limits, output_tokens: int | None = None
) -> int | None:
    """Prompt tokens available once the output allowance and a margin are reserved."""

    if window.tokens is None:
        return None
    reserve = limits.max_output_tokens if output_tokens is None else output_tokens
    return window.tokens - reserve - output_margin(window.tokens)


def output_floor(limits: Limits) -> int:
    """The smallest per-call output allocation worth making for this profile."""

    return min(limits.max_output_tokens, OUTPUT_FLOOR_TOKENS)


@dataclass(frozen=True)
class ObservedPrompt:
    """The message set of the previous provider call and the prompt size it reported."""

    indices: frozenset[int]
    prompt_tokens: int


@dataclass(frozen=True)
class ContextPlan:
    messages: tuple[Message, ...]
    indices: tuple[int, ...]
    capacity_tokens: int | None
    capacity_source: str
    budget_tokens: int | None
    estimated_prompt_tokens: int
    basis: str
    dropped_messages: int
    dropped_turns: int
    #: The profile's configured allowance; never changed by planning.
    configured_output_tokens: int
    #: What this one call may generate; below the configured value only for a
    #: known small context whose required prompt would not otherwise fit.
    allocated_output_tokens: int
    #: Set when even the mandatory window cannot fit; the provider must not be called.
    failure: str | None = None
    #: The stable public code for that failure: the model's window or the profile's input limit.
    failure_code: str = CONTEXT_FAILURE_CODE

    @property
    def reduced(self) -> bool:
        return self.dropped_messages > 0

    @property
    def output_reduced(self) -> bool:
        return self.allocated_output_tokens < self.configured_output_tokens

    def public_dict(self) -> dict[str, Any]:
        return {
            "capacity_tokens": self.capacity_tokens,
            "capacity_source": self.capacity_source,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "basis": self.basis,
            "reduced": self.reduced,
            "dropped_messages": self.dropped_messages,
            "configured_output_tokens": self.configured_output_tokens,
            "allocated_output_tokens": self.allocated_output_tokens,
        }


def unplanned_context() -> dict[str, Any]:
    """Session context before any provider call has been planned."""

    return {
        "capacity_tokens": None,
        "capacity_source": CAPACITY_SOURCE_UNKNOWN,
        "estimated_prompt_tokens": None,
        "basis": None,
        "reduced": False,
        "dropped_messages": 0,
        "configured_output_tokens": None,
        "allocated_output_tokens": None,
    }


def _turns(messages: Sequence[Message]) -> tuple[list[int], list[list[int]]]:
    """Leading system indices, then turns: a user message and everything up to the next."""

    system: list[int] = []
    turns: list[list[int]] = []
    for index, message in enumerate(messages):
        if message.role == "system" and not turns:
            system.append(index)
        elif message.role == "user" or not turns:
            turns.append([index])
        else:
            turns[-1].append(index)
    return system, turns


def plan_context(
    messages: Sequence[Message],
    tools: Sequence[ToolDefinition],
    output_schema: Mapping[str, Any] | None,
    limits: Limits,
    window: ContextWindow,
    observed: ObservedPrompt | None = None,
    measure: PromptMeasure | None = None,
) -> ContextPlan:
    """Choose the prompt window for one provider call without altering the transcript.

    Tool-call groups are never split because a turn is only ever kept or dropped
    whole. Nothing is summarized or re-executed; dropped turns simply leave the
    prompt. The mandatory set is the instructions plus the entire current turn.
    The configured output allowance is used whenever the mandatory set fits
    beside it; a known small context under pressure may instead run this one
    call with a smaller output allocation. If even that cannot fit, the plan
    carries a failure and the provider is not called.

    Two limits bound the window at once: the model's reported token capacity,
    when known, and the profile's character ceiling for what the adapter may
    send, which applies whether or not capacity is known. `measure` is the
    adapter's exact size of a candidate window; without it the generic estimate
    stands in. The retained transcript is never trimmed; only this call's window is.
    """

    per_message = [estimate_message_tokens(message) for message in messages]
    fixed = TEMPLATE_OVERHEAD_TOKENS + estimate_tools_tokens(tools, output_schema)
    system, turns = _turns(messages)
    mandatory_turns = min(len(turns), 1)

    def suffix(kept_turns: int) -> list[int]:
        return [*system, *(index for turn in turns[-kept_turns:] for index in turn)]

    def fits_chars(indices: Sequence[int]) -> bool:
        chosen_messages = [messages[index] for index in indices]
        chars = (
            estimate_prompt_chars(chosen_messages, tools, output_schema)
            if measure is None
            else measure(chosen_messages)
        )
        return chars <= limits.max_input_chars

    def estimate(indices: Sequence[int]) -> tuple[int, str]:
        chosen = set(indices)
        if observed is not None and observed.indices <= chosen:
            # The provider reported the exact size of that earlier prompt; only the
            # messages added since then need the heuristic.
            extra = sum(per_message[index] for index in chosen - observed.indices)
            return observed.prompt_tokens + extra, BASIS_CALIBRATED
        return fixed + sum(per_message[index] for index in chosen), BASIS_ESTIMATE

    configured = limits.max_output_tokens
    budget = input_budget(window, limits)
    if budget is None:
        # Unknown capacity claims nothing about the model: the window is the
        # whole transcript unless the profile's character ceiling alone forces
        # whole earlier turns out, newest kept first.
        chars_chosen: tuple[list[int], int] | None = None
        for kept_turns in range(len(turns), mandatory_turns - 1, -1):
            retained = suffix(kept_turns)
            if fits_chars(retained):
                chars_chosen = (retained, kept_turns)
                break
        chars_failure: str | None = None
        if chars_chosen is None:
            chars_chosen = (suffix(mandatory_turns), mandatory_turns)
            chars_failure = INPUT_IRREDUCIBLE_MESSAGE
        retained, kept_turns = chars_chosen
        indices = tuple(sorted(retained))
        total, basis = estimate(indices)
        return ContextPlan(
            tuple(messages[index] for index in indices),
            indices,
            None,
            window.source,
            None,
            total,
            basis,
            len(messages) - len(indices),
            len(turns) - kept_turns,
            configured,
            configured,
            chars_failure,
            INPUT_FAILURE_CODE,
        )
    capacity = window.tokens
    assert capacity is not None

    # First pass, unchanged: the largest suffix of turns that fits beside the
    # full configured output allowance and inside the character ceiling. A
    # superset of the previously observed prompt is sized from the provider's
    # own count, so it can fit where a smaller heuristic-only subset would not;
    # ordering from the whole transcript downwards never refuses a window that
    # a calibrated size admits.
    chosen: tuple[list[int], int, int, str] | None = None
    if budget > 0:
        for kept_turns in range(len(turns), mandatory_turns - 1, -1):
            retained = suffix(kept_turns)
            total, basis = estimate(retained)
            if total <= budget and fits_chars(retained):
                chosen = (retained, kept_turns, total, basis)
                break
    allocated = configured
    failure: str | None = None
    failure_code = CONTEXT_FAILURE_CODE
    if chosen is None and not fits_chars(suffix(mandatory_turns)):
        # The character ceiling, not the model, is what the mandatory window
        # cannot satisfy: no output reallocation can help, so fail explicitly.
        chosen = None
        failure = INPUT_IRREDUCIBLE_MESSAGE
        failure_code = INPUT_FAILURE_CODE
    elif chosen is None and capacity < SMALL_CONTEXT_TOKENS:
        # Small context under pressure: keep the smallest-sized window that
        # still carries the mandatory set (a calibrated superset may be smaller
        # than the heuristic mandatory subset) and give the rest of the context
        # to output, never below the useful floor. Optional history is not
        # retained at the expense of output space.
        best: tuple[list[int], int, int, str] | None = None
        for kept_turns in range(len(turns), mandatory_turns - 1, -1):
            retained = suffix(kept_turns)
            if not fits_chars(retained):
                continue
            total, basis = estimate(retained)
            if best is None or total < best[2]:
                best = (retained, kept_turns, total, basis)
        assert best is not None
        available = capacity - output_margin(capacity) - best[2]
        if available >= output_floor(limits):
            chosen = best
            allocated = min(configured, available)
    if chosen is None:
        retained = suffix(mandatory_turns)
        total, basis = estimate(retained)
        chosen = (retained, mandatory_turns, total, basis)
        if failure is None:
            failure = (
                RESERVE_MESSAGE
                if budget <= 0 and capacity >= SMALL_CONTEXT_TOKENS
                else IRREDUCIBLE_MESSAGE
            )
    retained, kept_turns, total, basis = chosen
    indices = tuple(sorted(retained))
    effective_budget = input_budget(window, limits, allocated)
    return ContextPlan(
        tuple(messages[index] for index in indices),
        indices,
        capacity,
        window.source,
        effective_budget,
        total,
        basis,
        len(messages) - len(indices),
        len(turns) - kept_turns,
        configured,
        allocated,
        failure,
        failure_code,
    )


def checked_plan(plan: ContextPlan) -> ContextPlan:
    """Raise the plan's explicit failure, if any, as the stable public code."""

    if plan.failure is not None:
        raise RuntimeFailure(plan.failure_code, plan.failure)
    return plan
