#!/usr/bin/env node
// Layer 2: the same MCP server over stdio, for running locally via npx instead
// of the remote Worker. Reuses the shared server core (./server) verbatim, so the
// tool/resource/prompt surface is identical to the hosted endpoint. Reads the same
// baked corpus; no network, no arguments.

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { buildServer } from "./server";

const server = buildServer();
const transport = new StdioServerTransport();
await server.connect(transport);
