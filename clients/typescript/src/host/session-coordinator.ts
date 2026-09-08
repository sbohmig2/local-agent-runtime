import { randomUUID } from "node:crypto";

import type { SessionEvent, SessionResponse, ToolRequest, ToolResult } from "../generated.js";

import type {
  ContinueSessionOptions,
  HostEvent,
  HostSession,
  ProcessingAuthorizer,
  PublicProfile,
  PublicAdapterCatalog,
  ReasoningEffort,
  RuntimePort,
  StartSessionRequest,
  ToolAuthorizer,
  ToolCatalog
} from "./contracts.js";
import { toPublicAdapterCatalog, toPublicProfile } from "./contracts.js";
import { HostError, safeError } from "./errors.js";

const MAX_PROMPT_CHARS = 140_000;
const MAX_TRUSTED_INSTRUCTION_CHARS = 100_000;

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function abortable<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => {
      signal.removeEventListener("abort", onAbort);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      }
    );
  });
}

function timeoutSignal(
  parent: AbortSignal,
  milliseconds: number,
  code: string
): { signal: AbortSignal; dispose: () => void } {
  const controller = new AbortController();
  const onAbort = (): void => controller.abort(parent.reason);
  if (parent.aborted) controller.abort(parent.reason);
  else parent.addEventListener("abort", onAbort, { once: true });
  const timer = setTimeout(
    () => controller.abort(new HostError(code, 504)),
    milliseconds
  );
  return {
    signal: controller.signal,
    dispose: () => {
      clearTimeout(timer);
      parent.removeEventListener("abort", onAbort);
    }
  };
}

function yieldTurn(signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const onAbort = (): void => {
      clearImmediate(handle);
      reject(signal.reason);
    };
    const handle = setImmediate(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    });
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

interface SessionRecord {
  public: HostSession;
  runtimeId: string;
  events: HostEvent[];
  controller: AbortController;
  work: Promise<void>;
  runtimeCursor: number;
  nextEventSequence: number;
  profile: PublicProfile;
  processingRequest: StartSessionRequest;
  continuing: boolean;
  toolRounds: number;
  timedOut: boolean;
  timeout?: ReturnType<typeof setTimeout>;
  expiresAt?: number;
}

export interface SessionCoordinatorOptions {
  trustedInstructions: string;
  autoExecuteTools: ReadonlySet<string>;
  authorizeProcessing: ProcessingAuthorizer;
  authorizeTool: ToolAuthorizer;
  toolTimeoutMs?: number;
  maxSessions?: number;
  maxEventsPerSession?: number;
  sessionTtlMs?: number;
  idlePollMs?: number;
  maxIdlePolls?: number;
  maxToolRounds?: number;
  maxSessionDurationMs?: number;
  nowMillis?: () => number;
}

function requestedEffort(
  effort: ReasoningEffort | undefined,
  profile: PublicProfile
): ReasoningEffort | undefined {
  if (effort === undefined) return undefined;
  // Refuse before the runtime is asked, so an unsupported effort is never
  // accepted and quietly dropped by a provider.
  if (!profile.reasoning.efforts.includes(effort)) {
    throw new HostError("reasoning_effort_unsupported", 400);
  }
  return effort;
}

function toolRequests(value: unknown): ToolRequest[] {
  if (!Array.isArray(value)) throw new HostError("invalid_runtime_response", 503);
  const result: ToolRequest[] = [];
  const ids = new Set<string>();
  for (const item of value) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new HostError("invalid_runtime_response", 503);
    }
    const request = item as Record<string, unknown>;
    if (
      typeof request.id !== "string" ||
      request.id === "" ||
      ids.has(request.id) ||
      typeof request.name !== "string" ||
      request.name === "" ||
      typeof request.arguments !== "object" ||
      request.arguments === null ||
      Array.isArray(request.arguments)
    ) {
      throw new HostError("invalid_runtime_response", 503);
    }
    ids.add(request.id);
    result.push({
      id: request.id,
      name: request.name,
      arguments: request.arguments as Record<string, unknown>
    });
  }
  return result;
}

export class SessionCoordinator {
  private readonly sessions = new Map<string, SessionRecord>();
  private readonly toolTimeoutMs: number;
  private readonly maxSessions: number;
  private readonly maxEventsPerSession: number;
  private readonly sessionTtlMs: number;
  private readonly idlePollMs: number;
  private readonly maxIdlePolls: number;
  private readonly maxToolRounds: number;
  private readonly maxSessionDurationMs: number;
  private readonly nowMillis: () => number;
  private readonly trustedInstructions: string;
  private readonly autoExecuteTools: ReadonlySet<string>;
  private readonly authorizeProcessing: ProcessingAuthorizer;
  private readonly authorizeTool: ToolAuthorizer;
  private pendingStarts = 0;

