# Local Agent Runtime documentation

Local Agent Runtime gives multiple applications one bounded way to configure
and invoke supported language-model providers without making one application
depend on another.

Start with the [product vision](./product/vision.md) and
[terminology](./product/terminology.md). The accepted packaging and integration
boundary is [ADR 0001](./architecture/decisions/0001-independent-runtime-package-and-gateway.md).
Consumer installation and process boundaries are described in
[Consumer integration](./integration.md).

| Area | Answers | Location |
|---|---|---|
| Product | What is this component for? | [product/](./product/) |
| Architecture | How should it behave? | [architecture/](./architecture/) |
| Components | Which permanent capability owns a concern? | [architecture/components/](./architecture/components/) |
| Roadmap | Which finite outcome are we pursuing? | [roadmap/](./roadmap/) |
| Tasks | What is ready for implementation? | [tasks/](./tasks/) |
| Providers | What does each provider route require? | [providers/](./providers/) |
| Releases | Which immutable artifacts and limits shipped? | [releases/](./releases/) |

## Current stage

The repository baseline and first implementation are complete. Deterministic
behavior and independent review have passed. LAR-002 is preparing the public,
MIT-licensed `v0.1.1` consumer artifacts; live-provider qualification remains
distinct work.
[LAR-001](./tasks/done/LAR-001-establish-provider-neutral-local-agent-runtime.md)
is the completed first task.
