import assert from "node:assert/strict";
import { test } from "node:test";

import { rowMatchesPredicate } from "../lib/rows.mjs";
import {
  canonicalJSON,
  parseJson,
  sha256Hex,
  verifyReceipt,
} from "../lib/verify.mjs";

const PAYLOAD_TYPE = "application/vnd.in-toto+json";
const STATEMENT_TYPE = "https://in-toto.io/Statement/v1";
const PREDICATE_TYPE = "https://operant.saagarpatel.dev/sitting-receipt/v1";
const SITTING_ID = "sit_abcdefghijklmnopqrstuvwxyz";

// Independent DSSE PAE (DSSEv1 len(type) type len(payload) payload), not imported
// from the verifier, so a broken PAE in the library fails the signature tests.
function dssePae(payloadType, payload) {
  const enc = new TextEncoder();
  const head = enc.encode(`DSSEv1 ${enc.encode(payloadType).length} ${payloadType} ${payload.length} `);
  const out = new Uint8Array(head.length + payload.length);
  out.set(head, 0);
  out.set(payload, head.length);
  return out;
}

function bytesToB64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function toPem(spki) {
  const body = bytesToB64(spki).match(/.{1,64}/g).join("\n");
  return `-----BEGIN PUBLIC KEY-----\n${body}\n-----END PUBLIC KEY-----\n`;
}

function finding(result, field) {
  const hits = result.findings.filter((item) => item.field === field);
  assert.equal(hits.length, 1, `expected one ${field} finding, saw ${hits.map((item) => item.field).join(",") || "none"}`);
  return hits[0];
}

function blobFetcher(map) {
  return async (sha) => {
    const bytes = map.get(sha);
    return bytes ? new Uint8Array(bytes) : undefined;
  };
}

async function signStatement(statement, privateKey, keyid) {
  const payloadText = canonicalJSON(statement);
  const payloadBytes = new TextEncoder().encode(payloadText);
  const sig = new Uint8Array(
    await crypto.subtle.sign({ name: "Ed25519" }, privateKey, dssePae(PAYLOAD_TYPE, payloadBytes)),
  );
  return {
    receipt_format: "operant.sitting-receipt/v1",
    keyid,
    envelope: {
      payloadType: PAYLOAD_TYPE,
      payload: bytesToB64(payloadBytes),
      signatures: [{ keyid, sig: bytesToB64(sig) }],
    },
  };
}