  constructor(
    private readonly runtime: RuntimePort,
    private readonly catalog: ToolCatalog,
    options: SessionCoordinatorOptions
  ) {
    this.toolTimeoutMs = options.toolTimeoutMs ?? 30_000;
    this.maxSessions = options.maxSessions ?? 100;
    this.maxEventsPerSession = options.maxEventsPerSession ?? 2_000;
    this.sessionTtlMs = options.sessionTtlMs ?? 86_400_000;
    this.idlePollMs = options.idlePollMs ?? 50;
    this.maxIdlePolls = options.maxIdlePolls ?? 1_200;
    this.maxToolRounds = options.maxToolRounds ?? 64;
    this.maxSessionDurationMs = options.maxSessionDurationMs ?? 900_000;
    this.nowMillis = options.nowMillis ?? Date.now;
    this.trustedInstructions = options.trustedInstructions;
    this.autoExecuteTools = new Set(options.autoExecuteTools);
    this.authorizeProcessing = options.authorizeProcessing;
    this.authorizeTool = options.authorizeTool;
    if (
      options.trustedInstructions.trim() === "" ||
      options.trustedInstructions.length > MAX_TRUSTED_INSTRUCTION_CHARS ||
      !Number.isSafeInteger(this.toolTimeoutMs) ||
      this.toolTimeoutMs < 1 ||
      !Number.isSafeInteger(this.maxSessions) ||
      this.maxSessions < 1 ||
      !Number.isSafeInteger(this.maxEventsPerSession) ||
      this.maxEventsPerSession < 1 ||
      !Number.isSafeInteger(this.sessionTtlMs) ||
      this.sessionTtlMs < 1 ||
      !Number.isSafeInteger(this.idlePollMs) ||
      this.idlePollMs < 1 ||
      !Number.isSafeInteger(this.maxIdlePolls) ||
      this.maxIdlePolls < 1 ||
      !Number.isSafeInteger(this.maxToolRounds) ||
      this.maxToolRounds < 1 ||
      !Number.isSafeInteger(this.maxSessionDurationMs) ||
      this.maxSessionDurationMs < 1
    ) {
      throw new HostError("invalid_configuration", 500);
    }
  }

  async health(): Promise<{ status: "available"; runtimeVersion: string; apiVersion: string }> {
    const health = await this.runtime.health();
    return {
      status: health.status,
      runtimeVersion: health.package_version,
      apiVersion: health.api_version
    };
  }

  async profiles(
    includeHealth = false,
    includeDiscovery = false
  ): Promise<{
    selectedProfile: string;
    profiles: PublicProfile[];
  }> {
    const state = await this.runtime.profiles(includeHealth, undefined, includeDiscovery);
    return {
      selectedProfile: state.selected_profile,
      profiles: state.profiles.map(toPublicProfile)
    };
  }

  async adapters(probe = false): Promise<PublicAdapterCatalog> {
    if (this.runtime.adapters === undefined) throw new HostError("catalog_unavailable", 503);
    return toPublicAdapterCatalog(await this.runtime.adapters(probe));
  }

