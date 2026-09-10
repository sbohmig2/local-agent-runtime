import type {
  AdaptersResponse,
  EmbeddingProfilesResponse,
  EmbeddingRequest,
  EmbeddingResponse,
  EventsResponse,
  HealthResponse,
  ModelOptionsResponse,
  ProfilesResponse,
  ReasoningProfile,
  SessionEvent,
  SessionRequest,
  SessionResponse,
  ToolResultsRequest
} from "../generated.js";

export const RUNTIME_PACKAGE_VERSION = "0.5.1";
export const RUNTIME_API_VERSION = "1.4.0";

/** Provider-neutral effort vocabulary. A profile publishes the subset it supports. */
export type ReasoningEffort = "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface ReasoningOptions {
  /** Empty means this profile has no reasoning control; render no effort selector. */
  efforts: readonly ReasoningEffort[];
  default: ReasoningEffort | null;
}

export interface ModelDiscovery {
  supported: boolean;
  models: readonly string[];
  detailCode: string | null;
  loadedModels?: readonly string[] | null;
}

export interface PublicAdapterOption {
  id: string;
  profileId: string;
  model: string;
  enabled: boolean;
  canEnable: boolean;
  canDisable: boolean;
  blockedReason: string | null;
}

export interface PublicAdapter {
  id: "codex_cli" | "claude_cli" | "grok_cli" | "lmstudio" | "openrouter";
  label: string;
  processing: "local" | "external";
  supported: true;
  configured: boolean;
  enabled: boolean;
  profileIds: string[];
  options: PublicAdapterOption[];
  probe: {
    state: "not_checked" | "checked" | "failed";
    installed: boolean | null;
    detailCode: string | null;
    checkedAt: string | null;
  };
  discovery?: ModelDiscovery;
}

export interface PublicAdapterCatalog {
  adapters: PublicAdapter[];
}

export interface PublicModelOption {
  id: string;
  displayName: string;
  reasoning: ReasoningOptions;
  qualifiedTasks: readonly string[];
  loaded: boolean | null;
}

export interface PublicModelOptions {
  profileId: string;
  supported: boolean;
  checkedAt: string;
  detailCode: string | null;
  options: PublicModelOption[];
}

export function toPublicModelOptions(value: ModelOptionsResponse): PublicModelOptions {
  return {
    profileId: value.profile_id,
    supported: value.supported,
    checkedAt: value.checked_at,
    detailCode: value.detail_code,
    options: value.options.map((item) => ({
      id: item.id,
      displayName: item.display_name,
      reasoning: { efforts: [...item.reasoning.efforts], default: item.reasoning.default },
      qualifiedTasks: [...item.qualified_tasks],
      loaded: item.loaded
    }))
  };
}

export function toPublicAdapterCatalog(value: AdaptersResponse): PublicAdapterCatalog {
  return { adapters: value.adapters.map((item) => ({
    id: item.id, label: item.label, processing: item.processing, supported: item.supported,
    configured: item.configured, enabled: item.enabled, profileIds: [...item.profile_ids],
    options: item.options.map((option) => ({
      id: option.id, profileId: option.profile_id, model: option.model, enabled: option.enabled,
      canEnable: option.can_enable, canDisable: option.can_disable, blockedReason: option.blocked_reason
    })),
    probe: { state: item.probe.state, installed: item.probe.installed,
      detailCode: item.probe.detail_code, checkedAt: item.probe.checked_at },
    ...(item.discovery === undefined ? {} : { discovery: {
      supported: item.discovery.supported, models: [...item.discovery.models],
      detailCode: item.discovery.detail_code,
      loadedModels: item.discovery.loaded_models === undefined || item.discovery.loaded_models === null
        ? null : [...item.discovery.loaded_models]
    } })
  })) };
}

export interface RuntimePort {
  adapters?(probe?: boolean, signal?: AbortSignal): Promise<AdaptersResponse>;
  setAdapterActivation?(
    body: { option_id: string; enabled: boolean }, signal?: AbortSignal
  ): Promise<AdaptersResponse>;
  health(signal?: AbortSignal): Promise<HealthResponse>;
  profiles(
    includeHealth?: boolean,
    signal?: AbortSignal,
    includeDiscovery?: boolean
  ): Promise<ProfilesResponse>;
  modelOptions(profileId: string, signal?: AbortSignal): Promise<ModelOptionsResponse>;
  selectProfile(body: { profile_id: string }, signal?: AbortSignal): Promise<ProfilesResponse>;
  createSession(body: SessionRequest, signal?: AbortSignal): Promise<SessionResponse>;
  session(sessionId: string, signal?: AbortSignal): Promise<SessionResponse>;
  events(sessionId: string, after?: number, signal?: AbortSignal): Promise<EventsResponse>;
  streamEvents(
    sessionId: string,
    after?: number,
    signal?: AbortSignal
  ): AsyncGenerator<SessionEvent>;
  submitToolResults(
    sessionId: string,
    body: ToolResultsRequest,
    signal?: AbortSignal
  ): Promise<SessionResponse>;
  continueSession(
    sessionId: string,
    body: { prompt: string; reasoning_effort?: ReasoningEffort },
    signal?: AbortSignal
  ): Promise<SessionResponse>;
  cancelSession(sessionId: string, signal?: AbortSignal): Promise<SessionResponse>;
  embeddingProfiles(signal?: AbortSignal): Promise<EmbeddingProfilesResponse>;
  embed(body: EmbeddingRequest, signal?: AbortSignal): Promise<EmbeddingResponse>;
}

