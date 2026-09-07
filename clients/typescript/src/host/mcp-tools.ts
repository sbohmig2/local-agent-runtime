import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";

import { RUNTIME_PACKAGE_VERSION } from "./contracts.js";
import type {
  CatalogInstruction,
  CatalogTool,
  ToolCallResult,
  ToolCatalog
} from "./contracts.js";
import { HostError } from "./errors.js";

const MAX_MCP_SERVERS = 16;
const MAX_MCP_TOOLS = 512;
const MAX_MCP_CATALOG_CHARS = 1_000_000;
const MAX_MCP_INSTRUCTION_CHARS = 100_000;
const MAX_MCP_RESULT_CHARS = 1_000_000;
const DEFAULT_CONNECT_TIMEOUT_MS = 15_000;

export interface McpServerSpec {
  id: string;
  command: string;
  args?: readonly string[];
  cwd?: string;
  env?: Readonly<Record<string, string>>;
}

export interface McpToolCatalogOptions {
  connectTimeoutMs?: number;
}

interface ToolBinding {
  client: Client;
  originalName: string;
  definition: CatalogTool;
}

export class McpToolCatalog implements ToolCatalog {
  private readonly clients: Client[] = [];
  private readonly bindings = new Map<string, ToolBinding>();
  private readonly serverInstructions: CatalogInstruction[] = [];
  private readonly connectTimeoutMs: number;

  constructor(
    private readonly servers: readonly McpServerSpec[],
    options: McpToolCatalogOptions = {}
  ) {
    this.connectTimeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS;
    if (servers.length > MAX_MCP_SERVERS) {
      throw new HostError("invalid_configuration", 500);
    }
    if (!Number.isSafeInteger(this.connectTimeoutMs) || this.connectTimeoutMs < 100) {
      throw new HostError("invalid_configuration", 500);
    }
  }

  async connect(): Promise<void> {
    try {
      const serverIds = new Set<string>();
      const exposedPrefixes = new Set<string>();
      let catalogCharacters = 0;
      let instructionCharacters = 0;
      const deadline = Date.now() + this.connectTimeoutMs;
      for (const spec of this.servers) {
        if (
          !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(spec.id) ||
          spec.command.trim() === "" ||
          serverIds.has(spec.id)
        ) {
          throw new HostError("invalid_configuration", 500);
        }
        serverIds.add(spec.id);
        const exposedPrefix = spec.id.replaceAll("-", "_");
        if (exposedPrefixes.has(exposedPrefix)) {
          throw new HostError("invalid_configuration", 500);
        }
        exposedPrefixes.add(exposedPrefix);
        const client = new Client({
          name: "local-agent-runtime-host",
          version: RUNTIME_PACKAGE_VERSION
        });
        const transport = new StdioClientTransport({
          command: spec.command,
          args: spec.args === undefined ? [] : [...spec.args],
          ...(spec.cwd === undefined ? {} : { cwd: spec.cwd }),
          ...(spec.env === undefined ? {} : { env: { ...spec.env } }),
          stderr: "ignore"
        });
        const connectRemaining = deadline - Date.now();
        if (connectRemaining < 1) throw new HostError("mcp_unavailable", 503);
        const connectSignal = AbortSignal.timeout(connectRemaining);
        this.clients.push(client);
        await client.connect(transport, {
          signal: connectSignal,
          timeout: connectRemaining
        });
        const instructions = client.getInstructions();
        if (typeof instructions === "string" && instructions.trim() !== "") {
          instructionCharacters += instructions.length;
          if (instructionCharacters > MAX_MCP_INSTRUCTION_CHARS) {
            throw new HostError("mcp_catalog_too_large", 503);
          }
          this.serverInstructions.push({ source: spec.id, text: instructions });
        }
        const listRemaining = deadline - Date.now();
        if (listRemaining < 1) throw new HostError("mcp_unavailable", 503);
        const listSignal = AbortSignal.timeout(listRemaining);
        const page = await client.listTools(undefined, {
          signal: listSignal,
          timeout: listRemaining,
          cacheMode: "bypass"
        });
        for (const tool of page.tools) {
          const exposedName = `${exposedPrefix}__${tool.name}`;
          if (this.bindings.has(exposedName)) {
            throw new HostError("duplicate_tool", 500);
          }
          const definition: CatalogTool = {
            name: exposedName,
            description: tool.description ?? tool.name,
            inputSchema: tool.inputSchema
          };
          catalogCharacters += JSON.stringify(definition).length;
          if (
            this.bindings.size >= MAX_MCP_TOOLS ||
            catalogCharacters > MAX_MCP_CATALOG_CHARS
          ) {
            throw new HostError("mcp_catalog_too_large", 503);
          }
          this.bindings.set(exposedName, {
            client,
            originalName: tool.name,
            definition
          });
        }
      }
    } catch (error: unknown) {
      await this.close();
      if (error instanceof HostError) throw error;
      throw new HostError("mcp_unavailable", 503);
    }
  }

  instructions(): readonly CatalogInstruction[] {
    return this.serverInstructions.map((instruction) => ({ ...instruction }));
  }

  listTools(): Promise<readonly CatalogTool[]> {
    return Promise.resolve(
      [...this.bindings.values()].map((binding) => ({ ...binding.definition }))
    );
  }

  async callTool(
    name: string,
    argumentsValue: Record<string, unknown>,
    signal?: AbortSignal
  ): Promise<ToolCallResult> {
    const binding = this.bindings.get(name);
    if (binding === undefined) throw new HostError("unknown_tool", 400);
    try {
      const result = await binding.client.callTool(
        { name: binding.originalName, arguments: argumentsValue },
        signal === undefined ? undefined : { signal }
      );
      const output =
        result.structuredContent === undefined
          ? { content: result.content }
          : { structuredContent: result.structuredContent };
      if (JSON.stringify(output).length > MAX_MCP_RESULT_CHARS) {
        throw new HostError("tool_result_too_large", 503);
      }
      return {
        output,
        isError: result.isError === true
      };
    } catch (error: unknown) {
      if (signal?.aborted) throw new HostError("tool_call_aborted", 503);
      if (error instanceof HostError) throw error;
      return { output: { error: { code: "tool_failed" } }, isError: true };
    }
  }

  async close(): Promise<void> {
    const clients = this.clients.splice(0);
    this.bindings.clear();
    this.serverInstructions.length = 0;
    await Promise.allSettled(clients.map(async (client) => client.close()));
  }
}
