#!/usr/bin/env node
// Smoke-test the stdio CLI: initializes the server and verifies the 5 tools are
// registered, all annotated readOnlyHint, and that get_results returns model profiles.
// Requires: npm run build:cli (produces dist/stdio.js)
//
//   node scripts/probe-mcp.mjs

import { spawn } from "node:child_process";
import { join, resolve } from "node:path";

const MCP_DIR = new URL("..", import.meta.url).pathname;
const STDIO_BIN = join(resolve(MCP_DIR), "dist/stdio.js");

const EXPECTED_TOOLS = [
	"compare_models",
	"get_case",
	"get_methodology",
	"get_results",
	"list_cases",
];

// ---- framed stdio transport helpers ----

function sendMsg(proc, msg) {
	const json = JSON.stringify(msg);
	proc.stdin.write(
		`Content-Length: ${Buffer.byteLength(json, "utf8")}\r\n\r\n${json}`,
	);
}

function parseFrames(buf) {
	const messages = [];
	let rest = buf;
	while (true) {
		const headerEnd = rest.indexOf("\r\n\r\n");
		if (headerEnd < 0) break;
		const header = rest.slice(0, headerEnd);
		const lenMatch = header.match(/Content-Length:\s*(\d+)/i);
		if (!lenMatch) {
			rest = rest.slice(headerEnd + 4);
			break;
		}
		const bodyLen = parseInt(lenMatch[1], 10);
		if (rest.length < headerEnd + 4 + bodyLen) break;
		const body = rest.slice(headerEnd + 4, headerEnd + 4 + bodyLen);
		rest = rest.slice(headerEnd + 4 + bodyLen);
		messages.push(JSON.parse(body));
	}
	return { messages, rest };
}

async function probe() {
	const proc = spawn("node", [STDIO_BIN], {
		stdio: ["pipe", "pipe", "inherit"],
	});

	const received = [];
	let buf = "";

	proc.stdout.on("data", (chunk) => {
		buf += chunk.toString("utf8");
		const { messages, rest } = parseFrames(buf);
		buf = rest;
		received.push(...messages);
	});

	const waitFor = (count, ms = 5000) =>
		new Promise((resolve, reject) => {
			const start = Date.now();
			const tick = setInterval(() => {
				if (received.length >= count) {
					clearInterval(tick);
					resolve(undefined);
				} else if (Date.now() - start > ms) {
					clearInterval(tick);
					reject(new Error(`Timeout: expected ${count} messages, got ${received.length}`));
				}
			}, 50);
		});

	let pass = true;

	function check(label, cond, detail = "") {
		if (!cond) {
			console.error(`FAIL [${label}]${detail ? ": " + detail : ""}`);
			pass = false;
		} else {
			console.log(`PASS [${label}]`);
		}
	}

	// 1. initialize
	sendMsg(proc, {
		jsonrpc: "2.0",
		id: 1,
		method: "initialize",
		params: {
			protocolVersion: "2025-06-18",
			capabilities: {},
			clientInfo: { name: "probe", version: "1" },
		},
	});
	await waitFor(1);
	check(
		"initialize / serverInfo.name",
		received[0]?.result?.serverInfo?.name === "operant-mcp",
		`got: ${received[0]?.result?.serverInfo?.name}`,
	);

	// 2. tools/list
	sendMsg(proc, { jsonrpc: "2.0", id: 2, method: "tools/list" });
	await waitFor(2);
	const toolsResult = received[1]?.result?.tools ?? [];
	const toolNames = toolsResult.map((t) => t.name).sort();
	check(
		"tools/list names",
		JSON.stringify(toolNames) === JSON.stringify(EXPECTED_TOOLS),
		`got: ${JSON.stringify(toolNames)}`,
	);
	check(
		"tools/list all readOnlyHint",
		toolsResult.every((t) => t.annotations?.readOnlyHint === true),
		"one or more tools missing readOnlyHint=true",
	);

	// 3. get_results
	sendMsg(proc, {
		jsonrpc: "2.0",
		id: 3,
		method: "tools/call",
		params: { name: "get_results", arguments: {} },
	});
	await waitFor(3);
	const resultsText = received[2]?.result?.content?.[0]?.text ?? "{}";
	const resultsPayload = JSON.parse(resultsText);
	check(
		"get_results / models count",
		(resultsPayload.models?.length ?? 0) > 0,
		`got ${resultsPayload.models?.length} models`,
	);
	check(
		"get_results / caveat present",
		typeof resultsPayload.caveat === "string" && resultsPayload.caveat.length > 0,
	);

	proc.kill();

	if (!pass) {
		console.error("\nProbe FAILED.");
		process.exit(1);
	}
	console.log("\nProbe PASSED.");
}

probe().catch((err) => {
	console.error("Probe error:", err.message);
	process.exit(1);
});