export interface CatalogTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface ToolCallResult {
  output: unknown;
  isError: boolean;
}

export interface ToolCatalog {
  instructions(): readonly CatalogInstruction[];
  listTools(signal?: AbortSignal): Promise<readonly CatalogTool[]>;
  callTool(
    name: string,
    argumentsValue: Record<string, unknown>,
    signal?: AbortSignal
  ): Promise<ToolCallResult>;
  close(): Promise<void>;
}

export interface CatalogInstruction {
  source: string;
  text: string;
}

export interface PublicProfile {
  id: string;
  providerId: string;
  model: string;
  processing: "local" | "external";
  privateProcessingEligible: boolean;
  qualifiedTasks: readonly string[];
  qualification: "qualified" | "unqualified";
  selected: boolean;
  capabilities: Record<string, boolean>;
  /** Readiness (`health`), enumeration (`discovery`) and task fitness
   * (`qualification`) are three independent axes; none implies another. */
  reasoning: ReasoningOptions;
  discovery?: ModelDiscovery;
  health?: {
    status: "available" | "unavailable" | "inconclusive";
    authenticated: boolean | null;
    installed: boolean | null;
    compatible: boolean | null;
    detailCode: string | null;
    effectiveModel: string | null;
  };
}

export interface HostEvent {
  sequence: number;
  type:
    | "session_started"
    | "model_working"
    | "assistant_text_delta"
    | "tools_requested"
    | "tools_completed"
    | "approval_required"
    | "completed"
    | "failed"
    | "canceled";
  occurredAt: string;
  detail: Record<string, unknown>;
}

export type HostSessionStatus =
  | "running"
  | "approval_required"
  | "completed"
  | "failed"
  | "canceled";

export interface HostSession {
  id: string;
  profileId: string;
  providerId: string;
  requestedModel: string;
  modelOptionId: string | null;
  effectiveModel: string | null;
  effectiveUpstream: string | null;
  processing: "local" | "external";
  requestedReasoningEffort: ReasoningEffort | null;
  effectiveReasoningEffort: ReasoningEffort | null;
  status: HostSessionStatus;
  finalText: string | null;
  failureCode: string | null;
  eventCount: number;
}

export interface StartSessionRequest {
  prompt: string;
  profileId?: string;
  taskCode?: string;
  privateProcessing?: boolean;
  allowExternalProcessing?: boolean;
  outputSchema?: Record<string, unknown>;
  reasoningEffort?: ReasoningEffort;
  modelOptionId?: string;
}

export interface ContinueSessionOptions {
  reasoningEffort?: ReasoningEffort;
}

export type ToolDecision = "execute" | "approval";
export interface ToolAuthorizationContext {
  request: Readonly<{
    id: string;
    name: string;
    arguments: Readonly<Record<string, unknown>>;
  }>;
  session: Readonly<HostSession>;
  profile: Readonly<PublicProfile>;
}
export type ToolAuthorizer = (
  context: Readonly<ToolAuthorizationContext>
) => ToolDecision | Promise<ToolDecision>;
export type ProcessingAuthorizer = (
  request: Readonly<StartSessionRequest>,
  profile: Readonly<PublicProfile>
) => "allow" | "deny" | Promise<"allow" | "deny">;

export function toPublicProfile(profile: ReasoningProfile): PublicProfile {
  const value: PublicProfile = {
    id: profile.id,
    providerId: profile.provider_id,
    model: profile.model,
    processing: profile.processing,
    privateProcessingEligible: profile.allow_private_processing,
    qualifiedTasks: [...profile.qualified_tasks],
    qualification: profile.qualification.status,
    selected: profile.selected,
    capabilities: { ...profile.capabilities },
    reasoning: {
      efforts: [...profile.reasoning.efforts],
      default: profile.reasoning.default
    }
  };
  if (profile.discovery !== undefined) {
    value.discovery = {
      supported: profile.discovery.supported,
      models: [...profile.discovery.models],
      detailCode: profile.discovery.detail_code,
      ...(profile.discovery.loaded_models === undefined ? {} : {
        loadedModels: profile.discovery.loaded_models === null ? null : [...profile.discovery.loaded_models]
      })
    };
  }
  if (profile.health !== undefined) {
    value.health = {
      status: profile.health.status,
      authenticated: profile.health.authenticated,
      installed: profile.health.installed,
      compatible: profile.health.compatible,
      detailCode: profile.health.detail_code,
      effectiveModel: profile.health.effective_model
    };
  }
  return value;
}
