#!/usr/bin/env node
// Ed25519 signing for the operant-mcp discovery manifest (.well-known/mcp.json), so
// an agent or registry can verify it authentically comes from Saagar. Zero deps
// (Node built-in crypto). Detached signature + published public key — the same
// scheme as the saagarpatel.dev portfolio manifest, so a verifier checks both the
// same way.
//
//   node scripts/sign-manifest.mjs gen-key   # one-time: create the keypair
//   node scripts/sign-manifest.mjs sign      # sign the manifest -> .sig
//   node scripts/sign-manifest.mjs verify    # verify manifest against .sig + pubkey
//
// The PRIVATE key lands in .signing/ (gitignored) — NEVER commit it. The PUBLIC key
// (mcp-ed25519.pub) and detached signature (mcp.json.sig) sit next to the manifest and
// are baked into the Worker by scripts/build-wellknown.mjs so the live endpoint serves
// all three. Defaults target this package's .well-known/; override with
// --manifest= / --key= / --pub= / --sig=.
//
// The manifest is signed as raw bytes (no canonicalization), so the Worker must serve
// the exact bytes that were signed. Re-run `sign` then `build:wellknown` after any
// manifest change.

import {
	createPrivateKey,
	createPublicKey,
	generateKeyPairSync,
	sign as edSign,
	verify as edVerify,
} from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const PKG_DIR = resolve(new URL("..", import.meta.url).pathname);
const args = process.argv.slice(2);
const cmd = args.find((a) => !a.startsWith("--")) ?? "verify";
const flag = (name, def) => {
	const pre = `--${name}=`;
	return args.find((a) => a.startsWith(pre))?.slice(pre.length) ?? def;
};

const MANIFEST = resolve(PKG_DIR, flag("manifest", ".well-known/mcp.json"));
const KEY = resolve(PKG_DIR, flag("key", ".signing/operant-ed25519.key"));
const PUB = resolve(PKG_DIR, flag("pub", ".well-known/mcp-ed25519.pub"));
const SIG = resolve(PKG_DIR, flag("sig", ".well-known/mcp.json.sig"));

function genKey() {
	if (existsSync(KEY)) {
		console.error(`refusing to overwrite existing private key: ${KEY}`);
		process.exit(1);
	}
	const { publicKey, privateKey } = generateKeyPairSync("ed25519");
	mkdirSync(dirname(KEY), { recursive: true });
	writeFileSync(KEY, privateKey.export({ type: "pkcs8", format: "pem" }));
	chmodSync(KEY, 0o600);
	mkdirSync(dirname(PUB), { recursive: true });
	writeFileSync(PUB, publicKey.export({ type: "spki", format: "pem" }));
	console.log(`private key -> ${KEY}  (chmod 600 — NEVER commit)`);
	console.log(`public key  -> ${PUB}  (baked into the Worker, served publicly)`);
}

function signManifest() {
	const priv = createPrivateKey(readFileSync(KEY));
	const signature = edSign(null, readFileSync(MANIFEST), priv); // Ed25519: algorithm = null
	writeFileSync(SIG, `${signature.toString("base64")}\n`);
	if (!existsSync(PUB)) {
		writeFileSync(PUB, createPublicKey(priv).export({ type: "spki", format: "pem" }));
	}
	console.log(`signed ${MANIFEST}`);
	console.log(`  -> ${SIG}  (base64 Ed25519, detached)`);
	console.log(`  -> ${PUB}  (public key)`);
}

function verifyManifest() {
	const pub = createPublicKey(readFileSync(PUB));
	const signature = Buffer.from(readFileSync(SIG, "utf8").trim(), "base64");
	const ok = edVerify(null, readFileSync(MANIFEST), pub, signature);
	console.log(ok ? "signature VALID" : "signature INVALID");
	process.exit(ok ? 0 : 1);
}

const commands = { "gen-key": genKey, sign: signManifest, verify: verifyManifest };
const run = commands[cmd];
if (!run) {
	console.error(`unknown command "${cmd}" — use: gen-key | sign | verify`);
	process.exit(1);
}
run();
