// End-to-end test of the Worker fetch handler driving the MCP protocol
// (stateless streamable HTTP, JSON responses). Verifies transport wiring,
// the 5 tools, resources, and the prompt against the baked corpus.

import { describe, expect, it } from "vitest";
import handler from "../src/index";

const ENDPOINT = "http://localhost/mcp";
const HEADERS = {
	"Content-Type": "application/json",
	Accept: "application/json, text/event-stream",
};

async function rpc(body: unknown): Promise<{ status: number; json: unknown }> {
	const res = await handler.fetch(
		new Request(ENDPOINT, {
			method: "POST",
			headers: HEADERS,
			body: JSON.stringify(body),
		}),
	);
	const text = await res.text();
	return { status: res.status, json: text ? JSON.parse(text) : null };
}

const init = () =>
	rpc({
		jsonrpc: "2.0",
		id: 1,
		method: "initialize",
		params: {
			protocolVersion: "2025-06-18",
			capabilities: {},
			clientInfo: { name: "test", version: "1" },
		},
	});

describe("MCP server over the Worker fetch handler", () => {
	it("rejects non-/mcp paths with 404", async () => {
		const res = await handler.fetch(
			new Request("http://localhost/", { method: "GET" }),
		);
		expect(res.status).toBe(404);
	});

	it("answers CORS preflight", async () => {
		const res = await handler.fetch(
			new Request(ENDPOINT, { method: "OPTIONS" }),
		);
		expect(res.status).toBe(204);
		expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
	});

	it("initializes and reports server info", async () => {
		const { status, json } = await init();
		expect(status).toBe(200);
		const result = (
			json as {
				result: {
					instructions: string;
					serverInfo: { name: string; version: string };
				};
			}
		).result;
		expect(result.serverInfo.name).toBe("operant-mcp");
		expect(result.serverInfo.version).toBe("0.1.1");
		expect(result.instructions).toContain("not durable performance claims");
		expect(result.instructions).not.toContain("reliable ranking");
	});

	it("lists the 5 read-only tools", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 2,
			method: "tools/list",
		});
		const toolsResult = json as {
			result: {
				tools: Array<{
					name: string;
					description?: string;
					annotations?: { readOnlyHint?: boolean };
				}>;
			};
		};
		const names = toolsResult.result.tools.map((t) => t.name).sort();
		expect(names).toEqual([
			"compare_models",
			"get_case",
			"get_methodology",
			"get_results",
			"list_cases",
		]);
		expect(
			toolsResult.result.tools.every(
				(t) => t.annotations?.readOnlyHint === true,
			),
		).toBe(true);
		const getResults = toolsResult.result.tools.find(
			(tool) => tool.name === "get_results",
		);
		const compareModels = toolsResult.result.tools.find(
			(tool) => tool.name === "compare_models",
		);
		expect(getResults?.description).toContain(
			"not durable named-model performance claims",
		);
		expect(compareModels?.description).toContain(
			"comparison_status=NOT_DURABLE",
		);
		expect(
			toolsResult.result.tools
				.map((tool) => tool.description ?? "")
				.join("\n"),
		).not.toContain("reliable ranking");
	});

	it("calls get_results and returns profiles with the integrity boundary", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 3,
			method: "tools/call",
			params: { name: "get_results", arguments: {} },
		});
		const payload = JSON.parse(
			(json as { result: { content: Array<{ text: string }> } }).result
				.content[0].text,
		);
		expect(payload.models.length).toBeGreaterThan(0);
		expect(payload.caveat).toContain(
			"not durable named-model performance claims",
		);
		expect(payload.caveat).not.toContain("reliable ranking");
		expect(payload.results_status).toBe(
			"CALCULATION_PROFILES_NOT_DURABLE_MODEL_CLAIMS",
		);
		expect(
			payload.claim_status.historical_reference_profiles.cross_model_ranking,
		).toBe("NOT_DURABLE");
		expect(payload.evidence_binding.historical_as_run_corpus).toBe("UNKNOWN");
		expect(payload.presentation).toBe(
			"calibration_profiles_not_flat_leaderboard",
		);
	});

	it("calls compare_models and returns a side-by-side result", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 4,
			method: "tools/call",
			params: {
				name: "compare_models",
				arguments: { model_a: "Opus", model_b: "Haiku" },
			},
		});
		const payload = JSON.parse(
			(json as { result: { content: Array<{ text: string }> } }).result
				.content[0].text,
		);
		// With the baked corpus both models exist; with the placeholder, we get an error — both are valid.
		expect(payload).toHaveProperty("model_a");
		expect(payload).toHaveProperty("model_b");
		expect(payload.comparison_status).toBe("NOT_DURABLE");
		expect(payload.caveat).toContain("Do not rank models");
	});

	it("calls get_methodology and returns axes and OCS formula", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 5,
			method: "tools/call",
			params: { name: "get_methodology", arguments: {} },
		});
		const payload = JSON.parse(
			(json as { result: { content: Array<{ text: string }> } }).result
				.content[0].text,
		);
		expect(payload.benchmark).toBe("OPERANT");
		expect(payload.ocs_formula).toContain("TPR - FPR");
		expect(Array.isArray(payload.axes)).toBe(true);
	});

	it("calls list_cases and returns case metadata", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 6,
			method: "tools/call",
			params: { name: "list_cases", arguments: {} },
		});
		const payload = JSON.parse(
			(json as { result: { content: Array<{ text: string }> } }).result
				.content[0].text,
		);
		expect(typeof payload.count).toBe("number");
		expect(Array.isArray(payload.cases)).toBe(true);
	});

	it("calls list_cases with axis filter", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 7,
			method: "tools/call",
			params: {
				name: "list_cases",
				arguments: { axis: "orchestration" },
			},
		});
		const payload = JSON.parse(
			(json as { result: { content: Array<{ text: string }> } }).result
				.content[0].text,
		);
		expect(
			(payload.cases as Array<{ axis: string }>).every(
				(c) => c.axis === "orchestration",
			),
		).toBe(true);
	});

	it("lists resources including operant://results and operant://methodology", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 8,
			method: "resources/list",
		});
		const uris = (
			json as { result: { resources: Array<{ uri: string }> } }
		).result.resources.map((r) => r.uri);
		expect(uris).toContain("operant://results");
		expect(uris).toContain("operant://methodology");
	});

	it("lists the score_my_agent prompt", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 9,
			method: "prompts/list",
		});
		const names = (
			json as { result: { prompts: Array<{ name: string }> } }
		).result.prompts.map((p) => p.name);
		expect(names).toContain("score_my_agent");
	});

	it("gets the score_my_agent prompt and mentions OCS", async () => {
		const { json } = await rpc({
			jsonrpc: "2.0",
			id: 10,
			method: "prompts/get",
			params: { name: "score_my_agent", arguments: {} },
		});
		const text = (
			json as {
				result: {
					messages: Array<{ content: { text: string } }>;
				};
			}
		).result.messages[0].content.text;
		expect(text).toContain("OCS");
		expect(text.toLowerCase()).toContain("score_my_agent.py");
		expect(text).toContain("Do not compare a new score");
		expect(text).not.toContain("compare your score against published");
	});
});
