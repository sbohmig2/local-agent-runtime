/** Backend-only transport: never put the gateway bearer token in browser state. */
import type { SessionEvent } from "./generated.js";

export interface RuntimeClientOptions {
  baseUrl: string;
  bearerToken: string;
  fetch?: typeof fetch;
}

export class RuntimeError extends Error {
  constructor(public readonly code: string, public readonly status: number) {
    super("Local Agent Runtime request failed: " + code);
  }
}

export class RuntimeTransport {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly fetcher: typeof fetch;

  constructor(options: RuntimeClientOptions) {
    const url = new URL(options.baseUrl);
    if (url.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new Error("Runtime endpoint must be a loopback HTTP origin");
    }
    if (options.bearerToken.length < 32) throw new Error("Runtime token is invalid");
    this.baseUrl = url.origin;
    this.token = options.bearerToken;
    this.fetcher = options.fetch ?? fetch;
  }

  private async response(path: string, method: string, body: unknown,
    signal?: AbortSignal, stream = false): Promise<Response> {
    const response = await this.fetcher(this.baseUrl + path, {
      method, signal, redirect: "error", cache: "no-store",
      headers: { Authorization: "Bearer " + this.token, "Content-Type": "application/json",
        Accept: stream ? "text/event-stream" : "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      let code = "request_failed";
      try {
        const payload = await this.readJson<{error?: {code?: unknown}}>(response, 16_384);
        const candidate = payload.error?.code;
        if (typeof candidate === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(candidate)) {
          code = candidate;
        }
      } catch {
        // Preserve the generic code. Never expose an untrusted response body.
      }
      throw new RuntimeError(code, response.status);
    }
    return response;
  }

  private async readJson<T>(response: Response, maxBytes: number): Promise<T> {
    const length = response.headers.get("content-length");
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > maxBytes)) {
      throw new Error("Runtime response exceeds its limit");
    }
    if (!response.body) throw new Error("Runtime response body is unavailable");
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) throw new Error("Runtime response exceeds its limit");
        chunks.push(value);
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)) as T;
  }

  protected async request<T>(path: string, method: string, body?: unknown,
    signal?: AbortSignal): Promise<T> {
    const response = await this.response(path, method, body, signal);
    return await this.readJson<T>(response, 34_000_000);
  }

  async *streamEvents(sessionId: string, after = 0, signal?: AbortSignal): AsyncGenerator<SessionEvent> {
    if (!Number.isSafeInteger(after) || after < 0) throw new Error("Invalid event cursor");
    const response = await this.response("/v1/sessions/" + encodeURIComponent(sessionId)
      + "/events?after=" + String(after), "GET", undefined, signal, true);
    if (!response.body) throw new Error("Runtime event stream unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let cursor = after;
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        if (buffer.length > 1_200_000) throw new Error("Runtime event exceeds its limit");
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = frame.split("\n").filter(line => line.startsWith("data: "))
            .map(line => line.slice(6)).join("\n");
          if (!data) continue;
          const event = JSON.parse(data) as SessionEvent;
          if (event.sequence !== cursor + 1 || typeof event.type !== "string") {
            throw new Error("Runtime event sequence is invalid");
          }
          cursor = event.sequence;
          yield event;
        }
        if (done) {
          if (buffer.trim()) throw new Error("Runtime event stream was truncated");
          break;
        }
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  }
}
