import { execFileSync } from "node:child_process";
import {
	cpSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	rmSync,
	symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const REPO_ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const MCP_ROOT = join(REPO_ROOT, "mcp");
const temporaryRoots = [];

afterEach(() => {
	for (const path of temporaryRoots.splice(0)) {
		rmSync(path, { recursive: true, force: true });
	}
});

describe("MCP scripts in spaced checkout paths", () => {
	it("builds corpus, well-known data, and probes from a path containing spaces", () => {
		const checkout = mkdtempSync(join(tmpdir(), "operant spaced checkout "));
		temporaryRoots.push(checkout);
		const mcp = join(checkout, "mcp");
		cpSync(join(MCP_ROOT, "scripts"), join(mcp, "scripts"), { recursive: true });
		cpSync(join(MCP_ROOT, ".well-known"), join(mcp, ".well-known"), {
			recursive: true,
		});
		cpSync(join(MCP_ROOT, "package.json"), join(mcp, "package.json"));
		cpSync(join(MCP_ROOT, "src"), join(mcp, "src"), { recursive: true });
		symlinkSync(join(MCP_ROOT, "node_modules"), join(mcp, "node_modules"), "dir");
		for (const path of [
			"lab/public/calibration-profiles.json",
			"operant_cases.json",
			"operant_axis2_cases.json",
			"operant_axis3_cases.json",
			"operant_axis4_cases.json",
		]) {
			mkdirSync(join(checkout, path, ".."), { recursive: true });
			cpSync(join(REPO_ROOT, path), join(checkout, path), { recursive: true });
		}

		execFileSync(process.execPath, [join(mcp, "scripts/build-corpus.mjs")], {
			cwd: checkout,
			stdio: "pipe",
		});
		execFileSync(process.execPath, [join(mcp, "scripts/build-wellknown.mjs")], {
			cwd: mcp,
			stdio: "pipe",
		});
		execFileSync("npm", ["--prefix", mcp, "run", "build:cli"], {
			cwd: checkout,
			stdio: "pipe",
		});
		const probe = execFileSync(process.execPath, [join(mcp, "scripts/probe-mcp.mjs")], {
			cwd: mcp,
			encoding: "utf8",
		});

		expect(existsSync(join(mcp, "src/corpus.generated.ts"))).toBe(true);
		expect(existsSync(join(mcp, "src/well-known.generated.ts"))).toBe(true);
		expect(probe).toContain("Probe PASSED.");
	});
});
