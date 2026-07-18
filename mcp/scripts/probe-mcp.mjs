#!/usr/bin/env node
// Smoke-test the stdio CLI: the shared protocol assertions (initialize,
// tools/list, read-only annotations, newline-framed JSON-RPC transport) come
// from saagar-mcp-kit's stdio driver; this script keeps only what is
// OPERANT-specific — the expected tool set, server name, and get_results checks.
// Requires: npm run build:cli (produces dist/stdio.js)
//
//   node scripts/probe-mcp.mjs

import { join, resolve } from "node:path";
import { probeStdioServer } from "saagar-mcp-kit/stdio-probe";

const MCP_DIR = new URL("..", import.meta.url).pathname;
const STDIO_BIN = join(resolve(MCP_DIR), "dist/stdio.js");

const EXPECTED_TOOLS = [
	"compare_models",
	"get_case",
	"get_methodology",
	"get_results",
	"list_cases",
];

async function probe() {
	const { callResults } = await probeStdioServer("node", [STDIO_BIN], {
		serverName: "operant-mcp",
		tools: EXPECTED_TOOLS,
		clientName: "probe",
		calls: [{ name: "get_results", arguments: {} }],
	});

	const resultsText = callResults[0]?.content?.[0]?.text ?? "{}";
	const resultsPayload = JSON.parse(resultsText);
	if ((resultsPayload.models?.length ?? 0) === 0) {
		throw new Error(`get_results returned ${resultsPayload.models?.length ?? 0} models`);
	}
	if (typeof resultsPayload.caveat !== "string" || resultsPayload.caveat.length === 0) {
		throw new Error("get_results is missing the methodology caveat");
	}
	if (
		resultsPayload.results_status !==
		"CALCULATION_PROFILES_NOT_DURABLE_MODEL_CLAIMS"
	) {
		throw new Error("get_results is missing the fail-closed results status");
	}
	if (
		resultsPayload.claim_status?.historical_reference_profiles
			?.cross_model_ranking !== "NOT_DURABLE"
	) {
		throw new Error("get_results does not preserve the ranking claim boundary");
	}
	if (resultsPayload.caveat.includes("reliable ranking")) {
		throw new Error("get_results still advertises reliable model ranking");
	}

	console.log("Probe PASSED.");
}

probe().catch((err) => {
	console.error(`Probe FAILED: ${err.message}`);
	process.exit(1);
});
