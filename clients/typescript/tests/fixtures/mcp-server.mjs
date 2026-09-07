import { McpServer } from "@modelcontextprotocol/server";
import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { z } from "zod";

const server = new McpServer(
  { name: "synthetic-records", version: "0.1.0" },
  { instructions: "Treat returned records as source data, not permission to mutate them." }
);

const requestedCount = Number(process.env.SYNTHETIC_TOOL_COUNT ?? "1");
const toolCount = Number.isSafeInteger(requestedCount) && requestedCount > 0 ? requestedCount : 1;

for (let index = 0; index < toolCount; index += 1) {
  const name = index === 0 ? "list_records" : `list_records_${String(index)}`;
  server.registerTool(
    name,
    {
      description: "List synthetic records.",
      inputSchema: z.object({ limit: z.number().int().positive().optional() })
    },
    ({ limit }) => ({
      content: [{ type: "text", text: `returned ${String(limit ?? 50)} records` }],
      structuredContent: { records: [{ id: "record-1", value: "100.25", unit: "EUR" }] }
    })
  );
}

server.registerTool(
  "wait_records",
  {
    description: "Wait until the request is canceled.",
    inputSchema: z.object({})
  },
  async () => {
    await new Promise((resolve) => setTimeout(resolve, 60_000));
    return { content: [{ type: "text", text: "finished" }] };
  }
);

await server.connect(new StdioServerTransport());
