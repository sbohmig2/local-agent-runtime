# Architecture

Architecture documents define the reusable runtime contract, dependency
direction, provider boundaries, and accepted decisions.

- [Components](./components/README.md)
- [Decisions](./decisions/README.md)
- [Provider capability baseline](./provider-capability-baseline.md)

The runtime is a library and optional local service. Consumers own their domain
behavior and communicate through versioned public contracts.
