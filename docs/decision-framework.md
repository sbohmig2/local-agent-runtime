# Decision and alignment framework

## Hard gates

Reject or redesign an option that:

- hides external processing or effective provider/model identity;
- stores or exposes credentials, prompts, tool results, or consumer data
  outside their authorized boundary;
- silently falls back between providers or models;
- lets a model authorize or directly execute unrestricted product tools;
- couples consumers through private imports, copied source, mutable branches,
  or a shared product database;
- makes a provider SDK or wire format the public runtime contract;
- conflates installed, healthy, compatible, qualified, and approved states;
- weakens cancellation, timeout, concurrency, redaction, or recovery behavior;
  or
- adds speculative marketplace, billing, tenancy, or hosted control-plane scope.

## Comparison method

For viable options compare present consumer value, reuse, security, provider
coupling, operability, testability, reversibility, distribution cost, and
migration cost. State known evidence separately from assumptions and identify
what would change the recommendation.

## Alignment status

Report on track, at risk, or off track by comparing observed behavior with the
active task and initiative. Code volume and provider count are not evidence of
usable interoperability.
