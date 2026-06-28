// Cloudflare Worker transport for the OPERANT MCP server.
//
// Stateless streamable HTTP: a fresh McpServer + transport is built per request
// (the official MCP stateless pattern) so no state crosses requests. The shared
// server core (tools, resources, prompts) lives in ./server.
//
// The Worker is also operant.saagarpatel.dev's only origin (no static host), so it
// serves the signed discovery manifest at the well-known paths itself.

import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { buildServer } from "./server";
import {
	MANIFEST_JSON,
	MANIFEST_PUBKEY,
	MANIFEST_SIG,
} from "./well-known.generated";

const CORS: Record<string, string> = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
	"Access-Control-Allow-Headers":
		"Content-Type, Mcp-Session-Id, MCP-Protocol-Version, Accept",
	"Access-Control-Expose-Headers": "Mcp-Session-Id",
};

// Static discovery documents, served verbatim so an agent or registry can find and
// verify this server. The bytes are exactly what the detached Ed25519 signature
// covers (see scripts/sign-manifest.mjs + scripts/build-wellknown.mjs).
const WELL_KNOWN: Record<string, { body: string; contentType: string }> = {
	"/.well-known/mcp.json": {
		body: MANIFEST_JSON,
		contentType: "application/json; charset=utf-8",
	},
	"/.well-known/mcp.json.sig": {
		body: MANIFEST_SIG,
		contentType: "text/plain; charset=utf-8",
	},
	"/.well-known/mcp-ed25519.pub": {
		body: MANIFEST_PUBKEY,
		contentType: "text/plain; charset=utf-8",
	},
};

export default {
	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);

		if (request.method === "OPTIONS") {
			return new Response(null, { status: 204, headers: CORS });
		}

		const doc = WELL_KNOWN[url.pathname];
		if (doc) {
			if (request.method !== "GET" && request.method !== "HEAD") {
				return Response.json(
					{ error: "Method not allowed" },
					{ status: 405, headers: { ...CORS, Allow: "GET, HEAD" } },
				);
			}
			return new Response(request.method === "HEAD" ? null : doc.body, {
				status: 200,
				headers: {
					...CORS,
					"Content-Type": doc.contentType,
					"Cache-Control": "public, max-age=3600",
				},
			});
		}

		if (url.pathname !== "/mcp") {
			return Response.json(
				{
					error: "Not found",
					mcp_endpoint: "/mcp",
					discovery: "/.well-known/mcp.json",
					benchmark: "OPERANT",
					site: "https://operant.saagarpatel.dev",
				},
				{ status: 404, headers: CORS },
			);
		}

		const server = buildServer();
		const transport = new WebStandardStreamableHTTPServerTransport({
			sessionIdGenerator: undefined, // stateless
			enableJsonResponse: true,
		});
		await server.connect(transport);

		const res = await transport.handleRequest(request);
		const headers = new Headers(res.headers);
		for (const [k, v] of Object.entries(CORS)) headers.set(k, v);
		return new Response(res.body, {
			status: res.status,
			statusText: res.statusText,
			headers,
		});
	},
} satisfies ExportedHandler;
