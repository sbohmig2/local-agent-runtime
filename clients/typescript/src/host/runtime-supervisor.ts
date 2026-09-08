import { randomBytes } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";

import { RuntimeClient } from "../generated.js";
import type {
  AdaptersResponse,
  EmbeddingProfilesResponse,
  EmbeddingRequest,
  EmbeddingResponse,
  EventsResponse,
  HealthResponse,
  ProfilesResponse,
  SessionEvent,
  SessionRequest,
  SessionResponse,
  ToolResultsRequest
} from "../generated.js";

import {
  RUNTIME_API_VERSION,
  RUNTIME_PACKAGE_VERSION,
  type RuntimePort
} from "./contracts.js";
import { HostError } from "./errors.js";

export interface RuntimeSupervisorOptions {
  mode: "spawn" | "connect";
  command?: string;
  configurationPath?: string;
  stateRoot?: string;
  baseUrl: string;
  connectToken?: string;
  excludeEnv?: readonly string[];
  startupTimeoutMs?: number;
  maxRestarts?: number;
}

type ClientFactory = (baseUrl: string, token: string) => RuntimePort;
type SpawnProcess = typeof spawn;

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function portFromUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new HostError("invalid_configuration", 500);
  }
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.pathname !== "/" ||
    url.username !== "" ||
    url.password !== "" ||
    url.search !== "" ||
    url.hash !== "" ||
    url.port === ""
  ) {
    throw new HostError("invalid_configuration", 500);
  }
  return url.port;
}

export class RuntimeSupervisor {
  private child: ChildProcess | undefined;
  private runtime: RuntimePort | undefined;
  private startPromise: Promise<void> | undefined;
  private unexpectedExit = false;
  private restarts = 0;
  private stopped = false;
  private generation = 0;
  private readonly startupTimeoutMs: number;
  private readonly maxRestarts: number;

  constructor(
    private readonly options: RuntimeSupervisorOptions,
    private readonly spawnProcess: SpawnProcess = spawn,
    private readonly clientFactory: ClientFactory = (baseUrl, token) =>
      new RuntimeClient({ baseUrl, bearerToken: token })
  ) {
    portFromUrl(options.baseUrl);
    this.startupTimeoutMs = options.startupTimeoutMs ?? 15_000;
    this.maxRestarts = options.maxRestarts ?? 1;
    if (
      !Number.isSafeInteger(this.startupTimeoutMs) ||
      this.startupTimeoutMs < 100 ||
      !Number.isSafeInteger(this.maxRestarts) ||
      this.maxRestarts < 0
    ) {
      throw new HostError("invalid_configuration", 500);
    }
  }

  async client(): Promise<RuntimePort> {
    if (this.stopped) throw new HostError("runtime_unavailable", 503);
    if (this.runtime !== undefined && !this.unexpectedExit) return this.runtime;
    await this.ensureStarted();
    if (this.runtime === undefined) throw new HostError("runtime_unavailable", 503);
    return this.runtime;
  }

  async start(): Promise<void> {
    if (this.stopped) {
      this.stopped = false;
      this.restarts = 0;
      this.unexpectedExit = false;
    }
    await this.ensureStarted();
  }

  private async ensureStarted(): Promise<void> {
    if (this.runtime !== undefined && !this.unexpectedExit) return;
    if (this.startPromise !== undefined) return this.startPromise;
    const start = this.startOnce();
    this.startPromise = start;
    try {
      await start;
    } finally {
      if (this.startPromise === start) this.startPromise = undefined;
    }
  }

