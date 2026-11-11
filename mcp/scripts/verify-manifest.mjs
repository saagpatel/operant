import { statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { isAbsolute, resolve } from "node:path";

function refuse(message) {
	console.error(`manifest verification refused: ${message}`);
	process.exit(2);
}

if (process.argv.length !== 3) refuse("usage: verify-manifest.mjs MANIFEST_PATH");

const manifestPath = resolve(process.argv[2]);
const configuredPin = process.env.MCP_TRUSTED_PUBLISHER_PUB;
if (!configuredPin || !isAbsolute(configuredPin)) {
	refuse("MCP_TRUSTED_PUBLISHER_PUB must be set to an absolute independently authenticated public-key path");
}

const trustedPubPath = resolve(configuredPin);
try {
	if (!statSync(trustedPubPath).isFile()) refuse("the configured publisher pin is not a regular file");
} catch {
	refuse("the configured publisher pin file is unavailable");
}

const result = spawnSync("mcp-kit-sign", [
	"verify-pinned",
	`--manifest=${manifestPath}`,
	`--trusted-pub=${trustedPubPath}`,
], { encoding: "utf8" });
if (result.error) refuse(`could not start mcp-kit-sign: ${result.error.message}`);
let response;
try {
	response = JSON.parse(result.stdout);
} catch {
	refuse("the installed mcp-kit-sign returned no policy-bound verification document");
}
if (
	response?.tool !== "mcp-kit-sign" ||
	response?.command !== "verify-pinned" ||
	response?.policy !== "verify-independent-publisher-pin-v1"
) {
	refuse("the installed mcp-kit-sign did not attest the required independent-pin policy");
}
if (response.status === "verified" && result.status === 0) {
	console.log("signature VALID (independent publisher pin verified)");
	process.exitCode = 0;
} else if (response.status === "invalid" && result.status === 1) {
	console.log("signature INVALID (publisher pin or manifest signature mismatch)");
	process.exitCode = 1;
} else {
	refuse("the installed mcp-kit-sign returned an inconsistent policy result");
}
