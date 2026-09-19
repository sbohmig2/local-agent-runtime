from __future__ import annotations

import json
import math

import pytest

from local_agent_runtime.context import (
    ASCII_CHARS_PER_TOKEN,
    BASIS_CALIBRATED,
    BASIS_ESTIMATE,
    CAPACITY_SOURCE_PROVIDER_LOADED,
    CAPACITY_SOURCE_UNKNOWN,
    INPUT_IRREDUCIBLE_MESSAGE,
    IRREDUCIBLE_MESSAGE,
    MESSAGE_OVERHEAD_TOKENS,
    OUTPUT_FLOOR_TOKENS,
    RESERVE_MESSAGE,
    SMALL_CONTEXT_TOKENS,
    TEMPLATE_OVERHEAD_TOKENS,
    TOOL_CALL_OVERHEAD_TOKENS,
    TOOL_OVERHEAD_TOKENS,
    ContextPlan,
    ObservedPrompt,
    checked_plan,
    estimate_message_tokens,
    estimate_prompt_chars,
    estimate_text_tokens,
    estimate_tools_tokens,
    input_budget,
    output_floor,
    plan_context,
    unplanned_context,
)
from local_agent_runtime.contracts import (
    ContextWindow,
    Limits,
    Message,
    ToolDefinition,
    ToolRequest,
)
from local_agent_runtime.errors import RuntimeFailure