  private async startOnce(): Promise<void> {
    if (this.stopped) throw new HostError("runtime_unavailable", 503);
    if (this.unexpectedExit) {
      if (this.options.mode !== "spawn" || this.restarts >= this.maxRestarts) {
        throw new HostError("runtime_unavailable", 503);
      }
      this.restarts += 1;
    }
    await this.disposeCurrentChild();
    this.unexpectedExit = false;
    let token: string;
    let spawnedGeneration: number | undefined;
    if (this.options.mode === "spawn") {
      if (
        this.options.command === undefined ||
        this.options.configurationPath === undefined ||
        this.options.stateRoot === undefined
      ) {
        throw new HostError("invalid_configuration", 500);
      }
      token = randomBytes(32).toString("base64url");
      const url = new URL(this.options.baseUrl);
      const childEnvironment = { ...process.env };
      for (const name of this.options.excludeEnv ?? []) delete childEnvironment[name];
      let child: ChildProcess;
      try {
        child = this.spawnProcess(
          this.options.command,
          [
            "serve",
            "--config",
            this.options.configurationPath,
            "--state-root",
            this.options.stateRoot,
            "--host",
            url.hostname === "[::1]" ? "::1" : url.hostname,
            "--port",
            portFromUrl(this.options.baseUrl)
          ],
          {
            env: { ...childEnvironment, LOCAL_AGENT_RUNTIME_TOKEN: token },
            stdio: "ignore",
            windowsHide: true
          }
        );
      } catch {
        throw new HostError("runtime_startup_failed", 503);
      }
      spawnedGeneration = this.generation + 1;
      this.generation = spawnedGeneration;
      this.child = child;
      const markUnexpected = (): void => {
        if (
          this.child === child &&
          this.generation === spawnedGeneration &&
          !this.stopped
        ) {
          this.runtime = undefined;
          this.unexpectedExit = true;
        }
      };
      child.once("exit", markUnexpected);
      child.once("error", markUnexpected);
    } else {
      token = this.options.connectToken ?? "";
      if (token.length < 32) throw new HostError("invalid_configuration", 500);
    }

    const candidate = this.clientFactory(this.options.baseUrl, token);
    const deadline = Date.now() + this.startupTimeoutMs;
    while (Date.now() < deadline) {
      if (this.stopped) {
        await this.disposeCurrentChild();
        throw new HostError("runtime_unavailable", 503);
      }
      if (
        spawnedGeneration !== undefined &&
        (this.generation !== spawnedGeneration ||
          this.child?.exitCode !== null ||
          this.unexpectedExit)
      ) {
        break;
      }
      try {
        const health = await candidate.health(AbortSignal.timeout(1_000));
        if (
          health.package_version !== RUNTIME_PACKAGE_VERSION ||
          health.api_version !== RUNTIME_API_VERSION
        ) {
          await this.disposeCurrentChild();
          throw new HostError("runtime_incompatible", 503);
        }
        if (this.stopped) {
          await this.disposeCurrentChild();
          throw new HostError("runtime_unavailable", 503);
        }
        this.runtime = candidate;
        return;
      } catch (error: unknown) {
        if (error instanceof HostError) throw error;
        await delay(50);
      }
    }
    await this.disposeCurrentChild();
    throw new HostError("runtime_startup_failed", 503);
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.runtime = undefined;
    this.unexpectedExit = false;
    const pendingStart = this.startPromise;
    await this.disposeCurrentChild();
    if (pendingStart !== undefined) await pendingStart.catch(() => undefined);
  }

  private async disposeCurrentChild(): Promise<void> {
    const child = this.child;
    this.child = undefined;
    this.generation += 1;
    if (child === undefined || child.exitCode !== null) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    child.kill("SIGTERM");
    await Promise.race([exited, delay(5_000)]);
    if (child.exitCode === null) {
      child.kill("SIGKILL");
      await Promise.race([exited, delay(1_000)]);
    }
  }
}

export class SupervisedRuntime implements RuntimePort {
  constructor(private readonly supervisor: RuntimeSupervisor) {}

  async health(signal?: AbortSignal): Promise<HealthResponse> {
    return (await this.supervisor.client()).health(signal);
  }

  async adapters(probe = false, signal?: AbortSignal): Promise<AdaptersResponse> {
    const client = await this.supervisor.client();
    if (client.adapters === undefined) throw new HostError("catalog_unavailable", 503);
    return client.adapters(probe, signal);
  }

  async setAdapterActivation(
    body: { option_id: string; enabled: boolean }, signal?: AbortSignal
  ): Promise<AdaptersResponse> {
    const client = await this.supervisor.client();
    if (client.setAdapterActivation === undefined) throw new HostError("activation_unavailable", 503);
    return client.setAdapterActivation(body, signal);
  }

  async profiles(
    includeHealth = false,
    signal?: AbortSignal,
    includeDiscovery = false
  ): Promise<ProfilesResponse> {
    return (await this.supervisor.client()).profiles(includeHealth, signal, includeDiscovery);
  }

  async selectProfile(
    body: { profile_id: string },
    signal?: AbortSignal
  ): Promise<ProfilesResponse> {
    return (await this.supervisor.client()).selectProfile(body, signal);
  }

  async createSession(body: SessionRequest, signal?: AbortSignal): Promise<SessionResponse> {
    return (await this.supervisor.client()).createSession(body, signal);
  }

  async session(sessionId: string, signal?: AbortSignal): Promise<SessionResponse> {
    return (await this.supervisor.client()).session(sessionId, signal);
  }

  async events(
    sessionId: string,
    after = 0,
    signal?: AbortSignal
  ): Promise<EventsResponse> {
    return (await this.supervisor.client()).events(sessionId, after, signal);
  }

  async *streamEvents(
    sessionId: string,
    after = 0,
    signal?: AbortSignal
  ): AsyncGenerator<SessionEvent> {
    const runtime = await this.supervisor.client();
    yield* runtime.streamEvents(sessionId, after, signal);
  }

  async submitToolResults(
    sessionId: string,
    body: ToolResultsRequest,
    signal?: AbortSignal
  ): Promise<SessionResponse> {
    return (await this.supervisor.client()).submitToolResults(sessionId, body, signal);
  }

  async continueSession(
    sessionId: string,
    body: { prompt: string },
    signal?: AbortSignal
  ): Promise<SessionResponse> {
    return (await this.supervisor.client()).continueSession(sessionId, body, signal);
  }

  async cancelSession(sessionId: string, signal?: AbortSignal): Promise<SessionResponse> {
    return (await this.supervisor.client()).cancelSession(sessionId, signal);
  }

  async embeddingProfiles(signal?: AbortSignal): Promise<EmbeddingProfilesResponse> {
    return (await this.supervisor.client()).embeddingProfiles(signal);
  }

  async embed(body: EmbeddingRequest, signal?: AbortSignal): Promise<EmbeddingResponse> {
    return (await this.supervisor.client()).embed(body, signal);
  }
}
