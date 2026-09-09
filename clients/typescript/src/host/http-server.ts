import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

import type {
  ContinueSessionOptions,
  HostEvent,
  HostSession,
  PublicProfile,
  PublicAdapterCatalog,
  PublicModelOptions,
  ReasoningEffort,
  StartSessionRequest
} from "./contracts.js";
import { HostError, safeError } from "./errors.js";

const MAX_BODY_BYTES = 1_000_000;
const DEFAULT_MAX_EVENT_STREAMS = 100;
const SSE_HEARTBEAT_MS = 15_000;
const TERMINAL = new Set(["approval_required", "completed", "failed", "canceled"]);
const REASONING_EFFORTS: ReadonlySet<string> = new Set([
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max"
]);

function reasoningEffort(value: unknown): ReasoningEffort | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !REASONING_EFFORTS.has(value)) {
    throw new HostError("invalid_request", 400);
  }
  return value as ReasoningEffort;
}

function sameToken(header: string | undefined, expected: string): boolean {
  if (header === undefined || !header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7));
  const wanted = Buffer.from(expected);
  return supplied.length === wanted.length && timingSafeEqual(supplied, wanted);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HostError("invalid_request", 400);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): void {
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new HostError("invalid_request", 400);
  }
}

function exactQuery(url: URL, allowed: ReadonlySet<string>): void {
  const keys = [...url.searchParams.keys()];
  if (keys.some((key) => !allowed.has(key)) || new Set(keys).size !== keys.length) {
    throw new HostError("invalid_request", 400);
  }
}

export interface ProductAgentPort {
  adapters?(probe?: boolean): Promise<PublicAdapterCatalog>;
  setAdapterActivation?(optionId: string, enabled: boolean): Promise<PublicAdapterCatalog>;
  health(): Promise<{ status: "available"; runtimeVersion: string; apiVersion: string }>;
  profiles(
    includeHealth?: boolean,
    includeDiscovery?: boolean
  ): Promise<{
    selectedProfile: string;
    profiles: PublicProfile[];
  }>;
  modelOptions(profileId: string): Promise<PublicModelOptions>;
  selectProfile(profileId: string): Promise<{
    selectedProfile: string;
    profiles: PublicProfile[];
  }>;
  start(request: StartSessionRequest): Promise<HostSession>;
  continue(
    sessionId: string,
    prompt: string,
    options?: ContinueSessionOptions
  ): Promise<HostSession>;
  session(sessionId: string): HostSession;
  events(sessionId: string, after?: number): HostEvent[];
  cancel(sessionId: string): Promise<HostSession>;
}

export interface HostHttpOptions {
  host: "127.0.0.1" | "::1";
  port: number;
  applicationToken: string;
  allowedOrigins: ReadonlySet<string>;
  pathPrefix?: string;
  maxEventStreams?: number;
}

export class HostHttpServer {
  private server: Server | undefined;
  private startPromise: Promise<void> | undefined;
  private boundPort: number | undefined;
  private readonly pathPrefix: string;
  private readonly maxEventStreams: number;
  private readonly eventStreams = new Set<ServerResponse>();
  private stopping = false;

  constructor(
    private readonly host: ProductAgentPort,
    private readonly options: HostHttpOptions
  ) {
    this.pathPrefix = options.pathPrefix ?? "/api/local-agent";
    this.maxEventStreams = options.maxEventStreams ?? DEFAULT_MAX_EVENT_STREAMS;
    let originsValid = true;
    try {
      originsValid = [...options.allowedOrigins].every((origin) => {
        const value = new URL(origin);
        return ["http:", "https:"].includes(value.protocol) && value.origin === origin;
      });
    } catch {
      originsValid = false;
    }
    if (
      options.applicationToken.length < 32 ||
      (options.host !== "127.0.0.1" && options.host !== "::1") ||
      options.allowedOrigins.size === 0 ||
      !originsValid ||
      !Number.isSafeInteger(options.port) ||
      options.port < 0 ||
      options.port > 65535 ||
      !Number.isSafeInteger(this.maxEventStreams) ||
      this.maxEventStreams < 1 ||
      !/^\/[a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)*$/.test(this.pathPrefix)
    ) {
      throw new HostError("invalid_configuration", 500);
    }
  }

