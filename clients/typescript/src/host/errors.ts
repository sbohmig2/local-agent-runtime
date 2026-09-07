export class HostError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
    message = "The local agent request failed safely"
  ) {
    super(message);
  }
}

export function safeError(error: unknown): HostError {
  if (error instanceof HostError) return error;
  const candidate = error as { code?: unknown; status?: unknown };
  if (
    typeof candidate?.code === "string" &&
    /^[a-z][a-z0-9_]{0,63}$/.test(candidate.code) &&
    Number.isInteger(candidate.status) &&
    typeof candidate.status === "number" &&
    candidate.status >= 400 &&
    candidate.status <= 599
  ) {
    return new HostError(candidate.code, candidate.status);
  }
  return new HostError("runtime_unavailable", 503);
}