  async setAdapterActivation(optionId: string, enabled: boolean): Promise<PublicAdapterCatalog> {
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(optionId) || typeof enabled !== "boolean") {
      throw new HostError("invalid_request", 400);
    }
    if (this.runtime.setAdapterActivation === undefined) throw new HostError("activation_unavailable", 503);
    return toPublicAdapterCatalog(await this.runtime.setAdapterActivation({ option_id: optionId, enabled }));
  }

  async selectProfile(profileId: string): Promise<{
    selectedProfile: string;
    profiles: PublicProfile[];
  }> {
    if (!/^[a-zA-Z][a-zA-Z0-9_.:-]{0,127}$/.test(profileId)) {
      throw new HostError("invalid_request", 400);
    }
    const state = await this.runtime.selectProfile({ profile_id: profileId });
    return {
      selectedProfile: state.selected_profile,
      profiles: state.profiles.map(toPublicProfile)
    };
  }

  async start(request: StartSessionRequest): Promise<HostSession> {
    if (
      typeof request.prompt !== "string" ||
      request.prompt.trim() === "" ||
      request.prompt.length > MAX_PROMPT_CHARS ||
      (request.outputSchema !== undefined &&
        (typeof request.outputSchema !== "object" ||
          request.outputSchema === null ||
          Array.isArray(request.outputSchema)))
    ) {
      throw new HostError("invalid_request", 400);
    }
    this.removeExpired();
    if (this.sessions.size + this.pendingStarts >= this.maxSessions) {
      throw new HostError("session_capacity_reached", 503);
    }
    this.pendingStarts += 1;
    try {
      const profileState = await this.runtime.profiles(false);
      const profileId = request.profileId ?? profileState.selected_profile;
      const rawProfile = profileState.profiles.find((item) => item.id === profileId);
      if (rawProfile === undefined) throw new HostError("profile_unavailable", 409);
      const profile = toPublicProfile(rawProfile);
      const effort = requestedEffort(request.reasoningEffort, profile);
      const processingRequest = { ...request, profileId };
      const processing = await this.authorizeProcessing(
        Object.freeze({ ...processingRequest }),
        Object.freeze({ ...profile })
      );
      if (processing !== "allow") throw new HostError("processing_not_allowed", 403);

      const tools = await this.catalog.listTools();
      const runtimeSession = await this.runtime.createSession({
        prompt: request.prompt,
        instructions: this.trustedInstructions,
        profile_id: profileId,
        ...(request.taskCode === undefined ? {} : { task_code: request.taskCode }),
        private_processing: request.privateProcessing ?? false,
        allow_external_processing: request.allowExternalProcessing ?? false,
        ...(request.outputSchema === undefined ? {} : { output_schema: request.outputSchema }),
        ...(effort === undefined ? {} : { reasoning_effort: effort }),
        tools: tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          input_schema: tool.inputSchema
        }))
      });
      const publicSession = this.publicSession(randomUUID(), runtimeSession, "running");
      const record: SessionRecord = {
        public: publicSession,
        runtimeId: runtimeSession.id,
        events: [],
        controller: new AbortController(),
        work: Promise.resolve(),
        runtimeCursor: 0,
        nextEventSequence: 1,
        profile,
        processingRequest,
        continuing: false,
        toolRounds: 0,
        timedOut: false
      };
      this.sessions.set(publicSession.id, record);
      this.addEvent(record, "session_started", { profileId });
      this.scheduleTimeout(record);
      record.work = this.drive(record).catch((error: unknown) =>
        this.settleDriveFailure(record, error)
      );
      return { ...publicSession };
    } finally {
      this.pendingStarts -= 1;
    }
  }

  async continue(
    sessionId: string,
    prompt: string,
    options: ContinueSessionOptions = {}
  ): Promise<HostSession> {
    const record = this.record(sessionId);
    if (
      typeof prompt !== "string" ||
      record.public.status !== "completed" ||
      record.continuing ||
      prompt.trim() === "" ||
      prompt.length > MAX_PROMPT_CHARS
    ) {
      throw new HostError("invalid_session_state", 409);
    }
    record.continuing = true;
    try {
      const effort = requestedEffort(
        options.reasoningEffort ?? record.public.requestedReasoningEffort ?? undefined,
        record.profile
      );
      const processingRequest: StartSessionRequest = {
        ...record.processingRequest,
        prompt,
        reasoningEffort: effort
      };
      const processing = await this.authorizeProcessing(
        Object.freeze({ ...processingRequest }),
        Object.freeze({ ...record.profile })
      );
      if (processing !== "allow") throw new HostError("processing_not_allowed", 403);
      const state = await this.runtime.continueSession(record.runtimeId, {
        prompt,
        ...(effort === undefined ? {} : { reasoning_effort: effort })
      });
      record.processingRequest = processingRequest;
      record.controller = new AbortController();
      record.timedOut = false;
      delete record.expiresAt;
      record.public = this.publicSession(sessionId, state, "running");
      this.addEvent(record, "session_started", { continuation: true });
      this.scheduleTimeout(record);
      record.work = this.drive(record).catch((error: unknown) =>
        this.settleDriveFailure(record, error)
      );
      return { ...record.public };
    } finally {
      record.continuing = false;
    }
  }

  session(sessionId: string): HostSession {
    return { ...this.record(sessionId).public };
  }

  events(sessionId: string, after = 0): HostEvent[] {
    const record = this.record(sessionId);
    if (!Number.isSafeInteger(after) || after < 0 || after > record.public.eventCount) {
      throw new HostError("invalid_event_cursor", 400);
    }
    const earliest = record.events[0]?.sequence ?? record.nextEventSequence;
    if (after < earliest - 1) throw new HostError("stale_event_cursor", 409);
    return record.events.filter((event) => event.sequence > after).map((event) => ({ ...event }));
  }

  async waitForSettled(sessionId: string): Promise<HostSession> {
    const record = this.record(sessionId);
    await record.work;
    return { ...record.public };
  }

  async cancel(sessionId: string): Promise<HostSession> {
    const record = this.record(sessionId);
    if (record.public.status !== "running") return { ...record.public };
    record.controller.abort();
    await this.cancelRuntimeBounded(record.runtimeId);
    await record.work;
    if (record.public.status === "running") {
      this.markTerminal(record, "canceled");
      this.addEvent(record, "canceled", {});
    }
    return { ...record.public };
  }

  async shutdown(): Promise<void> {
    await Promise.allSettled(
      [...this.sessions.values()].map(async (record) => {
        if (record.public.status === "running") await this.cancel(record.public.id);
      })
    );
    await this.catalog.close();
  }

  private async drive(record: SessionRecord): Promise<void> {
    let lastStatus: SessionResponse["status"] | undefined;
    let idlePolls = 0;
    while (!record.controller.signal.aborted) {
      const cursorBeforeStream = record.runtimeCursor;
      const iterator = this.runtime.streamEvents(
        record.runtimeId,
        record.runtimeCursor,
        record.controller.signal
      )[Symbol.asyncIterator]();
      let streamFinished = false;
      try {
        while (true) {
          const next = await abortable(iterator.next(), record.controller.signal);
          if (next.done === true) {
            streamFinished = true;
            break;
          }
          record.runtimeCursor = next.value.sequence;
          this.normalizeRuntimeEvent(record, next.value);
        }
      } finally {
        if (!streamFinished) {
          try {
            const cleanup = iterator.return?.(undefined);
            if (cleanup !== undefined) void Promise.resolve(cleanup).catch(() => undefined);
          } catch {
            // A non-conforming iterator must not replace the session failure.
          }
        }
      }
      const state = await abortable(
        this.runtime.session(record.runtimeId, record.controller.signal),
        record.controller.signal
      );
      this.syncSession(record, state);
      if (state.status === "waiting_for_tool") {
        record.toolRounds += 1;
        if (record.toolRounds > this.maxToolRounds) {
          throw new HostError("tool_round_limit_reached", 503);
        }
        const pending = toolRequests(state.pending_tools);
        const blocked: ToolRequest[] = [];
        for (const request of pending) {
          const decision = this.autoExecuteTools.has(request.name)
            ? await abortable(
                Promise.resolve(
                  this.authorizeTool(
                    Object.freeze({
                      request: Object.freeze({
                        ...request,
                        arguments: Object.freeze({ ...request.arguments })
                      }),
                      session: Object.freeze({ ...record.public }),
                      profile: Object.freeze({ ...record.profile })
                    })
                  )
                ),
                record.controller.signal
              )
            : "approval";
          if (decision !== "execute") blocked.push(request);
        }
        if (blocked.length > 0) {
          this.markTerminal(record, "approval_required");
          record.public.failureCode = "deterministic_approval_required";
          this.addEvent(record, "approval_required", {
            tools: blocked.map((request) => request.name)
          });
          await this.cancelRuntimeBounded(record.runtimeId);
          return;
        }
        idlePolls = 0;
        lastStatus = state.status;
        const timed = timeoutSignal(record.controller.signal, this.toolTimeoutMs, "tool_timeout");
        try {
          const results: ToolResult[] = [];
          for (const request of pending) {
            const result = await abortable(
              this.catalog.callTool(request.name, request.arguments, timed.signal),
              timed.signal
            );
            results.push({
              request_id: request.id,
              name: request.name,
              output: result.output,
              is_error: result.isError
            });
          }
          await abortable(
            this.runtime.submitToolResults(record.runtimeId, { results }, timed.signal),
            timed.signal
          );
        } finally {
          timed.dispose();
        }
        await yieldTurn(record.controller.signal);
        continue;
      }
      if (state.status === "completed") {
        this.markTerminal(record, "completed");
        this.addEvent(record, "completed", {
          text: state.final_text,
          effectiveModel: state.effective_model,
          effectiveUpstream: state.effective_upstream,
          usage: state.usage
        });
        return;
      }
      if (state.status === "failed") {
        this.markTerminal(record, "failed");
        record.public.failureCode = state.failure?.code ?? "runtime_failure";
        this.addEvent(record, "failed", { code: record.public.failureCode });
        return;
      }
      if (state.status === "canceled") {
        this.markTerminal(record, "canceled");
        this.addEvent(record, "canceled", {});
        return;
      }
      const madeProgress =
        record.runtimeCursor !== cursorBeforeStream || state.status !== lastStatus;
      lastStatus = state.status;
      idlePolls = madeProgress ? 0 : idlePolls + 1;
      if (idlePolls >= this.maxIdlePolls) {
        throw new HostError("runtime_stream_stalled", 503);
      }
      await delay(this.idlePollMs, record.controller.signal);
    }
  }

  private normalizeRuntimeEvent(record: SessionRecord, event: SessionEvent): void {
    if (event.type === "provider_started") {
      this.addEvent(record, "model_working", {});
    } else if (event.type === "tool_requests") {
      const requests = Array.isArray(event.payload.requests) ? event.payload.requests : [];
      this.addEvent(record, "tools_requested", {
        count: requests.length,
        tools: requests.flatMap((request) => {
          const name = (request as { name?: unknown }).name;
          return typeof name === "string" ? [name] : [];
        })
      });
    } else if (event.type === "tool_results_received") {
      this.addEvent(record, "tools_completed", {
        count: typeof event.payload.count === "number" ? event.payload.count : 0
      });
    }
  }

  private syncSession(record: SessionRecord, state: SessionResponse): void {
    record.public.effectiveModel = state.effective_model;
    record.public.effectiveUpstream = state.effective_upstream;
    record.public.effectiveReasoningEffort = state.effective_reasoning_effort;
    record.public.finalText = state.final_text;
  }

  private publicSession(
    id: string,
    state: SessionResponse,
    status: HostSession["status"]
  ): HostSession {
    return {
      id,
      profileId: state.profile_id,
      providerId: state.provider_id,
      requestedModel: state.requested_model,
      effectiveModel: state.effective_model,
      effectiveUpstream: state.effective_upstream,
      processing: state.processing,
      requestedReasoningEffort: state.requested_reasoning_effort,
      effectiveReasoningEffort: state.effective_reasoning_effort,
      status,
      finalText: state.final_text,
      failureCode: state.failure?.code ?? null,
      eventCount: 0
    };
  }

  private record(sessionId: string): SessionRecord {
    const record = this.sessions.get(sessionId);
    if (record === undefined) throw new HostError("session_not_found", 404);
    if (record.expiresAt !== undefined && record.expiresAt <= this.nowMillis()) {
      this.sessions.delete(sessionId);
      throw new HostError("session_expired", 410);
    }
    return record;
  }

  private removeExpired(): void {
    const current = this.nowMillis();
    for (const [sessionId, record] of this.sessions) {
      if (record.expiresAt !== undefined && record.expiresAt <= current) {
        this.sessions.delete(sessionId);
      }
    }
  }

  private markTerminal(record: SessionRecord, status: HostSession["status"]): void {
    if (record.timeout !== undefined) {
      clearTimeout(record.timeout);
      delete record.timeout;
    }
    record.public.status = status;
    record.expiresAt = this.nowMillis() + this.sessionTtlMs;
  }

  private scheduleTimeout(record: SessionRecord): void {
    record.timeout = setTimeout(() => {
      record.timedOut = true;
      record.controller.abort(new HostError("session_timeout", 504));
    }, this.maxSessionDurationMs);
  }

  private async settleDriveFailure(record: SessionRecord, error: unknown): Promise<void> {
    if (record.timedOut) {
      this.markTerminal(record, "failed");
      record.public.failureCode = "session_timeout";
      this.addEvent(record, "failed", { code: "session_timeout" });
      await this.cancelRuntimeBounded(record.runtimeId);
      return;
    }
    if (record.controller.signal.aborted) {
      this.markTerminal(record, "canceled");
      this.addEvent(record, "canceled", {});
      return;
    }
    const safe = safeError(error);
    this.markTerminal(record, "failed");
    record.public.failureCode = safe.code;
    this.addEvent(record, "failed", { code: safe.code });
  }

  private async cancelRuntimeBounded(runtimeId: string): Promise<void> {
    const parent = new AbortController();
    const timed = timeoutSignal(parent.signal, Math.min(this.toolTimeoutMs, 5_000), "cancel_timeout");
    try {
      await abortable(this.runtime.cancelSession(runtimeId, timed.signal), timed.signal).catch(
        () => undefined
      );
    } finally {
      timed.dispose();
    }
  }

  private addEvent(
    record: SessionRecord,
    type: HostEvent["type"],
    detail: Record<string, unknown>
  ): void {
    const sequence = record.nextEventSequence;
    record.nextEventSequence += 1;
    record.events.push({
      sequence,
      type,
      occurredAt: new Date(this.nowMillis()).toISOString(),
      detail
    });
    if (record.events.length > this.maxEventsPerSession) record.events.shift();
    record.public.eventCount = sequence;
  }
}