  async start(): Promise<void> {
    if (this.boundPort !== undefined) return;
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
    this.stopping = false;
    this.server = createServer((request, response) => {
      void this.handle(request, response).catch((error: unknown) => {
        this.failure(response, safeError(error));
      });
    });
    try {
      await new Promise<void>((resolve, reject) => {
        this.server?.once("error", reject);
        this.server?.listen(this.options.port, this.options.host, () => {
          const address = this.server?.address();
          if (typeof address === "object" && address !== null) this.boundPort = address.port;
          resolve();
        });
      });
    } catch {
      this.server = undefined;
      this.boundPort = undefined;
      throw new HostError("host_startup_failed", 503);
    }
  }

  baseUrl(): string {
    if (this.boundPort === undefined) throw new HostError("host_unavailable", 503);
    const host = this.options.host === "::1" ? "[::1]" : this.options.host;
    return `http://${host}:${String(this.boundPort)}`;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    const server = this.server;
    this.server = undefined;
    this.boundPort = undefined;
    if (server === undefined) return;
    const closed = new Promise<void>((resolve, reject) => {
      server.close((error) => (error === undefined ? resolve() : reject(error)));
    });
    for (const response of this.eventStreams) response.end();
    server.closeAllConnections();
    await closed;
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    const port = this.boundPort;
    if (port === undefined) throw new HostError("host_unavailable", 503);
    const expectedHost =
      this.options.host === "::1" ? `[::1]:${String(port)}` : `127.0.0.1:${String(port)}`;
    if (request.headers.host !== expectedHost) throw new HostError("invalid_host", 400);
    const origin = request.headers.origin;
    if (origin !== undefined && !this.options.allowedOrigins.has(origin)) {
      throw new HostError("invalid_origin", 403);
    }
    if (origin !== undefined) {
      response.setHeader("Access-Control-Allow-Origin", origin);
      response.setHeader("Vary", "Origin");
    }
    if (request.method === "OPTIONS") {
      if (origin === undefined) throw new HostError("invalid_origin", 403);
      const requestedMethod = request.headers["access-control-request-method"];
      const requestedHeaders = (request.headers["access-control-request-headers"] ?? "")
        .split(",")
        .map((value) => value.trim().toLowerCase())
        .filter((value) => value !== "");
      if (
        (requestedMethod !== "GET" && requestedMethod !== "POST") ||
        requestedHeaders.some((value) => value !== "authorization" && value !== "content-type")
      ) {
        throw new HostError("invalid_request", 400);
      }
      response.writeHead(204, {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Max-Age": "600"
      });
      response.end();
      return;
    }
    if (!sameToken(request.headers.authorization, this.options.applicationToken)) {
      throw new HostError("unauthorized", 401);
    }
    const url = new URL(request.url ?? "/", `http://${expectedHost}`);

    if (request.method === "GET" && url.pathname === `${this.pathPrefix}/health`) {
      exactQuery(url, new Set());
      this.json(response, 200, await this.host.health());
      return;
    }
    if (request.method === "GET" && url.pathname === `${this.pathPrefix}/profiles`) {
      exactQuery(url, new Set(["health", "discovery"]));
      const health = url.searchParams.get("health") ?? "false";
      const discovery = url.searchParams.get("discovery") ?? "false";
      if (
        (health !== "true" && health !== "false") ||
        (discovery !== "true" && discovery !== "false")
      ) {
        throw new HostError("invalid_request", 400);
      }
      this.json(response, 200, await this.host.profiles(health === "true", discovery === "true"));
      return;
    }
    const modelOptionsMatch = new RegExp(
      `^${this.pathPrefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/profiles/([a-z][a-z0-9_-]{0,63})/model-options$`
    ).exec(url.pathname);
    if (request.method === "GET" && modelOptionsMatch !== null) {
      exactQuery(url, new Set());
      const profileId = modelOptionsMatch[1];
      if (profileId === undefined) throw new HostError("invalid_request", 400);
      this.json(response, 200, await this.host.modelOptions(profileId));
      return;
    }
    if (request.method === "GET" && url.pathname === `${this.pathPrefix}/adapters`) {
      exactQuery(url, new Set(["probe"]));
      const probe = url.searchParams.get("probe") ?? "false";
      if (probe !== "true" && probe !== "false") throw new HostError("invalid_request", 400);
      if (this.host.adapters === undefined) throw new HostError("catalog_unavailable", 503);
      this.json(response, 200, await this.host.adapters(probe === "true"));
      return;
    }
    if (request.method === "POST" && url.pathname === `${this.pathPrefix}/adapter-activation`) {
      exactQuery(url, new Set());
      const body = object(await this.readBody(request));
      exactKeys(body, new Set(["optionId", "enabled"]));
      if (typeof body.optionId !== "string" || typeof body.enabled !== "boolean") {
        throw new HostError("invalid_request", 400);
      }
      if (this.host.setAdapterActivation === undefined) throw new HostError("activation_unavailable", 503);
      this.json(response, 200, await this.host.setAdapterActivation(body.optionId, body.enabled));
      return;
    }
    if (request.method === "POST" && url.pathname === `${this.pathPrefix}/selection`) {
      exactQuery(url, new Set());
      const body = object(await this.readBody(request));
      exactKeys(body, new Set(["profileId"]));
      if (typeof body.profileId !== "string") throw new HostError("invalid_request", 400);
      this.json(response, 200, await this.host.selectProfile(body.profileId));
      return;
    }
    if (request.method === "POST" && url.pathname === `${this.pathPrefix}/sessions`) {
      exactQuery(url, new Set());
      const body = object(await this.readBody(request));
      exactKeys(
        body,
        new Set([
          "prompt",
          "profileId",
          "taskCode",
          "privateProcessing",
          "allowExternalProcessing",
          "reasoningEffort",
          "modelOptionId"
        ])
      );
      if (typeof body.prompt !== "string") throw new HostError("invalid_request", 400);
      const effort = reasoningEffort(body.reasoningEffort);
      const start: StartSessionRequest = {
        prompt: body.prompt,
        ...(typeof body.profileId === "string" ? { profileId: body.profileId } : {}),
        ...(typeof body.taskCode === "string" ? { taskCode: body.taskCode } : {}),
        ...(typeof body.privateProcessing === "boolean"
          ? { privateProcessing: body.privateProcessing }
          : {}),
        ...(typeof body.allowExternalProcessing === "boolean"
          ? { allowExternalProcessing: body.allowExternalProcessing }
          : {}),
        ...(effort === undefined ? {} : { reasoningEffort: effort }),
        ...(typeof body.modelOptionId === "string"
          ? { modelOptionId: body.modelOptionId }
          : {})
      };
      if (
        (body.profileId !== undefined && typeof body.profileId !== "string") ||
        (body.taskCode !== undefined && typeof body.taskCode !== "string") ||
        (body.privateProcessing !== undefined && typeof body.privateProcessing !== "boolean") ||
        (body.allowExternalProcessing !== undefined &&
          typeof body.allowExternalProcessing !== "boolean") ||
        (body.modelOptionId !== undefined && typeof body.modelOptionId !== "string")
      ) {
        throw new HostError("invalid_request", 400);
      }
      this.json(response, 202, await this.host.start(start));
      return;
    }

    const escapedPrefix = this.pathPrefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = new RegExp(
      `^${escapedPrefix}/sessions/([a-f0-9-]+)(?:/(events|input|cancel))?$`
    ).exec(url.pathname);
    if (match !== null) {
      const sessionId = match[1];
      const operation = match[2];
      if (sessionId === undefined) throw new HostError("invalid_request", 400);
      if (request.method === "GET" && operation === undefined) {
        exactQuery(url, new Set());
        this.json(response, 200, this.host.session(sessionId));
        return;
      }
      if (request.method === "GET" && operation === "events") {
        exactQuery(url, new Set(["after"]));
        const rawAfter = url.searchParams.get("after") ?? "0";
        if (!/^(0|[1-9][0-9]{0,14})$/.test(rawAfter)) {
          throw new HostError("invalid_request", 400);
        }
        const after = Number(rawAfter);
        if (!Number.isSafeInteger(after)) throw new HostError("invalid_request", 400);
        if (request.headers.accept === "text/event-stream") {
          await this.streamEvents(request, response, sessionId, after);
        } else {
          this.json(response, 200, { events: this.host.events(sessionId, after) });
        }
        return;
      }
      if (request.method === "POST" && operation === "input") {
        exactQuery(url, new Set());
        const body = object(await this.readBody(request));
        exactKeys(body, new Set(["prompt", "reasoningEffort"]));
        if (typeof body.prompt !== "string") throw new HostError("invalid_request", 400);
        const effort = reasoningEffort(body.reasoningEffort);
        this.json(
          response,
          202,
          await this.host.continue(
            sessionId,
            body.prompt,
            effort === undefined ? {} : { reasoningEffort: effort }
          )
        );
        return;
      }
      if (request.method === "POST" && operation === "cancel") {
        exactQuery(url, new Set());
        this.json(response, 200, await this.host.cancel(sessionId));
        return;
      }
    }
    throw new HostError("not_found", 404);
  }

