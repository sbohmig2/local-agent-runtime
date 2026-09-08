"""Provider-neutral reasoning-effort resolution shared by every adapter.

Two distinct sets keep the catalog truthful.  ``transport`` is what an adapter is
able to hand to its provider at all: a level outside it cannot be delivered by
any model on that route, so no configuration may introduce it.  ``verified`` is
what this repository has actually observed *one exact model* accept, and it is
keyed by model identity because a CLI accepting a level says nothing about a
particular model behind it.

An unknown model therefore inherits nothing.  A deployment that knows its model
supports a level declares it on the profile; that declaration attests model
support, it never overrides protocol impossibility.
"""

from __future__ import annotations

from collections.abc import Mapping

from local_agent_runtime.contracts import ModelProfile, ReasoningEffort
from local_agent_runtime.errors import invalid_configuration

ORDER = tuple(ReasoningEffort)


def ordered(values: tuple[ReasoningEffort, ...]) -> tuple[ReasoningEffort, ...]:
    return tuple(effort for effort in ORDER if effort in values)


def resolve_efforts(
    profile: ModelProfile,
    transport: tuple[ReasoningEffort, ...],
    verified: Mapping[str, tuple[ReasoningEffort, ...]],
) -> tuple[ReasoningEffort, ...]:
    """Return the efforts a profile may request, or an empty tuple for no control."""
    if profile.reasoning_efforts:
        unsupported = [item for item in profile.reasoning_efforts if item not in transport]
        if unsupported:
            raise invalid_configuration(
                f"Profile {profile.id} declares reasoning efforts this route cannot deliver"
            )
        efforts = ordered(tuple(profile.reasoning_efforts))
    else:
        # A provider alias such as "default" names no exact model, so nothing is known.
        efforts = ordered(verified.get(profile.model, ()))
    default = profile.default_reasoning_effort
    if default is not None and default not in efforts:
        raise invalid_configuration(
            f"Profile {profile.id} default reasoning effort is not a supported effort"
        )
    return efforts