async function buildWorld() {
  const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const spki = new Uint8Array(await crypto.subtle.exportKey("spki", publicKey));
  const keyid = "test-key";
  const pubkeys = {
    format: "operant.sittings-pubkey/v1",
    keys: [{
      keyid,
      alg: "ed25519",
      spki_der_b64: bytesToB64(spki),
      public_key_pem: toPem(spki),
    }],
  };
  const pemOnly = {
    format: "operant.sittings-pubkey/v1",
    keys: [{ keyid, alg: "ed25519", public_key_pem: toPem(spki) }],
  };

  const corpusA = new TextEncoder().encode("alpha-café\n");
  const corpusB = new TextEncoder().encode("beta\n");
  const scorerA = new TextEncoder().encode("score-me\n");
  const hashA = await sha256Hex(corpusA);
  const hashB = await sha256Hex(corpusB);
  const hashS = await sha256Hex(scorerA);
  const corpusFiles = [
    { path: "beta.txt", sha256: hashB, bytes: corpusB.length },
    { path: "alpha.txt", sha256: hashA, bytes: corpusA.length },
  ];
  const scorerFiles = [{ path: "score.txt", sha256: hashS, bytes: scorerA.length }];
  async function aggregate(files) {
    const pairs = files.map(({ path, sha256 }) => ({ path, sha256 })).sort((a, b) => (a.path < b.path ? -1 : 1));
    return sha256Hex(new TextEncoder().encode(canonicalJSON(pairs)));
  }
  const corpus = { sha256: await aggregate(corpusFiles), files: corpusFiles };
  const scorer = { sha256: await aggregate(scorerFiles), files: scorerFiles };
  const evidence = { calls: [] };
  const evidenceSha = await sha256Hex(new TextEncoder().encode(canonicalJSON(evidence)));
  const modelBinding = {
    format: "operant.model-binding/v1",
    level: "unbound",
    claimed_model: "example-model",
    observed_model: null,
    binds: ["No upstream call was observed."],
    does_not_bind: [
      "Does not establish which model produced the answers.",
      "Does not establish that the submitter setup matches the claim.",
    ],
    evidence,
    evidence_sha256: evidenceSha,
  };
  const answers = [{
    case_id: "case-1",
    prompt_sha256: "11".repeat(32),
    answer: "hold",
    received_at: "2026-11-08T20:01:00Z",
    relay_call_ids: [],
  }];
  const score = {
    ocs: 0.5,
    tpr: 0.25,
    fpr: 0.125,
    n_scored: 1,
    n_cases: 2,
    n_unanswered: 1,
    band: "mid",
  };
  const record = {
    format: "operant.sitting/v1",
    sitting_id: SITTING_ID,
    created_at: "2026-11-08T20:00:00Z",
    finished_at: "2026-11-08T20:20:00Z",
    agent_label: "north-agent",
    claimed_model: "example-model",
    corpus,
    scorer,
    case_order: ["case-1"],
    answers,
    score,
    model_binding: modelBinding,
  };
  const answersSha = await sha256Hex(new TextEncoder().encode(canonicalJSON(answers)));
  const recordSha = await sha256Hex(new TextEncoder().encode(canonicalJSON(record)));
  const predicate = {
    receipt_format: "operant.sitting-receipt/v1",
    sitting_id: SITTING_ID,
    agent_label: "north-agent",
    claimed_model: "example-model",
    sealed_at: "2026-11-08T20:20:00Z",
    corpus,
    scorer,
    answers_sha256: answersSha,
    score,
    model_binding: modelBinding,
    seal_binds: [
      "corpus bytes",
      "scorer bytes",
      "the answers",
      "the score recomputed from them",
      "the model binding at its stated level",
    ],
    seal_does_not_bind: ["identity of the submitter"],
  };
  const statement = {
    _type: STATEMENT_TYPE,
    subject: [{ name: `operant-sitting:${SITTING_ID}`, digest: { sha256: recordSha } }],
    predicateType: PREDICATE_TYPE,
    predicate,
  };
  const receipt = await signStatement(statement, privateKey, keyid);
  const blobs = new Map([
    [hashA, corpusA],
    [hashB, corpusB],
    [hashS, scorerA],
  ]);
  return { receipt, record, pubkeys, pemOnly, blobs, privateKey, keyid, statement, hashA, hashS };
}

function optionsFor(world, extra = {}) {
  return {
    pubkeys: world.pubkeys,
    fetchBlob: blobFetcher(world.blobs),
    record: world.record,
    ...extra,
  };
}

test("DSSE PAE prefix matches the spec spelling", () => {
  const payload = new TextEncoder().encode("{}");
  const out = dssePae(PAYLOAD_TYPE, payload);
  const prefix = new TextDecoder().decode(out.slice(0, out.length - payload.length));
  assert.equal(prefix, "DSSEv1 28 application/vnd.in-toto+json 2 ");
  assert.equal(new TextDecoder().decode(out.slice(-2)), "{}");
});