LOADED = ContextWindow(2_000, CAPACITY_SOURCE_PROVIDER_LOADED)
LIMITS = Limits(max_output_tokens=100)
SYSTEM = Message("system", "s" * 30)  # 10 + 8 = 18 tokens
TOOL = ToolDefinition(
    "lookup",
    "Get a value",
    {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
)


def user(index: int) -> Message:
    return Message("user", f"{index:03d}" + "u" * 297)  # 100 + 8 = 108 tokens


def assistant(index: int) -> Message:
    return Message("assistant", f"{index:03d}" + "a" * 297)  # 108 tokens


def transcript(turns: int) -> list[Message]:
    messages = [SYSTEM]
    for index in range(turns):
        messages.extend([user(index), assistant(index)])
    return messages


def test_ascii_estimate_is_pessimistic_and_character_class_aware() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a" * 300) == 100
    assert estimate_text_tokens("a" * 301) == 101
    # Latin, Greek and Cyrillic letters count one token each, never a fraction.
    assert estimate_text_tokens("Größe") == math.ceil(3 / ASCII_CHARS_PER_TOKEN) + 2
    # CJK and emoji count three tokens per character, matching the worst
    # observed loopback tokenization instead of a prose average.
    assert estimate_text_tokens("金融データ") == 15
    assert estimate_text_tokens("🙂🚀") == 6
    assert estimate_text_tokens("ab🙂") == 1 + 3


def test_message_estimate_covers_tool_arguments_results_and_framing() -> None:
    plain = Message("user", "a" * 30)
    assert estimate_message_tokens(plain) == 10 + MESSAGE_OVERHEAD_TOKENS
    arguments = {"id": 7, "fields": ["one", "two"]}
    call = ToolRequest("call-1", "lookup", arguments)
    request = Message("assistant", "", (call,))
    expected = (
        MESSAGE_OVERHEAD_TOKENS
        + TOOL_CALL_OVERHEAD_TOKENS
        + estimate_text_tokens("call-1")
        + estimate_text_tokens("lookup")
        + estimate_text_tokens(json.dumps(arguments, separators=(",", ":")))
    )
    assert estimate_message_tokens(request) == expected
    result = Message("tool", '{"output":{"value":1},"is_error":false}', tool_request_id="call-1")
    assert estimate_message_tokens(result) == (
        MESSAGE_OVERHEAD_TOKENS
        + estimate_text_tokens(result.content)
        + estimate_text_tokens("call-1")
    )


def test_tool_and_output_schemas_are_charged_to_the_prompt() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    without_schema = estimate_tools_tokens((TOOL,), None)
    assert without_schema == (
        TOOL_OVERHEAD_TOKENS
        + estimate_text_tokens("lookup")
        + estimate_text_tokens("Get a value")
        + estimate_text_tokens(json.dumps(TOOL.input_schema, separators=(",", ":")))
    )
    assert estimate_tools_tokens((TOOL,), schema) == without_schema + (
        TOOL_OVERHEAD_TOKENS + estimate_text_tokens(json.dumps(schema, separators=(",", ":")))
    )
    assert estimate_tools_tokens((), None) == 0


def test_budget_reserves_output_and_a_margin_or_stays_unknown() -> None:
    assert input_budget(ContextWindow(), LIMITS) is None
    assert input_budget(LOADED, LIMITS) == 2_000 - 100 - 128
    assert input_budget(ContextWindow(40_000, "provider_loaded"), LIMITS) == (40_000 - 100 - 2_000)
    assert input_budget(
        ContextWindow(32_768, "provider_loaded"), Limits(max_output_tokens=16_384)
    ) == (32_768 - 16_384 - 1_639)


def test_unknown_capacity_forwards_the_whole_transcript_without_claims() -> None:
    messages = transcript(8)
    plan = plan_context(messages, (TOOL,), None, LIMITS, ContextWindow())
    assert plan.messages == tuple(messages)
    assert plan.indices == tuple(range(len(messages)))
    assert plan.reduced is False
    assert plan.public_dict() == {
        "capacity_tokens": None,
        "capacity_source": CAPACITY_SOURCE_UNKNOWN,
        "estimated_prompt_tokens": plan.estimated_prompt_tokens,
        "basis": BASIS_ESTIMATE,
        "reduced": False,
        "dropped_messages": 0,
        "configured_output_tokens": 100,
        "allocated_output_tokens": 100,
    }
    assert plan.estimated_prompt_tokens == (
        TEMPLATE_OVERHEAD_TOKENS
        + estimate_tools_tokens((TOOL,), None)
        + sum(estimate_message_tokens(message) for message in messages)
    )
    assert unplanned_context() == {
        "capacity_tokens": None,
        "capacity_source": CAPACITY_SOURCE_UNKNOWN,
        "estimated_prompt_tokens": None,
        "basis": None,
        "reduced": False,
        "dropped_messages": 0,
        "configured_output_tokens": None,
        "allocated_output_tokens": None,
    }


def test_short_transcript_is_forwarded_unchanged() -> None:
    messages = transcript(2)
    plan = plan_context(messages, (), None, LIMITS, LOADED)
    assert plan.messages == tuple(messages)
    assert plan.reduced is False
    assert plan.dropped_turns == 0
    assert plan.capacity_tokens == 2_000
    assert plan.budget_tokens == 1_772


def test_oldest_complete_turns_leave_first_and_the_newest_suffix_stays() -> None:
    messages = transcript(8)
    plan = plan_context(messages, (), None, LIMITS, LOADED)
    # 640 template + 18 system + 5 turns of 216 tokens = 1738 <= 1772; six do not fit.
    assert plan.dropped_turns == 3
    assert plan.dropped_messages == 6
    assert plan.messages[0] == SYSTEM
    assert plan.messages[1:] == tuple(messages[7:])
    assert plan.indices == (0, *range(7, 17))
    assert plan.estimated_prompt_tokens == 1_738
    assert plan.basis == BASIS_ESTIMATE
    assert plan.reduced is True
    assert messages == transcript(8), "planning never mutates the transcript"


def test_tool_groups_are_kept_or_dropped_only_as_whole_turns() -> None:
    call = ToolRequest("call-1", "lookup", {"id": 1})
    grouped_turn = [
        user(1),
        Message("assistant", "", (call,)),
        Message(
            "tool", '{"output":"' + "r" * 300 + '"}', tool_request_id="call-1", tool_name="lookup"
        ),
        assistant(1),
    ]
    messages = [SYSTEM, user(0), assistant(0), *grouped_turn, user(2), assistant(2)]
    # Budget admits the current turn and the grouped turn but not the first one.
    window = ContextWindow(640 + 18 + 216 + 500 + 100 + 128, "provider_loaded")
    plan = plan_context(messages, (), None, LIMITS, window)
    assert plan.messages == (SYSTEM, *grouped_turn, user(2), assistant(2))
    assert plan.dropped_turns == 1
    # A tighter budget drops the grouped turn entirely rather than splitting it.
    tighter = ContextWindow(640 + 18 + 216 + 200 + 100 + 128, "provider_loaded")
    plan = plan_context(messages, (), None, LIMITS, tighter)
    assert plan.messages == (SYSTEM, user(2), assistant(2))
    assert plan.dropped_turns == 2
    assert plan.dropped_messages == 6


def test_the_entire_current_turn_with_every_tool_group_is_mandatory() -> None:
    first = ToolRequest("call-1", "lookup", {"id": 1})
    second = ToolRequest("call-2", "lookup", {"id": 2})
    current = [
        user(5),
        Message("assistant", "", (first,)),
        Message(
            "tool", '{"output":"' + "r" * 600 + '"}', tool_request_id="call-1", tool_name="lookup"
        ),
        Message("assistant", "", (second,)),
        Message(
            "tool", '{"output":"' + "r" * 600 + '"}', tool_request_id="call-2", tool_name="lookup"
        ),
    ]
    messages = [*transcript(3), *current]
    mandatory = TEMPLATE_OVERHEAD_TOKENS + sum(
        estimate_message_tokens(message) for message in (SYSTEM, *current)
    )
    window = ContextWindow(mandatory + 100 + 128, "provider_loaded")
    plan = plan_context(messages, (), None, LIMITS, window)
    assert plan.messages == (SYSTEM, *current)
    assert plan.dropped_turns == 3
    assert plan.failure is None
    # One token less and the mandatory set no longer fits: explicit refusal, no partial turn.
    short = plan_context(
        messages, (), None, LIMITS, ContextWindow(mandatory + 100 + 127, "provider_loaded")
    )
    assert short.failure == IRREDUCIBLE_MESSAGE
    assert short.messages == (SYSTEM, *current)
    assert short.dropped_turns == 3
    assert short.estimated_prompt_tokens == mandatory
    with pytest.raises(RuntimeFailure) as caught:
        checked_plan(short)
    assert caught.value.code == "context_window_exceeded"
    assert str(caught.value) == IRREDUCIBLE_MESSAGE
    assert checked_plan(plan) is plan


def test_output_reserve_larger_than_capacity_is_an_explicit_failure() -> None:
    # A large context never trades output for prompt: the configured allowance
    # simply does not fit and the failure says so.
    huge = ContextWindow(200_000, "provider_loaded")
    plan = plan_context(transcript(3), (), None, Limits(max_output_tokens=300_000), huge)
    assert plan.failure == RESERVE_MESSAGE
    assert plan.budget_tokens == 200_000 - 300_000 - 10_000
    assert plan.messages == (SYSTEM, user(2), assistant(2))
    assert plan.allocated_output_tokens == 300_000
    with pytest.raises(RuntimeFailure) as caught:
        checked_plan(plan)
    assert caught.value.code == "context_window_exceeded"
    assert str(caught.value) == RESERVE_MESSAGE
    # A small context tries a smaller allocation first; here 2000-128-874 = 998
    # is below the 2048 floor, so it is irreducible rather than a reserve failure.
    plan = plan_context(transcript(3), (), None, Limits(max_output_tokens=4_096), LOADED)
    assert plan.failure == IRREDUCIBLE_MESSAGE
    assert plan.allocated_output_tokens == 4_096


def test_observed_prompt_calibrates_supersets_and_is_preferred_over_a_heuristic_subset() -> None:
    messages = transcript(2)
    observed = ObservedPrompt(frozenset({0, 1, 2}), 100)
    # Heuristic: whole transcript 640+18+432=1090, mandatory subset 640+18+216=874.
    # Calibrated whole transcript: 100 observed + 216 new = 316 fits a 500 budget.
    window = ContextWindow(500 + 100 + 128, "provider_loaded")
    plan = plan_context(messages, (), None, LIMITS, window, observed)
    assert plan.messages == tuple(messages)
    assert plan.basis == BASIS_CALIBRATED
    assert plan.estimated_prompt_tokens == 316
    assert plan.reduced is False
    # Without the observation the same window is irreducible for the heuristic.
    assert plan_context(messages, (), None, LIMITS, window).failure == IRREDUCIBLE_MESSAGE
    # An observation that is not a subset of the candidate is ignored, not scaled.
    stale = ObservedPrompt(frozenset({0, 1, 2, 3, 4, 5, 6}), 100)
    plan = plan_context(messages, (), None, LIMITS, LOADED, stale)
    assert plan.basis == BASIS_ESTIMATE
    assert plan.estimated_prompt_tokens == 1_090


def test_plan_reports_counts_only() -> None:
    plan = plan_context(transcript(8), (), None, LIMITS, LOADED)
    assert isinstance(plan, ContextPlan)
    public = json.dumps(plan.public_dict())
    assert "uuu" not in public and "aaa" not in public and "sss" not in public
    assert set(plan.public_dict()) == {
        "capacity_tokens",
        "capacity_source",
        "estimated_prompt_tokens",
        "basis",
        "reduced",
        "dropped_messages",
        "configured_output_tokens",
        "allocated_output_tokens",
    }


def sized(role: str, tokens: int, tag: str = "x") -> Message:
    """A message whose heuristic size is exactly `tokens` (content plus framing)."""

    return Message(role, tag + "a" * ((tokens - MESSAGE_OVERHEAD_TOKENS) * 3 - len(tag)))


def test_exact_small_context_case_allocates_output_instead_of_failing() -> None:
    # 32768 loaded, 8192 configured, margin 1639: budget 22937 < required 22972.
    limits = Limits(max_output_tokens=8_192)
    window = ContextWindow(32_768, "provider_loaded")
    system = sized("system", 4_000)
    current = sized("user", 22_972 - TEMPLATE_OVERHEAD_TOKENS - 4_000)
    messages = [system, current]
    plan = plan_context(messages, (), None, limits, window)
    assert plan.estimated_prompt_tokens == 22_972
    assert plan.failure is None
    assert plan.messages == tuple(messages)
    assert plan.configured_output_tokens == 8_192
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 22_972 == 8_157
    assert plan.output_reduced is True and plan.reduced is False
    assert plan.budget_tokens == 22_972
    # One more prompt token shrinks the allocation by exactly one.
    plan = plan_context([system, sized("user", 22_972 - 640 - 4_000 + 1)], (), None, limits, window)
    assert plan.allocated_output_tokens == 8_156
    # Thirty-five fewer and the configured allowance fits unchanged.
    plan = plan_context(
        [system, sized("user", 22_972 - 640 - 4_000 - 35)], (), None, limits, window
    )
    assert plan.allocated_output_tokens == 8_192 and plan.output_reduced is False


def test_original_sixteen_k_pressure_keeps_the_mandatory_turn_with_less_output() -> None:
    limits = Limits(max_output_tokens=16_384)
    window = ContextWindow(32_768, "provider_loaded")
    messages = [sized("system", 3_000), *transcript(2)[1:], sized("user", 14_000)]
    plan = plan_context(messages, (), None, limits, window)
    # Mandatory set 640 + 3000 + 14000 = 17640 > 14745 budget; allocation is the rest.
    assert plan.failure is None
    assert plan.messages == (messages[0], messages[-1])
    assert plan.dropped_turns == 2 and plan.dropped_messages == 4
    assert plan.estimated_prompt_tokens == 17_640
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 17_640 == 13_489
    assert plan.configured_output_tokens == 16_384


def test_small_but_roomy_context_is_unchanged() -> None:
    limits = Limits(max_output_tokens=8_192)
    window = ContextWindow(32_768, "provider_loaded")
    messages = transcript(4)
    plan = plan_context(messages, (), None, limits, window)
    assert plan.messages == tuple(messages)
    assert plan.allocated_output_tokens == 8_192
    assert plan.output_reduced is False and plan.reduced is False
    assert plan.budget_tokens == 32_768 - 8_192 - 1_639


# A character ceiling no token-sized transcript below reaches: these cases exercise
# the token dimension alone; the character dimension has its own tests.
ROOMY_CHARS = 10_000_000


@pytest.mark.parametrize("capacity", [127_999, 128_000, 131_072])
def test_small_context_threshold_is_exact(capacity: int) -> None:
    limits = Limits(max_output_tokens=16_384, max_input_chars=ROOMY_CHARS)
    window = ContextWindow(capacity, "provider_loaded")
    margin = math.ceil(capacity * 0.05)
    budget = capacity - 16_384 - margin
    messages = [sized("system", 2_000), sized("user", budget + 100 - 640 - 2_000)]
    plan = plan_context(messages, (), None, limits, window)
    assert plan.estimated_prompt_tokens == budget + 100
    if capacity < SMALL_CONTEXT_TOKENS:
        assert plan.failure is None
        assert plan.allocated_output_tokens == 16_384 - 100
    else:
        assert plan.failure == IRREDUCIBLE_MESSAGE
        assert plan.allocated_output_tokens == 16_384


@pytest.mark.parametrize("capacity", [128_000, 131_072, 262_144, 1_048_576])
def test_large_contexts_keep_configured_output_and_whole_turn_handling(capacity: int) -> None:
    limits = Limits(max_output_tokens=16_384, max_input_chars=ROOMY_CHARS)
    window = ContextWindow(capacity, "provider_loaded")
    roomy = transcript(20)
    plan = plan_context(roomy, (), None, limits, window)
    assert plan.messages == tuple(roomy)
    assert plan.allocated_output_tokens == 16_384 and plan.output_reduced is False
    # Pressured history prunes whole turns exactly as before and never shrinks output.
    budget = capacity - 16_384 - math.ceil(capacity * 0.05)
    turn_tokens = 10_000
    pressured = [SYSTEM]
    total_turns = budget // turn_tokens + 5
    for index in range(total_turns):
        pressured.extend(
            [
                sized("user", turn_tokens // 2, f"u{index}"),
                sized("assistant", turn_tokens // 2, f"a{index}"),
            ]
        )
    plan = plan_context(pressured, (), None, limits, window)
    fits = (budget - 640 - 18) // turn_tokens
    assert plan.dropped_turns == total_turns - fits
    assert plan.dropped_messages == 2 * (total_turns - fits)
    assert plan.messages[0] == SYSTEM and plan.messages[1:] == tuple(pressured[-2 * fits :])
    assert plan.allocated_output_tokens == 16_384 and plan.output_reduced is False
    # An irreducible turn on a large context fails without touching output.
    plan = plan_context([SYSTEM, sized("user", budget + 1 - 640 - 18)], (), None, limits, window)
    assert plan.failure == IRREDUCIBLE_MESSAGE and plan.allocated_output_tokens == 16_384


def test_output_floor_is_the_configured_allowance_when_that_is_smaller() -> None:
    assert output_floor(Limits(max_output_tokens=512)) == 512
    assert output_floor(Limits(max_output_tokens=8_192)) == OUTPUT_FLOOR_TOKENS
    window = ContextWindow(32_768, "provider_loaded")
    # Configured 512: the mandatory set leaves exactly 512 -> allocation stays 512.
    messages = [SYSTEM, sized("user", 32_768 - 1_639 - 512 - 640 - 18)]
    plan = plan_context(messages, (), None, Limits(max_output_tokens=512), window)
    assert plan.failure is None and plan.allocated_output_tokens == 512
    # One token more and even the floor cannot fit: explicit failure, no call.
    messages = [SYSTEM, sized("user", 32_768 - 1_639 - 512 - 640 - 18 + 1)]
    plan = plan_context(messages, (), None, Limits(max_output_tokens=512), window)
    assert plan.failure == IRREDUCIBLE_MESSAGE
    # Configured 8192 with room for 2047 output: below the 2048 floor, so it fails.
    messages = [SYSTEM, sized("user", 32_768 - 1_639 - 2_047 - 640 - 18)]
    plan = plan_context(messages, (), None, Limits(max_output_tokens=8_192), window)
    assert plan.failure == IRREDUCIBLE_MESSAGE
    messages = [SYSTEM, sized("user", 32_768 - 1_639 - 2_048 - 640 - 18)]
    plan = plan_context(messages, (), None, Limits(max_output_tokens=8_192), window)
    assert plan.failure is None and plan.allocated_output_tokens == 2_048


def test_fallback_prefers_a_smaller_calibrated_superset_over_the_heuristic_subset() -> None:
    limits = Limits(max_output_tokens=8_192)
    window = ContextWindow(32_768, "provider_loaded")
    messages = [sized("system", 4_000), *transcript(1)[1:], sized("user", 19_000)]
    # Heuristic mandatory set: 640 + 4000 + 19000 = 23640 > 22937 budget.
    # The provider measured the earlier prompt (system + turn 0) at 3000 tokens,
    # so the whole transcript is 3000 + 19000 = 22000 by calibration and fits
    # beside the full configured allowance; nothing is dropped or reduced.
    observed = ObservedPrompt(frozenset({0, 1, 2}), 3_000)
    plan = plan_context(messages, (), None, limits, window, observed)
    assert plan.failure is None
    assert plan.messages == tuple(messages)
    assert plan.basis == BASIS_CALIBRATED and plan.estimated_prompt_tokens == 22_000
    assert plan.allocated_output_tokens == 8_192
    # With a larger current turn the configured allowance no longer fits either
    # way; the fallback still sizes from the smaller calibrated superset.
    messages = [sized("system", 4_000), *transcript(1)[1:], sized("user", 21_000)]
    plan = plan_context(messages, (), None, limits, window, observed)
    assert plan.failure is None
    assert plan.messages == tuple(messages)
    assert plan.basis == BASIS_CALIBRATED and plan.estimated_prompt_tokens == 24_000
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 24_000 == 7_129
    # The same transcript without calibration: mandatory heuristic 25640 fits
    # only with a 5489-token allocation and the earlier turn is dropped.
    plan = plan_context(messages, (), None, limits, window)
    assert plan.messages == (messages[0], messages[-1])
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 25_640 == 5_489


def test_fallback_keeps_no_optional_history_at_the_expense_of_output() -> None:
    limits = Limits(max_output_tokens=8_192)
    window = ContextWindow(32_768, "provider_loaded")
    small_turn = [sized("user", 300, "s"), sized("assistant", 300, "t")]
    messages = [sized("system", 4_000), *small_turn, sized("user", 19_000)]
    plan = plan_context(messages, (), None, limits, window)
    # 640 + 4000 + 19000 = 23640 > 22937: the small earlier turn could have been
    # kept at the cost of 600 output tokens; output wins.
    assert plan.messages == (messages[0], messages[-1])
    assert plan.dropped_turns == 1
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 23_640 == 7_489


def test_fallback_tie_between_windows_keeps_the_larger_one() -> None:
    limits = Limits(max_output_tokens=8_192)
    window = ContextWindow(32_768, "provider_loaded")
    messages = [sized("system", 4_000), *transcript(1)[1:], sized("user", 19_000)]
    # The provider measured system + turn 0 at exactly the heuristic size of
    # the system message plus template (640 + 4000), so the calibrated whole
    # transcript and the heuristic mandatory subset tie at 23640 tokens.
    observed = ObservedPrompt(frozenset({0, 1, 2}), 4_640)
    plan = plan_context(messages, (), None, limits, window, observed)
    assert plan.failure is None
    assert plan.estimated_prompt_tokens == 23_640
    assert plan.messages == tuple(messages), "a tie keeps the larger window"
    assert plan.basis == BASIS_CALIBRATED
    assert plan.allocated_output_tokens == 32_768 - 1_639 - 23_640 == 7_489
    # One reported token more and the mandatory heuristic subset is strictly smaller.
    plan = plan_context(
        messages, (), None, limits, window, ObservedPrompt(frozenset({0, 1, 2}), 4_641)
    )
    assert plan.messages == (messages[0], messages[-1])
    assert plan.basis == BASIS_ESTIMATE and plan.estimated_prompt_tokens == 23_640
    assert plan.allocated_output_tokens == 7_489


def test_failed_plans_report_the_configured_allowance_and_send_nothing() -> None:
    window = ContextWindow(32_768, "provider_loaded")
    limits = Limits(max_output_tokens=8_192)
    plan = plan_context([SYSTEM, sized("user", 40_000)], (), None, limits, window)
    assert plan.failure == IRREDUCIBLE_MESSAGE
    assert plan.allocated_output_tokens == plan.configured_output_tokens == 8_192
    assert plan.output_reduced is False
    with pytest.raises(RuntimeFailure):
        checked_plan(plan)


def test_character_ceiling_prunes_whole_turns_when_tokens_are_roomy() -> None:
    """The profile's input ceiling bounds the window even when the model has room."""
    messages = transcript(12)
    limits = Limits(max_output_tokens=16_384, max_input_chars=2_000)
    window = ContextWindow(1_048_576, CAPACITY_SOURCE_PROVIDER_LOADED)
    plan = plan_context(messages, (), None, limits, window)
    assert plan.failure is None and plan.reduced is True
    assert plan.messages[0] == SYSTEM and plan.messages[-1] == messages[-1]
    assert plan.dropped_messages == 2 * plan.dropped_turns > 0
    assert estimate_prompt_chars(plan.messages, (), None) <= 2_000
    # One more turn would not have fit: the window is the largest suffix that does.
    wider = [SYSTEM, *messages[-2 * (12 - plan.dropped_turns + 1) :]]
    assert estimate_prompt_chars(wider, (), None) > 2_000
    # Output stays configured and the token evidence is reported as it is.
    assert plan.allocated_output_tokens == 16_384 and plan.output_reduced is False
    assert plan.capacity_tokens == 1_048_576 and plan.basis == BASIS_ESTIMATE
    # The same transcript with a roomy ceiling is not touched.
    roomy = plan_context(messages, (), None, Limits(max_output_tokens=16_384), window)
    assert roomy.reduced is False and roomy.messages == tuple(messages)


def test_unknown_capacity_prunes_only_for_the_character_ceiling() -> None:
    messages = transcript(12)
    limits = Limits(max_output_tokens=16_384, max_input_chars=2_000)
    plan = plan_context(messages, (), None, limits, ContextWindow())
    assert plan.failure is None and plan.reduced is True
    assert plan.capacity_tokens is None and plan.capacity_source == CAPACITY_SOURCE_UNKNOWN
    assert plan.budget_tokens is None and plan.basis == BASIS_ESTIMATE
    assert plan.messages[0] == SYSTEM and plan.messages[-1] == messages[-1]
    assert estimate_prompt_chars(plan.messages, (), None) <= 2_000
    assert plan.allocated_output_tokens == 16_384
    untouched = plan_context(messages, (), None, Limits(max_output_tokens=16_384), ContextWindow())
    assert untouched.reduced is False and untouched.messages == tuple(messages)


def test_an_irreducible_current_turn_fails_with_the_input_limit_code() -> None:
    """When the mandatory window itself exceeds the ceiling the plan fails explicitly,
    with the input-limit code, whether capacity is known, small or unknown."""
    messages = [SYSTEM, Message("user", "u" * 3_000)]
    limits = Limits(max_output_tokens=16_384, max_input_chars=2_000)
    for window in (
        ContextWindow(),
        ContextWindow(32_768, CAPACITY_SOURCE_PROVIDER_LOADED),
        ContextWindow(1_048_576, CAPACITY_SOURCE_PROVIDER_LOADED),
    ):
        plan = plan_context(messages, (), None, limits, window)
        assert plan.failure == INPUT_IRREDUCIBLE_MESSAGE
        assert plan.failure_code == "input_limit_exceeded"
        assert plan.messages == tuple(messages) and plan.reduced is False
        # No output reallocation is attempted for a character-bound refusal.
        assert plan.allocated_output_tokens == 16_384
        with pytest.raises(RuntimeFailure) as caught:
            checked_plan(plan)
        assert caught.value.code == "input_limit_exceeded"
    # A token-bound refusal keeps its own code.
    token_bound = plan_context(
        [SYSTEM, sized("user", 200_000)],
        (),
        None,
        Limits(max_output_tokens=16_384, max_input_chars=ROOMY_CHARS),
        ContextWindow(131_072, CAPACITY_SOURCE_PROVIDER_LOADED),
    )
    assert token_bound.failure == IRREDUCIBLE_MESSAGE
    assert token_bound.failure_code == "context_window_exceeded"