  private async streamEvents(
    request: IncomingMessage,
    response: ServerResponse,
    sessionId: string,
    after: number
  ): Promise<void> {
    let pending = this.host.events(sessionId, after);
    if (this.eventStreams.size >= this.maxEventStreams) {
      throw new HostError("event_stream_capacity_reached", 503);
    }
    this.eventStreams.add(response);
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no"
    });
    response.flushHeaders();
    response.write(": connected\n\n");
    let cursor = after;
    let connected = true;
    let lastHeartbeat = Date.now();
    request.once("close", () => {
      connected = false;
    });
    try {
      while (connected && !this.stopping) {
        for (const event of pending) {
          cursor = event.sequence;
          this.writeEvent(response, event);
        }
        if (TERMINAL.has(this.host.session(sessionId).status) && pending.length === 0) break;
        if (Date.now() - lastHeartbeat >= SSE_HEARTBEAT_MS) {
          response.write(": heartbeat\n\n");
          lastHeartbeat = Date.now();
        }
        await delay(50);
        pending = this.host.events(sessionId, cursor);
      }
    } finally {
      this.eventStreams.delete(response);
      if (!response.writableEnded) response.end();
    }
  }

  private writeEvent(response: ServerResponse, event: HostEvent): void {
    response.write(
      `id: ${String(event.sequence)}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
    );
  }

  private async readBody(request: IncomingMessage): Promise<unknown> {
    let size = 0;
    const chunks: Buffer[] = [];
    for await (const chunk of request) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as Uint8Array);
      size += buffer.length;
      if (size > MAX_BODY_BYTES) throw new HostError("request_too_large", 413);
      chunks.push(buffer);
    }
    try {
      return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
    } catch {
      throw new HostError("invalid_request", 400);
    }
  }

  private json(response: ServerResponse, status: number, value: unknown): void {
    response.statusCode = status;
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    response.end(JSON.stringify(value));
  }

  private failure(response: ServerResponse, error: HostError): void {
    if (response.headersSent) {
      response.end();
      return;
    }
    this.json(response, error.status, { error: { code: error.code } });
  }
}