test("canonicalJSON matches Python json.dumps goldens", async () => {
  assert.equal(
    canonicalJSON({ b: [1, { z: true, a: null }], a: "café" }),
    '{"a":"café","b":[1,{"a":null,"z":true}]}',
  );
  assert.equal(
    canonicalJSON(["b", { z: 1, a: [true, null, "ü"] }]),
    '["b",{"a":[true,null,"ü"],"z":1}]',
  );
  assert.equal(
    canonicalJSON({ s: "Δέλτα 😀", arr: ["x", "y", { p: 1 }] }),
    '{"arr":["x","y",{"p":1}],"s":"Δέλτα 😀"}',
  );
  assert.equal(
    canonicalJSON({ k: -2, msg: "line\nquote\"slash\\end", n: 0.5 }),
    '{"k":-2,"msg":"line\\nquote\\"slash\\\\end","n":0.5}',
  );
  // Python leaves U+2028 raw under ensure_ascii=False. Canonical form does too.
  assert.equal(canonicalJSON("\u2028"), '"\u2028"');
  assert.equal(
    canonicalJSON(parseJson('{"n":0,"ocs":0.0,"t":1.0}')),
    '{"n":0,"ocs":0.0,"t":1.0}',
  );
  assert.equal(canonicalJSON(parseJson("-0.0")), "-0.0");
  assert.equal(canonicalJSON(parseJson("-0")), "0");
  assert.equal(
    canonicalJSON(parseJson('{\n  "b": [1, {"z": true, "a": null}],\n  "a": "café"\n}\n')),
    '{"a":"café","b":[1,{"a":null,"z":true}]}',
  );
  assert.equal(
    await sha256Hex(new TextEncoder().encode(canonicalJSON("café"))),
    "28380feb8724d669bc8d4cf5b5a5bb1adbdc61b81ebd06f3fabc567b4f3b0fc5",
  );
  assert.equal(
    await sha256Hex(new TextEncoder().encode(canonicalJSON({ b: [1, { z: true, a: null }], a: "café" }))),
    "85f065fa4945644879be14bcc20e6261bac57877344bfafd092a2e8c7fa7df82",
  );
});

test("a valid receipt verifies", async () => {
  const world = await buildWorld();
  const result = await verifyReceipt(world.receipt, optionsFor(world));
  assert.equal(result.ok, true, result.findings.filter((item) => !item.ok).map((item) => `${item.field}: ${item.detail}`).join("\n"));
  const level = finding(result, "model_binding.level");
  assert.match(level.detail, /binding level: unbound/);
  assert.doesNotMatch(level.detail, /verified/i);
  const binds = finding(result, "model_binding.binds");
  assert.equal(binds.detail, "No upstream call was observed.");
  assert.match(finding(result, "not_checked").detail, /uv run python -m sittings\.receipt verify/);
  const pemResult = await verifyReceipt(world.receipt, optionsFor(world, { pubkeys: world.pemOnly }));
  assert.equal(pemResult.ok, true);
});

test("an altered payload byte fails the signature", async () => {
  const world = await buildWorld();
  const payload = b64ToBytes(world.receipt.envelope.payload);
  const at = new TextDecoder().decode(payload).indexOf("north-agent");
  assert.ok(at > 0);
  payload[at] = "N".charCodeAt(0);
  const receipt = {
    ...world.receipt,
    envelope: { ...world.receipt.envelope, payload: bytesToB64(payload) },
  };
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "signature").ok, false);
  assert.equal(finding(result, "keyid").ok, true);
  assert.equal(finding(result, "payload").ok, true);
});

test("an altered signature fails the signature", async () => {
  const world = await buildWorld();
  const sig = b64ToBytes(world.receipt.envelope.signatures[0].sig);
  sig[0] ^= 0xff;
  const receipt = {
    ...world.receipt,
    envelope: {
      ...world.receipt.envelope,
      signatures: [{ keyid: world.keyid, sig: bytesToB64(sig) }],
    },
  };
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "signature").ok, false);
  assert.equal(finding(result, "payload").ok, true);
  assert.equal(finding(result, "corpus").ok, true);
});

test("an altered corpus blob fails corpus", async () => {
  const world = await buildWorld();
  const blobs = new Map(world.blobs);
  const tampered = new Uint8Array(blobs.get(world.hashA));
  tampered[0] ^= 0xff;
  blobs.set(world.hashA, tampered);
  const result = await verifyReceipt(world.receipt, optionsFor(world, { fetchBlob: blobFetcher(blobs) }));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "corpus").ok, false);
  assert.match(finding(result, "corpus").detail, /alpha\.txt/);
  assert.equal(finding(result, "scorer").ok, true);
  assert.equal(finding(result, "signature").ok, true);
});

