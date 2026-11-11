import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import { afterEach, test } from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const SCRIPT = fileURLToPath(new URL("./verify-manifest.mjs", import.meta.url));
const temporaryRoots = [];

afterEach(() => {
	for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function fixture() {
	const root = mkdtempSync(join(tmpdir(), "operant-verify-pin-"));
	temporaryRoots.push(root);
	const bin = join(root, "bin");
	const pin = join(root, "publisher-pin.pub");
	const manifest = join(root, "manifest.json");
	const log = join(root, "verify-args.json");
	mkdirSync(bin);
	writeFileSync(pin, "synthetic public pin\n");
	writeFileSync(manifest, "{}\n");
	const cli = join(bin, "mcp-kit-sign");
	writeFileSync(cli, `#!/usr/bin/env node
const fs = require("node:fs");
fs.writeFileSync(process.env.MOCK_VERIFY_LOG, JSON.stringify(process.argv.slice(2)));
process.stdout.write(process.env.MOCK_VERIFY_OUTPUT || JSON.stringify({ tool: "mcp-kit-sign", command: "verify-pinned", policy: "verify-independent-publisher-pin-v1", status: "verified" }));
process.exit(Number(process.env.MOCK_VERIFY_STATUS || 0));
`);
	chmodSync(cli, 0o755);
	return { bin, pin, manifest, log };
}

function run(p, options = {}) {
	const env = { ...process.env, PATH: `${p.bin}${delimiter}${process.env.PATH}`, MOCK_VERIFY_LOG: p.log };
	const pin = Object.hasOwn(options, "pin") ? options.pin : p.pin;
	if (!pin) delete env.MCP_TRUSTED_PUBLISHER_PUB;
	else env.MCP_TRUSTED_PUBLISHER_PUB = pin;
	if (options.output !== undefined) env.MOCK_VERIFY_OUTPUT = options.output;
	if (options.status !== undefined) env.MOCK_VERIFY_STATUS = String(options.status);
	return spawnSync(process.execPath, [SCRIPT, p.manifest], { cwd: REPO_ROOT, env, encoding: "utf8" });
}

test("wrapper passes the manifest and configured pin to the strict verifier", () => {
	const p = fixture();
	const result = run(p);
	assert.equal(result.status, 0, result.stderr);
	assert.match(result.stdout, /independent publisher pin verified/);
	assert.deepEqual(JSON.parse(readFileSync(p.log, "utf8")), [
		"verify-pinned", `--manifest=${p.manifest}`, `--trusted-pub=${p.pin}`,
	]);
});

test("wrapper fails closed before invoking the CLI when the pin is missing", () => {
	const p = fixture();
	const result = run(p, { pin: null });
	assert.equal(result.status, 2);
	assert.match(result.stderr, /MCP_TRUSTED_PUBLISHER_PUB must be set/);
	assert.equal(existsSync(p.log), false);
});

test("wrapper rejects legacy usage text even when an old CLI exits successfully", () => {
	const p = fixture();
	const result = run(p, { output: "Usage: mcp-kit-sign verify --manifest=PATH\\n" });
	assert.equal(result.status, 2);
	assert.match(result.stderr, /no policy-bound verification document/);
	assert.equal(existsSync(p.log), true);
});

test("wrapper preserves invalid publisher-pin results", () => {
	const p = fixture();
	const result = run(p, {
		output: JSON.stringify({ tool: "mcp-kit-sign", command: "verify-pinned", policy: "verify-independent-publisher-pin-v1", status: "invalid" }),
		status: 1,
	});
	assert.equal(result.status, 1);
	assert.match(result.stdout, /signature INVALID/);
});

test("wrapper rejects an unavailable publisher-pin path", () => {
	const p = fixture();
	const result = run(p, { pin: join(p.bin, "missing.pub") });
	assert.equal(result.status, 2);
	assert.match(result.stderr, /publisher pin file is unavailable/);
	assert.equal(existsSync(p.log), false);
});
