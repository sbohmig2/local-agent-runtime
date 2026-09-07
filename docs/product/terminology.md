# Product terminology

- **Provider connection:** Deployment-local configuration for one provider
  transport, command, endpoint, and secret references.
- **Model profile:** A named, bounded provider-and-model configuration with
  processing policy and resource limits.
- **Task route:** A deployment-local mapping from a stable consumer task code to
  one model profile.
- **Session:** One explicitly selected profile, context boundary, and lifecycle.
- **Local client:** A CLI executable installed on the machine. This does not
  imply local inference.
- **Local inference:** Model processing performed by an eligible loopback-only
  local runtime, initially LM Studio.
- **External processing:** Evidence or prompts may leave the machine, including
  subscription-backed CLI and OpenRouter routes.
- **Tool request:** A normalized model request for a consumer-owned capability.
  It is not authorization or execution.
- **Effective route:** The provider, adapter, and model that actually handled an
  invocation.
- **Qualification:** Task-specific evidence that an otherwise compatible model
  performs acceptably. Health is not qualification.