test("an altered scorer blob fails scorer", async () => {
  const world = await buildWorld();
  const blobs = new Map(world.blobs);
  const tampered = new Uint8Array(blobs.get(world.hashS));
  tampered[0] ^= 0xff;
  blobs.set(world.hashS, tampered);
  const result = await verifyReceipt(world.receipt, optionsFor(world, { fetchBlob: blobFetcher(blobs) }));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "scorer").ok, false);
  assert.match(finding(result, "scorer").detail, /score\.txt/);
  assert.equal(finding(result, "corpus").ok, true);
  assert.equal(finding(result, "signature").ok, true);
});

test("an altered record answer fails answers", async () => {
  const world = await buildWorld();
  const record = parseJson(canonicalJSON(world.record));
  record.answers[0].answer = `${record.answers[0].answer}.`;
  const result = await verifyReceipt(world.receipt, optionsFor(world, { record }));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "answers").ok, false);
  assert.equal(finding(result, "record").ok, false);
  assert.equal(finding(result, "signature").ok, true);
});

test("altered binding evidence fails model_binding.evidence", async () => {
  const world = await buildWorld();
  const statement = parseJson(canonicalJSON(world.statement));
  statement.predicate.model_binding.evidence = { note: "tampered" };
  const receipt = await signStatement(statement, world.privateKey, world.keyid);
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "model_binding.evidence").ok, false);
  assert.equal(finding(result, "signature").ok, true);
  assert.equal(finding(result, "model_binding.level").ok, true);
  assert.match(finding(result, "model_binding.binds").detail, /No upstream call was observed\./);
});

test("an unknown receipt format is refused", async () => {
  const world = await buildWorld();
  const receipt = { ...world.receipt, receipt_format: "operant.sitting-receipt/v0" };
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "receipt_format").ok, false);
  assert.equal(finding(result, "signature").ok, true);
});

test("an untrusted key is refused", async () => {
  const world = await buildWorld();
  const other = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const spki = new Uint8Array(await crypto.subtle.exportKey("spki", other.publicKey));
  const pubkeys = {
    format: "operant.sittings-pubkey/v1",
    keys: [{ keyid: "other-key", alg: "ed25519", spki_der_b64: bytesToB64(spki), public_key_pem: toPem(spki) }],
  };
  const result = await verifyReceipt(world.receipt, optionsFor(world, { pubkeys }));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "keyid").ok, false);
  assert.match(finding(result, "keyid").detail, /not in the trusted set/);
});

test("the table refuses a row whose displayed ocs differs from the predicate", () => {
  const predicate = {
    sitting_id: SITTING_ID,
    agent_label: "north-agent",
    claimed_model: "example-model",
    sealed_at: "2026-11-08T20:20:00Z",
    score: { ocs: 0.5, tpr: 0.25, fpr: 0.125, band: "mid" },
    model_binding: { level: "unbound", binds: ["No upstream call was observed."] },
  };
  const row = {
    sitting_id: SITTING_ID,
    agent_label: "north-agent",
    claimed_model: "example-model",
    ocs: 0.5,
    tpr: 0.25,
    fpr: 0.125,
    band: "mid",
    binding_level: "unbound",
    sealed_at: "2026-11-08T20:20:00Z",
  };
  assert.equal(rowMatchesPredicate(row, predicate), true);
  assert.equal(rowMatchesPredicate({ ...row, ocs: 0.25 }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, agent_label: "other-agent" }, predicate), false);
  const floatScore = parseJson('{"ocs":0.0,"tpr":0.0,"fpr":0.0,"band":"mid"}');
  const floatPredicate = { ...predicate, score: floatScore };
  assert.equal(rowMatchesPredicate({ ...row, ocs: 0, tpr: 0, fpr: 0 }, floatPredicate), true);
  assert.equal(rowMatchesPredicate({ ...row, ocs: 0.1, tpr: 0, fpr: 0 }, floatPredicate), false);
});
