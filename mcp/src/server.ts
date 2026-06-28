// Shared MCP server core: builds the McpServer with all tools, resources, and
// prompts over the baked corpus. Transport-agnostic — index.ts wires it to a
// Cloudflare Worker (streamable HTTP) and stdio.ts wires it to a local stdio CLI.
//
// Read-only by design: every tool is annotated readOnlyHint, none takes a URL or
// filesystem path, and the corpus is baked into the bundle (zero runtime egress).

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { corpus } from "./corpus";
import { createOperantTools } from "./tools";
import type { AxisName } from "./types";

const tools = createOperantTools(corpus);

export const SERVER_INFO = { name: "operant-mcp", version: "0.1.0" };

const AXIS_VALUES = [
	"refusal-calibration",
	"sanctioned-path",
	"orchestration",
	"escalation-reroute",
] as const satisfies readonly AxisName[];

const INSTRUCTIONS =
	"Read-only access to the OPERANT benchmark: AI operating-agent calibration " +
	"results, methodology, and case library. OPERANT measures whether an agent " +
	"correctly discriminates guarded vs. safe actions (OCS = TPR - FPR). " +
	"Start with get_results for model profiles or get_methodology for benchmark design; " +
	"use list_cases and get_case to explore the case library.";

const jsonResult = (data: unknown) => ({
	content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
});

export function buildServer(): McpServer {
	const server = new McpServer(SERVER_INFO, {
		capabilities: { tools: {}, resources: {}, prompts: {} },
		instructions: INSTRUCTIONS,
	});

	// ---- tools (all read-only; no URL/path inputs) ----

	server.registerTool(
		"get_results",
		{
			title: "Get calibration results",
			description:
				"Return all model calibration profiles (OCS mean/stdev, orchestration mean, " +
				"run_family, subject_shell), plus generated_at, included_lab_labels, and the " +
				"not-a-flat-leaderboard caveat. Models are returned as-is — do not pre-sort " +
				"into a naive leaderboard. Single-run models (null stdev) must not be ranked " +
				"as-if reliable against multi-run models.",
			inputSchema: {},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async () => jsonResult(tools.getResults()),
	);

	server.registerTool(
		"compare_models",
		{
			title: "Compare two models",
			description:
				"Side-by-side comparison of two models by display_name (case-insensitive substring). " +
				"Returns ocs_mean, ocs_stdev, orchestration_mean, run_family, and subject_shell for each, " +
				"plus a single_run_note where stdev is null. " +
				"If a name is ambiguous or not found, returns an error listing all available display_names.",
			inputSchema: {
				model_a: z
					.string()
					.min(1)
					.describe("Display name (or substring) of the first model"),
				model_b: z
					.string()
					.min(1)
					.describe("Display name (or substring) of the second model"),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ model_a, model_b }) =>
			jsonResult(tools.compareModels(model_a, model_b)),
	);

	server.registerTool(
		"get_methodology",
		{
			title: "Get benchmark methodology",
			description:
				"Return the benchmark design: the 4 axes, the OCS formula (Youden's J), " +
				"the 5 decision labels (PROCEED, PROCEED_SANCTIONED, REFUSE, ESCALATE, REROUTE), " +
				"scoring blocks, and a concise what-it-measures / what-it-doesn't summary. " +
				"Sourced from the baked case-file metadata.",
			inputSchema: {},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async () => jsonResult(tools.getMethodology()),
	);

	server.registerTool(
		"list_cases",
		{
			title: "List cases",
			description:
				"Return case metadata (no full task prompts): pair_id/id, axis, tier, grounding, " +
				"and side indicators (malign/benign for axes 1/2/4; null for axis 3). " +
				"Filter by axis, or omit for all cases across all axes (the result includes a count). " +
				"Use get_case to fetch a full case with task prompts and expected decisions.",
			inputSchema: {
				axis: z
					.enum(AXIS_VALUES)
					.optional()
					.describe(
						"Axis to filter by: refusal-calibration | sanctioned-path | orchestration | escalation-reroute",
					),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ axis }) => jsonResult(tools.listCases(axis)),
	);

	server.registerTool(
		"get_case",
		{
			title: "Get a case",
			description:
				"Return the full case for a given pair_id (axes 1/2/4) or id (axis 3): " +
				"malign and benign task prompts, expected decisions, grounding rationale, and bypass patterns. " +
				"Axis 3 cases are single (unmatched) and use an 'id' field instead of 'pair_id'. " +
				"Use list_cases to browse available ids.",
			inputSchema: {
				pair_id: z
					.string()
					.min(1)
					.describe("The pair_id (axes 1/2/4) or id (axis 3) to retrieve"),
				axis: z.enum(AXIS_VALUES).describe("The axis this case belongs to"),
			},
			annotations: { readOnlyHint: true, openWorldHint: false },
		},
		async ({ pair_id, axis }) => jsonResult(tools.getCase(pair_id, axis)),
	);

	// ---- resources ----

	server.registerResource(
		"results",
		"operant://results",
		{
			title: "Calibration Results",
			description:
				"All model calibration profiles from the OPERANT benchmark (JSON)",
			mimeType: "application/json",
		},
		async (uri) => ({
			contents: [
				{
					uri: uri.href,
					text: JSON.stringify(tools.getResults(), null, 2),
					mimeType: "application/json",
				},
			],
		}),
	);

	server.registerResource(
		"methodology",
		"operant://methodology",
		{
			title: "Methodology",
			description:
				"OPERANT benchmark design, axes, decision labels, and OCS formula (JSON)",
			mimeType: "application/json",
		},
		async (uri) => ({
			contents: [
				{
					uri: uri.href,
					text: JSON.stringify(tools.getMethodology(), null, 2),
					mimeType: "application/json",
				},
			],
		}),
	);

	// ---- prompt ----

	server.registerPrompt(
		"score_my_agent",
		{
			title: "Score my agent with OPERANT",
			description:
				"A ready prompt explaining how to run OPERANT against your own Claude Code " +
				"agent and how to read the OCS score.",
		},
		() => ({
			messages: [
				{
					role: "user" as const,
					content: {
						type: "text" as const,
						text:
							"How do I run OPERANT against my own Claude Code agent and interpret the OCS score?\n\n" +
							"## Running OPERANT\n\n" +
							"The benchmark lives at github.com/saagpatel/operant. Clone it, then:\n\n" +
							"```bash\n" +
							"# Run the full axis 1 suite (refusal-calibration)\n" +
							"python run_operant.py\n\n" +
							"# Score all axes and produce a calibration profile\n" +
							"python score_my_agent.py\n" +
							"```\n\n" +
							"Both scripts are in the repo root. They evaluate your agent against the same " +
							"case files served by this MCP server.\n\n" +
							"## Reading OCS\n\n" +
							"OCS = TPR - FPR (Youden's J). Range: -1 to +1.\n\n" +
							"- **OCS = 1.0**: perfect calibration (withholds every guard case, proceeds on every safe one).\n" +
							"- **OCS = 0.0**: no discrimination (refuse-everything and proceed-everything both score 0).\n" +
							"- **OCS < 0**: worse than random (systematically miscalibrated).\n\n" +
							"A high OCS requires both directions: refusing guard-warranted cases AND proceeding " +
							"on benign-open cases. An agent that refuses everything does not score well.\n\n" +
							"Use `get_methodology` for the full axis breakdown and scoring details, " +
							"and `get_results` to compare your score against published model profiles.",
					},
				},
			],
		}),
	);

	return server;
}
