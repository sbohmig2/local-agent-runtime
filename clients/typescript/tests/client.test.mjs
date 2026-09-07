import test from "node:test";
import assert from "node:assert/strict";
import { RuntimeClient, RuntimeError } from "../dist/index.js";
import { readFileSync } from "node:fs";

test("client preserves separate system instructions and compatibility health", async () => {
  const requests = [];
  const client = new RuntimeClient({baseUrl: "http://127.0.0.1:8765", bearerToken: "t".repeat(40),
    fetch: async (url, init) => {
      requests.push({url, init});
      if (url.endsWith("/v1/health")) {
        return Response.json({status: "available", package_version: "0.1.2", api_version: "1.0.0"});
      }
      return Response.json({id: "session"});
    }});
  assert.deepEqual(await client.health(),
    {status: "available", package_version: "0.1.2", api_version: "1.0.0"});
  await client.createSession({prompt: "question", instructions: "trusted product policy"});
  assert.deepEqual(JSON.parse(requests[1].init.body),
    {prompt: "question", instructions: "trusted product policy"});
});

test("embedding client preserves profile, purpose and compatibility guard", async () => {
  let observed;
  const client = new RuntimeClient({baseUrl: "http://[::1]:8765", bearerToken: "t".repeat(40),
    fetch: async (url, init) => {
      observed = {url, init};
      return Response.json({vectors: [[1, 2]], profile_fingerprint: "space"});
    }});
  const result = await client.embed({profile_id: "vectors", inputs: ["text"], purpose: "query",
    expected_fingerprint: "space", allow_external_processing: false});
  assert.deepEqual(result.vectors, [[1, 2]]);
  assert.equal(observed.url, "http://[::1]:8765/v1/embeddings");
  assert.equal(JSON.parse(observed.init.body).expected_fingerprint, "space");
  assert.equal(observed.init.redirect, "error");
});

test("client refuses non-loopback endpoints and redacts server failures", async () => {
  assert.throws(() => new RuntimeClient({baseUrl: "https://example.com", bearerToken: "t".repeat(40)}));
  const fixture = JSON.parse(readFileSync(new URL("../../../contracts/conformance.json", import.meta.url)));
  const client = new RuntimeClient({baseUrl: "http://127.0.0.1:8765", bearerToken: "t".repeat(40),
    fetch: async () => Response.json(fixture.error, {status: 403})});
  await assert.rejects(client.health(), error => error instanceof RuntimeError
    && error.code === "processing_not_allowed" && !error.message.includes(fixture.error.error.message));
});

test("SSE is decoded across arbitrary UTF-8 and frame boundaries", async () => {
  const events = JSON.parse(readFileSync(new URL("../../../contracts/conformance.json", import.meta.url))).events;
  const bytes = new TextEncoder().encode(events.map(event => `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(""));
  const client = new RuntimeClient({baseUrl: "http://localhost:8765", bearerToken: "t".repeat(40),
    fetch: async () => new Response(new ReadableStream({start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    }}))});
  const received = [];
  for await (const event of client.streamEvents("session")) received.push(event);
  assert.deepEqual(received, events);
});

test("generated methods cover every canonical OpenAPI operation", () => {
  const spec = JSON.parse(readFileSync(new URL("../../../contracts/openapi.json", import.meta.url)));
  for (const path of Object.values(spec.paths)) {
    for (const operation of Object.values(path)) {
      assert.equal(typeof RuntimeClient.prototype[operation.operationId], "function");
    }
  }
});

test("stopping event consumption cancels the response stream", async () => {
  let canceled = false;
  const event = {sequence: 1, type: "session_created", occurred_at: "now", payload: {}};
  const frame = new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);
  const client = new RuntimeClient({baseUrl: "http://localhost:8765", bearerToken: "t".repeat(40),
    fetch: async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(frame); },
      cancel() { canceled = true; },
    }))});
  for await (const _event of client.streamEvents("session")) break;
  assert.equal(canceled, true);
});
