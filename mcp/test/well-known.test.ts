// The Worker also serves operant.saagarpatel.dev's signed discovery manifest at the
// well-known paths (no static host exists for this origin). These tests verify the
// routes, that the served manifest does NOT drift from the live tool surface, and
// that the embedded detached signature actually verifies against the embedded public
// key — so a forgotten re-sign (or a hand-edit of the generated file) fails CI.
//
// Crypto uses Web Crypto (crypto.subtle + Ed25519), the same API the Worker runtime
// provides, so the check exercises the real verification path with no Node-only deps.

import { describe, expect, it } from "vitest";
import handler from "../src/index";
import {
	MANIFEST_JSON,
	MANIFEST_PUBKEY,
	MANIFEST_SIG,
} from "../src/well-known.generated";

const get = (path: string, method = "GET") =>
	handler.fetch(new Request(`http://localhost${path}`, { method }));

function base64ToBytes(b64: string): Uint8Array {
	const bin = atob(b64.trim());
	const out = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
	return out;
}

function pemToDer(pem: string): Uint8Array {
	const body = pem
		.replace(/-----BEGIN [^-]+-----/, "")
		.replace(/-----END [^-]+-----/, "")
		.replace(/\s+/g, "");
	return base64ToBytes(body);
}

async function toolsList(): Promise<string[]> {
	const res = await handler.fetch(
		new Request("http://localhost/mcp", {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json, text/event-stream",
			},
			body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
		}),
	);
	const json = JSON.parse(await res.text()) as {
		result: { tools: Array<{ name: string }> };
	};
	return json.result.tools.map((t) => t.name).sort();
}

describe("well-known discovery manifest", () => {
	it("serves the manifest as JSON with the live server contract", async () => {
		const res = await get("/.well-known/mcp.json");
		expect(res.status).toBe(200);
		expect(res.headers.get("Content-Type")).toContain("application/json");
		expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
		const m = JSON.parse(await res.text());
		expect(m.name).toBe("operant-mcp");
		expect(m.registry).toBe("io.github.saagpatel/operant-mcp");
		expect(m.mcp_server.endpoint).toBe("https://operant.saagarpatel.dev/mcp");
		expect(m.mcp_server.transport).toBe("streamable-http");
	});

	it("does not drift from the server's live tool surface", async () => {
		const advertised = (JSON.parse(MANIFEST_JSON).mcp_server.tools as string[])
			.slice()
			.sort();
		expect(advertised).toEqual(await toolsList());
	});

	it("serves the detached signature and public key", async () => {
		const sig = await get("/.well-known/mcp.json.sig");
		expect(sig.status).toBe(200);
		expect(sig.headers.get("Content-Type")).toContain("text/plain");

		const pub = await get("/.well-known/mcp-ed25519.pub");
		expect(pub.status).toBe(200);
		expect(await pub.text()).toContain("PUBLIC KEY");
	});

	it("answers HEAD with no body and rejects non-GET with 405", async () => {
		const head = await get("/.well-known/mcp.json", "HEAD");
		expect(head.status).toBe(200);
		expect(await head.text()).toBe("");

		const post = await get("/.well-known/mcp.json", "POST");
		expect(post.status).toBe(405);
	});

	it("the embedded signature verifies against the embedded public key", async () => {
		const key = await crypto.subtle.importKey(
			"spki",
			pemToDer(MANIFEST_PUBKEY),
			{ name: "Ed25519" },
			false,
			["verify"],
		);
		const ok = await crypto.subtle.verify(
			"Ed25519",
			key,
			base64ToBytes(MANIFEST_SIG),
			new TextEncoder().encode(MANIFEST_JSON),
		);
		expect(ok).toBe(true);
	});

	it("still 404s unknown paths with a discovery hint", async () => {
		const res = await get("/nope");
		expect(res.status).toBe(404);
		const body = (await res.json()) as { discovery?: string };
		expect(body.discovery).toBe("/.well-known/mcp.json");
	});
});
