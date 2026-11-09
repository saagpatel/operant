import assert from "node:assert/strict";
import { test } from "node:test";

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { DISPLAY_FIELDS, recordIsPublishable, rowAccepted, rowMatchesPredicate } from "../lib/rows.mjs";
import {
  JsonNumber,
  MODEL_BINDING_POLICY,
  canonicalJSON,
  checksealKeyid,
  displayRate,
  parseJson,
  sha256Hex,
  verifyReceipt,
} from "../lib/verify.mjs";

const PAYLOAD_TYPE = "application/vnd.in-toto+json";
const STATEMENT_TYPE = "https://in-toto.io/Statement/v1";
const PREDICATE_TYPE = "https://operant.saagarpatel.dev/sitting-receipt/v1";
const SITTING_ID = "sit_abcdefghijklmnopqrstuvwxyz";
const BINDING_POLICY = JSON.parse(readFileSync(new URL("../../sittings/testdata/model-binding-policy-v1.json", import.meta.url), "utf8"));

test("browser binding policy equals the Python v1 policy export", () => {
  assert.deepEqual(MODEL_BINDING_POLICY, BINDING_POLICY);
});

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

const SEAL_BINDS = [
  "corpus bytes",
  "scorer bytes",
  "the answers",
  "the score recomputed from them",
  "the model binding at its stated level",
];
const SEAL_DOES_NOT_BIND = [
  "the submitter's honesty about their setup beyond the model binding",
  "that the agent was not tuned on the public corpus",
  "anything about the orchestration axis",
  "the identity of the submitter",
  "that the T1 local host signing key was not compromised",
];

async function buildWorld() {
  const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const spki = new Uint8Array(await crypto.subtle.exportKey("spki", publicKey));
  const keyid = await checksealKeyid(spki);
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
    { path: "alpha.txt", sha256: hashA, bytes: corpusA.length },
    { path: "beta.txt", sha256: hashB, bytes: corpusB.length },
  ];
  const scorerFiles = [{ path: "score.txt", sha256: hashS, bytes: scorerA.length }];
  async function aggregate(files) {
    const pairs = files.map(({ path, sha256 }) => ({ path, sha256 })).sort((a, b) => (a.path < b.path ? -1 : 1));
    return sha256Hex(new TextEncoder().encode(canonicalJSON(pairs)));
  }
  const corpus = { sha256: await aggregate(corpusFiles), files: corpusFiles };
  const scorer = { sha256: await aggregate(scorerFiles), files: scorerFiles };
  const evidence = { calls: [], claim_status: "unknown" };
  const evidenceSha = await sha256Hex(new TextEncoder().encode(canonicalJSON(evidence)));
  const modelBinding = {
    format: "operant.model-binding/v1",
    level: "unbound",
    claimed_model: "example-model",
    observed_model: null,
    binds: [...BINDING_POLICY.levels.unbound.binds],
    does_not_bind: [...BINDING_POLICY.levels.unbound.does_not_bind],
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
    ocs: "0.500000",
    tpr: "0.250000",
    fpr: "0.125000",
    accuracy: "0.500000",
    safe_correct_rate: "0.500000",
    n_scored: 1,
    n_cases: 2,
    n_unparseable: 0,
    n_unanswered: 1,
    n_bypass_failures: 0,
    band: "mid",
    publishable: true,
    not_publishable_reasons: [],
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
    seal_binds: SEAL_BINDS,
    seal_does_not_bind: SEAL_DOES_NOT_BIND,
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

test("canonicalJSON matches the shared vectors and rejects non-portable values", async () => {
  const vectors = JSON.parse(readFileSync(new URL("../../sittings/testdata/canonical-vectors.json", import.meta.url), "utf8"));
  assert.equal(vectors.format, "operant.canonical-vectors/v1");
  assert.ok(vectors.vectors.length > 0);
  for (const vector of vectors.vectors) {
    const value = parseJson(vector.input_json_text);
    const encoded = canonicalJSON(value);
    assert.equal(encoded, vector.canonical_text);
    assert.equal(await sha256Hex(new TextEncoder().encode(encoded)), vector.sha256);
  }
  assert.equal(
    canonicalJSON({ b: [1, { z: true, a: null }], a: "café" }),
    '{"a":"café","b":[1,{"a":null,"z":true}]}',
  );
  assert.equal(canonicalJSON("\u2028"), '"\u2028"');
  assert.equal(canonicalJSON(parseJson("-0")), "0");
  assert.equal(canonicalJSON({ max: 9007199254740991, min: -9007199254740991, text: "café" }), '{"max":9007199254740991,"min":-9007199254740991,"text":"café"}');
  assert.throws(() => canonicalJSON({ x: 0.5 }), /floats/);
  assert.throws(() => canonicalJSON({ x: Number.NaN }), /floats/);
  assert.throws(() => canonicalJSON({ x: Number.POSITIVE_INFINITY }), /floats/);
  assert.throws(() => canonicalJSON(parseJson('{"x":1.0}')), /floats/);
  assert.throws(() => canonicalJSON(parseJson('{"x":0.0}')), /floats/);
  assert.throws(() => canonicalJSON(parseJson("-0.0")), /floats/);
  assert.throws(() => canonicalJSON({ "é": "non-ASCII key" }), /ASCII/);
  assert.throws(() => canonicalJSON({ "\ud800": "key" }), /ASCII|surrogate/);
  assert.throws(() => canonicalJSON("\ud800"), /surrogate/);
  assert.throws(() => canonicalJSON("\udc00"), /surrogate/);
  assert.equal(canonicalJSON("😀"), '"😀"');
  assert.throws(() => canonicalJSON({ nested: [0, { float: 2.5 }] }), /floats/);
  assert.throws(() => canonicalJSON({ x: 9007199254740992 }), /safe range/);
  assert.throws(() => canonicalJSON({ x: -9007199254740992 }), /safe range/);
  assert.throws(() => canonicalJSON(parseJson("9007199254740992")), /safe range/);
  assert.throws(() => canonicalJSON(parseJson("-9007199254740992")), /safe range/);
  assert.throws(() => canonicalJSON({ nested: [0, { unsafe: 9007199254740992 }] }), /safe range/);
  assert.equal(displayRate("0.825000"), "0.825");
  assert.equal(displayRate("1.000000"), "1");
  assert.equal(displayRate("0.000000"), "0");
  assert.equal(displayRate("-0.100000"), "-0.1");
  assert.equal(displayRate("0.825"), "0.825");
});

test("a valid receipt verifies", async () => {
  const world = await buildWorld();
  const result = await verifyReceipt(world.receipt, optionsFor(world));
  assert.equal(result.ok, true, result.findings.filter((item) => !item.ok).map((item) => `${item.field}: ${item.detail}`).join("\n"));
  const level = finding(result, "model_binding.level");
  assert.match(level.detail, /binding level: unbound/);
  assert.doesNotMatch(level.detail, /verified/i);
  const binds = finding(result, "model_binding.binds");
  assert.equal(binds.detail, "");
  const skipped = finding(result, "not_checked");
  assert.match(skipped.detail, /not checked in browser/);
  assert.match(skipped.detail, /uv run python -m sittings\.receipt verify --receipt web\/data\/receipts\/sit_abcdefghijklmnopqrstuvwxyz\.receipt\.json --record web\/data\/records\/sit_abcdefghijklmnopqrstuvwxyz\.record\.json --pubkeys web\/data\/sittings-pubkey\.json --blob-root web\/data\/blobs --repo-root \./);
  assert.equal(result.fingerprint, world.keyid);
  assert.match(result.fingerprint, /^ed25519:[0-9a-f]{16}$/);
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
  assert.equal(finding(result, "model_binding.binds").ok, true);
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

test("signature is checked over the raw payload bytes", async () => {
  const world = await buildWorld();
  const payload = b64ToBytes(world.receipt.envelope.payload);
  const text = new TextDecoder().decode(payload);
  const spaced = `{ ${text.slice(1)}`;
  const receipt = {
    ...world.receipt,
    envelope: { ...world.receipt.envelope, payload: bytesToB64(new TextEncoder().encode(spaced)) },
  };
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "signature").ok, false);
  assert.equal(finding(result, "payload").ok, false);
});

test("a wrapper keyid that disagrees is refused while the raw signature still checks", async () => {
  const world = await buildWorld();
  const receipt = { ...world.receipt, keyid: "ed25519:0123456789abcdef" };
  const result = await verifyReceipt(receipt, optionsFor(world));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "keyid").ok, false);
  assert.match(finding(result, "keyid").detail, /disagree/);
  assert.equal(finding(result, "signature").ok, true);
});

test("the table refuses a row whose displayed field differs from the predicate", () => {
  const predicate = {
    sitting_id: SITTING_ID,
    agent_label: "north-agent",
    claimed_model: "example-model",
    sealed_at: "2026-11-08T20:20:00Z",
    score: {
      ocs: "0.500000",
      accuracy: "0.500000",
      n_scored: 1,
      n_cases: 2,
      n_bypass_failures: 0,
      band: "mid",
      publishable: true,
      not_publishable_reasons: [],
    },
    model_binding: { level: "unbound", observed_model: null, binds: [] },
  };
  const row = {
    sitting_id: SITTING_ID,
    agent_label: "north-agent",
    claimed_model: "example-model",
    observed_model: null,
    ocs: "0.500000",
    accuracy: "0.500000",
    n_scored: 1,
    n_cases: 2,
    n_bypass_failures: 0,
    band: "mid",
    binding_level: "unbound",
    sealed_at: "2026-11-08T20:20:00Z",
  };
  const record = {
    sitting_id: row.sitting_id,
    agent_label: row.agent_label,
    claimed_model: row.claimed_model,
    score: predicate.score,
    model_binding: predicate.model_binding,
  };
  assert.equal(rowMatchesPredicate(row, predicate), true);
  assert.equal(rowAccepted(row, predicate, record), true);
  assert.equal(rowMatchesPredicate({ ...row, ocs: "0.250000" }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, ocs: "0.500" }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, accuracy: "0.250000" }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, n_scored: 0 }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, n_cases: 1 }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, n_bypass_failures: 1 }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, band: "other" }, predicate), false);
  assert.equal(rowMatchesPredicate({ ...row, agent_label: "other-agent" }, predicate), false);
  assert.equal(rowAccepted(row, predicate, { ...record, score: { ...record.score, publishable: false, not_publishable_reasons: ["partial"] } }), false);
  const unsigned = { ...predicate, score: { ...predicate.score, publishable: false, not_publishable_reasons: ["partial"] } };
  assert.equal(rowAccepted(row, unsigned, record), false);
  assert.equal(recordIsPublishable(record), true);
  const floatPredicate = { ...predicate, score: { ...predicate.score, ocs: parseJson("0.0") } };
  assert.equal(rowMatchesPredicate({ ...row, ocs: 0 }, floatPredicate), false);
});

async function resign(world, record) {
  const statement = parseJson(canonicalJSON(world.statement));
  statement.predicate.corpus = record.corpus;
  statement.predicate.scorer = record.scorer;
  statement.predicate.score = record.score;
  statement.predicate.model_binding = record.model_binding;
  statement.predicate.agent_label = record.agent_label;
  statement.predicate.claimed_model = record.claimed_model;
  statement.predicate.sitting_id = record.sitting_id;
  statement.predicate.answers_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(record.answers)));
  statement.subject[0].digest.sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(record)));
  statement.subject[0].name = `operant-sitting:${record.sitting_id}`;
  const receipt = await signStatement(statement, world.privateKey, world.keyid);
  return { ...world, record, statement, receipt };
}

async function relayWorld() {
  const world = await buildWorld();
  const requestBody = new TextEncoder().encode(canonicalJSON({
    model: "example-model", messages: [{ role: "user", content: "prompt" }],
    metadata: { sitting_id: world.record.sitting_id, case_id: "case-1" }, stream: false,
  }));
  const responseBody = new TextEncoder().encode('{"model":"example-model","choices":[{"message":{"content":"hold"}}]}');
  const call = {
    call_id: `call_${"ab".repeat(16)}`,
    case_id: "case-1",
    sitting_id: world.record.sitting_id,
    provider: "example",
    upstream_base_url: "https://example.com",
    requested_model: "example-model",
    completion_text: "hold",
    response_model: "example-model",
    response_header_model: null,
    request_body_b64: bytesToB64(requestBody),
    response_body_b64: bytesToB64(responseBody),
    request_sha256: await sha256Hex(requestBody),
    response_sha256: await sha256Hex(responseBody),
    started_at: "2026-11-08T20:01:00.000001Z",
    finished_at: "2026-11-08T20:01:00.000002Z",
  };
  const evidence = { calls: [call], claim_status: "match" };
  const binding = {
    format: "operant.model-binding/v1",
    level: "host-relayed",
    claimed_model: "example-model",
    observed_model: "example-model",
    binds: [...BINDING_POLICY.levels["host-relayed"].binds],
    does_not_bind: [...BINDING_POLICY.levels["host-relayed"].does_not_bind],
    evidence,
    evidence_sha256: await sha256Hex(new TextEncoder().encode(canonicalJSON(evidence))),
  };
  const record = parseJson(canonicalJSON(world.record));
  record.model_binding = binding;
  record.answers[0].answer = "hold";
  record.answers[0].prompt_sha256 = await sha256Hex(new TextEncoder().encode("prompt"));
  record.answers[0].relay_call_ids = [call.call_id];
  return resign(world, record);
}

test("host-relayed rechecks completion text and evidence hashes", async () => {
  const world = await relayWorld();
  const result = await verifyReceipt(world.receipt, optionsFor(world));
  assert.equal(result.ok, true, result.findings.filter((item) => !item.ok).map((item) => `${item.field}: ${item.detail}`).join("\n"));
  assert.match(finding(result, "model_binding.level").detail, /host-relayed/);
  assert.match(finding(result, "model_binding.relay").detail, /recorded completion text/);
  assert.match(finding(result, "model_binding.relay").detail, /hashes match/);
  assert.equal(finding(result, "model_binding.binds").detail, world.record.model_binding.binds.join("\n"));
  assert.equal(finding(result, "model_binding.does_not_bind").detail, world.record.model_binding.does_not_bind.join("\n"));

  const mismatched = parseJson(canonicalJSON(world.record));
  mismatched.answers[0].answer = "DECISION: REFUSE";
  const resigned = await resign(world, mismatched);
  const answerResult = await verifyReceipt(resigned.receipt, optionsFor(world, { record: resigned.record }));
  assert.equal(answerResult.ok, false);
  assert.equal(finding(answerResult, "model_binding.relay").ok, false);
  assert.match(finding(answerResult, "model_binding.relay").detail, /does not equal the recorded completion text/);
  assert.equal(finding(answerResult, "signature").ok, true);
  assert.equal(finding(answerResult, "answers").ok, true);

  const stale = parseJson(canonicalJSON(world.record));
  stale.model_binding.evidence_sha256 = "ab".repeat(32);
  const staleSigned = await resign(world, stale);
  const staleResult = await verifyReceipt(staleSigned.receipt, optionsFor(world, { record: staleSigned.record }));
  assert.equal(staleResult.ok, false);
  assert.equal(finding(staleResult, "model_binding.evidence").ok, false);
  assert.equal(finding(staleResult, "signature").ok, true);
  assert.equal(finding(staleResult, "model_binding.relay").ok, true);

  const badHash = parseJson(canonicalJSON(world.record));
  badHash.model_binding.evidence.calls[0].response_sha256 = "cd".repeat(32);
  badHash.model_binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(badHash.model_binding.evidence)));
  const badHashSigned = await resign(world, badHash);
  const hashResult = await verifyReceipt(badHashSigned.receipt, optionsFor(world, { record: badHashSigned.record }));
  assert.equal(hashResult.ok, false);
  assert.equal(finding(hashResult, "model_binding.relay").ok, false);
  assert.match(finding(hashResult, "model_binding.relay").detail, /response hash does not match/);
  assert.equal(finding(hashResult, "model_binding.evidence").ok, true);
  assert.equal(finding(hashResult, "signature").ok, true);
});

test("signed replacement character cannot be substituted with an escaped lone surrogate", async () => {
  const world = await buildWorld();
  const record = parseJson(canonicalJSON(world.record));
  record.answers[0].answer = "\ufffd";
  const signed = await resign(world, record);
  const tampered = parseJson(canonicalJSON(record).replace("\ufffd", "\\ud800"));
  const result = await verifyReceipt(signed.receipt, optionsFor(signed, { record: tampered }));
  assert.equal(result.ok, false);
  assert.equal(finding(result, "record").ok, false);
  assert.equal(finding(result, "answers").ok, false);
  assert.equal(finding(result, "signature").ok, true);
});

test("host-relayed response must contain the signed completion and model", async () => {
  const world = await relayWorld();
  const bodies = [
    '{"model":"example-model","choices":[{"message":{"content":"other"}}]}',
    '{"model":"other-model","choices":[{"message":{"content":"hold"}}]}',
    '{"model":"example-model","choices":[{"message":{"content":"hold"}},{"message":{"content":"hold"}}]}',
    '{"model":"example-model","choices":[{"message":{"content":null}}]}',
    '{"model":"example-model","model":"example-model","choices":[{"message":{"content":"hold"}}]}',
  ];
  for (const body of bodies) {
    const record = parseJson(canonicalJSON(world.record));
    const call = record.model_binding.evidence.calls[0];
    const bytes = new TextEncoder().encode(body);
    call.response_body_b64 = bytesToB64(bytes);
    call.response_sha256 = await sha256Hex(bytes);
    record.model_binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(record.model_binding.evidence)));
    const signed = await resign(world, record);
    const result = await verifyReceipt(signed.receipt, optionsFor(signed));
    assert.equal(finding(result, "signature").ok, true);
    assert.equal(finding(result, "model_binding.evidence").ok, true);
    assert.equal(finding(result, "model_binding.relay").ok, false, body);
  }
});

test("host-relayed model and derived evidence must be consistent", async () => {
  const world = await relayWorld();
  const mismatch = parseJson(canonicalJSON(world.record));
  mismatch.claimed_model = "different-claim";
  mismatch.model_binding.claimed_model = mismatch.claimed_model;
  mismatch.model_binding.evidence.claim_status = "mismatch";
  mismatch.model_binding.evidence.calls[0].response_header_model = "example-model";
  mismatch.model_binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(mismatch.model_binding.evidence)));
  const mismatchSigned = await resign(world, mismatch);
  const mismatchResult = await verifyReceipt(mismatchSigned.receipt, optionsFor(mismatchSigned));
  assert.equal(finding(mismatchResult, "model_binding.relay").ok, true);

  const cases = [
    ["mixed models", async (record) => {
      const call = structuredClone(record.model_binding.evidence.calls[0]);
      call.call_id = `call_${"cd".repeat(16)}`;
      call.case_id = "case-2";
      const request = new TextEncoder().encode(canonicalJSON({
        model: call.requested_model, messages: [{ role: "user", content: "prompt" }],
        metadata: { sitting_id: record.sitting_id, case_id: call.case_id }, stream: false,
      }));
      call.request_body_b64 = bytesToB64(request);
      call.request_sha256 = await sha256Hex(request);
      call.response_model = "other-model";
      const body = new TextEncoder().encode('{"model":"other-model","choices":[{"message":{"content":"hold"}}]}');
      call.response_body_b64 = bytesToB64(body);
      call.response_sha256 = await sha256Hex(body);
      record.model_binding.evidence.calls.push(call);
      record.case_order.push(call.case_id);
      record.answers.push({ ...structuredClone(record.answers[0]), case_id: call.case_id, relay_call_ids: [call.call_id] });
    }],
    ["observed model", (record) => { record.model_binding.observed_model = "other-model"; }],
    ["claim status", (record) => { record.model_binding.evidence.claim_status = "mismatch"; }],
    ["header model", (record) => { record.model_binding.evidence.calls[0].response_header_model = "other-model"; }],
    ["empty model", async (record) => {
      const call = record.model_binding.evidence.calls[0];
      call.response_model = "";
      const body = new TextEncoder().encode('{"model":"","choices":[{"message":{"content":"hold"}}]}');
      call.response_body_b64 = bytesToB64(body);
      call.response_sha256 = await sha256Hex(body);
    }],
    ["claimed model", (record) => { record.model_binding.claimed_model = "other-model"; }],
    ["case coverage", (record) => { record.case_order.push("case-2"); }],
    ["prompt hash", (record) => { record.answers[0].prompt_sha256 = "ab".repeat(32); }],
    ["request tuple", (record) => { record.model_binding.evidence.calls[0].provider = ""; }],
  ];
  for (const [label, mutate] of cases) {
    const record = parseJson(canonicalJSON(world.record));
    await mutate(record);
    record.model_binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(record.model_binding.evidence)));
    const signed = await resign(world, record);
    const result = await verifyReceipt(signed.receipt, optionsFor(signed));
    assert.equal(finding(result, "signature").ok, true, label);
    assert.equal(finding(result, "model_binding.evidence").ok, true, label);
    assert.equal(finding(result, "model_binding.relay").ok, false, label);
  }
});

test("signed bindings require exact v1 schema, policy, and derived claims at both levels", async () => {
  const mutations = [
    ["extra binding key", "schema", (b) => { b.extra = true; }],
    ...BINDING_POLICY.binding_fields.map((key) => [`missing ${key}`, "schema", (b) => { delete b[key]; }]),
    ["format", "format", (b) => { b.format = "operant.model-binding/v2"; }],
    ["unknown level", "level", (b) => { b.level = "independent"; }],
    ["array level", "level", (b) => { b.level = ["unbound"]; }],
    ["null level", "level", (b) => { b.level = null; }],
    ["object level", "level", (b) => { b.level = {}; }],
    ["prototype level", "level", (b) => { b.level = "toString"; }],
    ["altered binds", "binds", (b) => { b.binds = ["Independently verified model identity"]; }],
    ["missing limits", "does_not_bind", (b) => { b.does_not_bind = []; }],
    ["reordered limits", "does_not_bind", (b) => { b.does_not_bind.reverse(); }],
    ["null evidence", "evidence.schema", (b) => { b.evidence = null; }],
    ["extra evidence key", "evidence.schema", (b) => { b.evidence.extra = true; }],
    ["missing calls", "evidence.schema", (b) => { delete b.evidence.calls; }],
    ["calls object", "evidence.schema", (b) => { b.evidence.calls = {}; }],
    ["missing status", "evidence.claim_status", (b) => { delete b.evidence.claim_status; }],
    ["wrong status", "evidence.claim_status", (b) => { b.evidence.claim_status = "mismatch"; }],
    ["unknown status", "evidence.claim_status", (b) => { b.evidence.claim_status = "verified"; }],
    ["null status", "evidence.claim_status", (b) => { b.evidence.claim_status = null; }],
    ["invented observed model", "observed_model", (b) => { b.observed_model = "invented"; }],
    ["predicate claim differs", "claimed_model", (b) => { b.claimed_model = "invented"; }],
    ["null claim", "claimed_model", (b) => { b.claimed_model = null; }],
  ];
  for (const world of [await buildWorld(), await relayWorld()]) {
    for (const [label, field, mutate] of mutations) {
      const record = parseJson(canonicalJSON(world.record));
      const binding = record.model_binding;
      mutate(binding);
      // Keep hashes and signatures correct so the named policy check is decisive.
      if (Object.hasOwn(binding, "evidence") && Object.hasOwn(binding, "evidence_sha256")) {
        binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(binding.evidence)));
      }
      const signed = await resign(world, record);
      const result = await verifyReceipt(signed.receipt, optionsFor(signed));
      const context = `${world.record.model_binding.level}: ${label}`;
      assert.equal(finding(result, "signature").ok, true, context);
      assert.equal(finding(result, "record").ok, true, context);
      assert.equal(result.ok, false, context);
      assert.equal(finding(result, `model_binding.${field}`).ok, false, context);
    }
  }
});

function downgrade(record) {
  const b = record.model_binding;
  b.level = "unbound";
  b.binds = [...BINDING_POLICY.levels.unbound.binds];
  b.does_not_bind = [...BINDING_POLICY.levels.unbound.does_not_bind];
}

async function signBindingChange(world, record) {
  record.model_binding.evidence_sha256 = await sha256Hex(new TextEncoder().encode(canonicalJSON(record.model_binding.evidence)));
  const signed = await resign(world, record);
  return verifyReceipt(signed.receipt, optionsFor(signed));
}

test("signed retained calls require valid schema, metadata, context, and bodies at both levels", async () => {
  const world = await relayWorld();
  const mutations = [
    ...BINDING_POLICY.call_fields.map((key) => [`missing ${key}`, (call) => { delete call[key]; }]),
    ["extra key", (call) => { call.extra = true; }],
    ["invalid id", (call) => { call.call_id = "invented"; }],
    ["wrong sitting", (call) => { call.sitting_id = "sit_wrong"; }],
    ["wrong case", (call) => { call.case_id = "case-other"; }],
    ["empty provider", (call) => { call.provider = ""; }],
    ["null requested model", (call) => { call.requested_model = null; }],
    ["numeric response model", (call) => { call.response_model = 1; }],
    ["numeric response header", (call) => { call.response_header_model = 1; }],
    ["bad timestamp", (call) => { call.started_at = "invalid"; }],
    ["impossible date", (call) => { call.started_at = "2026-02-30T00:00:00Z"; }],
    ["impossible time", (call) => { call.started_at = "2026-11-08T25:00:00Z"; }],
    ["missing UTC suffix", (call) => { call.started_at = "2026-11-08T20:01:00"; }],
    ["reversed microseconds", (call) => { call.finished_at = "2026-11-08T20:01:00.000000Z"; }],
    ...["http://public.example/v1", "http://127.1/v1", "https://u:p@example.com/v1", "https://example.com/",
      "https://example.com/other", "https://example.com/v1?key=x", "https://example.com/v1#x",
      "https://example.com:", "https://example.com:65536"].map((url) => [url, (call) => { call.upstream_base_url = url; }]),
    ["base64 whitespace", (call) => { call.request_body_b64 += "\n"; }],
    ["base64 URL alphabet", (call) => { call.response_body_b64 = "____"; }],
    ["body digest mismatch", (call) => { call.response_sha256 = "00".repeat(32); }],
    ["request shape", async (call) => {
      const raw = new TextEncoder().encode('{"messages":[{"content":"prompt"}]}');
      call.request_body_b64 = bytesToB64(raw);
      call.request_sha256 = await sha256Hex(raw);
    }],
    ["response completion", async (call) => {
      const raw = new TextEncoder().encode('{"model":"example-model","choices":[{"message":{"content":"other"}}]}');
      call.response_body_b64 = bytesToB64(raw);
      call.response_sha256 = await sha256Hex(raw);
    }],
    ["duplicate JSON keys", async (call) => {
      const raw = new TextEncoder().encode('{"model":"example-model","model":"example-model","choices":[{"message":{"content":"hold"}}]}');
      call.response_body_b64 = bytesToB64(raw);
      call.response_sha256 = await sha256Hex(raw);
    }],
  ];
  for (const unbound of [false, true]) {
    for (const [label, mutate] of mutations) {
      const record = parseJson(canonicalJSON(world.record));
      if (unbound) downgrade(record);
      await mutate(record.model_binding.evidence.calls[0]);
      const result = await signBindingChange(world, record);
      const context = `${record.model_binding.level}: ${label}`;
      assert.equal(finding(result, "signature").ok, true, context);
      assert.equal(finding(result, "model_binding.evidence_sha256").ok, true, context);
      assert.equal(finding(result, "model_binding.evidence.calls").ok, false, context);
      assert.equal(result.ok, false, context);
    }
  }
});

test("unbound evidence retains calls and derives observations without requiring host linkage", async () => {
  const world = await relayWorld();
  for (const model of ["example-model", "different-model", "", null]) {
    const record = parseJson(canonicalJSON(world.record));
    downgrade(record);
    const binding = record.model_binding;
    const call = binding.evidence.calls[0];
    call.response_model = model;
    call.response_header_model = "contradictory-header";
    const raw = new TextEncoder().encode(JSON.stringify({ model, choices: [{ message: { content: "hold" } }], usage: { cost: 0.125 } }));
    call.response_body_b64 = bytesToB64(raw);
    call.response_sha256 = await sha256Hex(raw);
    record.answers[0].answer = "a different answer causes downgrade";
    record.answers[0].relay_call_ids = [];
    binding.observed_model = model || null;
    binding.evidence.claim_status = !model ? "unknown" : model === record.claimed_model ? "match" : "mismatch";
    const result = await signBindingChange(world, record);
    assert.equal(result.ok, true, result.findings.filter((f) => !f.ok).map((f) => `${f.field}: ${f.detail}`).join("\n"));
    assert.equal(finding(result, "model_binding.observed_model").ok, true);
    assert.equal(finding(result, "model_binding.evidence.claim_status").ok, true);
  }
});

test("unbound observations ignore empty models and become unknown for multiple distinct models", async () => {
  const world = await relayWorld();
  for (const model of [null, "", "example-model", "different-model"]) {
    const record = parseJson(canonicalJSON(world.record));
    downgrade(record);
    const binding = record.model_binding;
    const call = structuredClone(binding.evidence.calls[0]);
    call.call_id = `call_${"cd".repeat(16)}`;
    call.case_id = "case-2";
    call.response_model = model;
    const request = new TextEncoder().encode(canonicalJSON({
      model: call.requested_model, messages: [{ role: "user", content: "prompt" }],
      metadata: { sitting_id: record.sitting_id, case_id: call.case_id }, stream: false,
    }));
    call.request_body_b64 = bytesToB64(request);
    call.request_sha256 = await sha256Hex(request);
    const response = new TextEncoder().encode(JSON.stringify({ model, choices: [{ message: { content: "hold" } }] }));
    call.response_body_b64 = bytesToB64(response);
    call.response_sha256 = await sha256Hex(response);
    binding.evidence.calls.push(call);
    record.case_order.push(call.case_id);
    binding.observed_model = model === "different-model" ? null : "example-model";
    binding.evidence.claim_status = binding.observed_model === null ? "unknown" : "match";
    const result = await signBindingChange(world, record);
    assert.equal(result.ok, true, result.findings.filter((f) => !f.ok).map((f) => `${f.field}: ${f.detail}`).join("\n"));

    for (const duplicate of ["call_id", "case_id"]) {
      const invalid = parseJson(canonicalJSON(record));
      invalid.model_binding.evidence.calls[1][duplicate] = invalid.model_binding.evidence.calls[0][duplicate];
      const duplicateResult = await signBindingChange(world, invalid);
      assert.equal(finding(duplicateResult, "signature").ok, true);
      assert.equal(finding(duplicateResult, "model_binding.evidence.calls").ok, false, duplicate);
    }
  }
});

test("relay timestamps accept Python ISO forms and preserve microsecond ordering", async () => {
  const world = await relayWorld();
  for (const [start, finish] of [
    ["2026-11-08T20:01:00.000001Z", "2026-11-08T20:01:00.000002Z"],
    ["2026-11-08T20:01:00,000001Z", "2026-11-08T20:01:00.0000019Z"],
    ["20261108T200100Z", "2026-11-08 20:01:01Z"],
    ["2026-11-08T20Z", "2026-11-08T20:01Z"],
    ["2026-11-08T20,5Z", "2026-11-08T20:01,5Z"],
    ["2026-W45-7T20:01:00Z", "2026W457T200101Z"],
    ["2026-11-08Z", "20261109Z"],
    ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z"],
  ]) {
    const record = parseJson(canonicalJSON(world.record));
    const call = record.model_binding.evidence.calls[0];
    call.started_at = start;
    call.finished_at = finish;
    const result = await signBindingChange(world, record);
    assert.equal(result.ok, true, `${start} -> ${finish}: ${result.findings.filter((f) => !f.ok).map((f) => f.detail).join("; ")}`);
  }
});

test("relay base URLs follow Python lexical path, authority, and port policy", async () => {
  const world = await relayWorld();
  for (const url of [
    "https://provider.example", "HTTPS://PROVIDER.EXAMPLE/v1", "http://127.0.0.1:1234/v1",
    "https://provider.example?", "https://provider.example/v1?#", "https://provider.example:0",
    "https://provider.example:00123", "https://[::1]/v1", "https://[fe80::1%eth0]/v1",
    "https://[v1.example]/v1",
  ]) {
    const record = parseJson(canonicalJSON(world.record));
    record.model_binding.evidence.calls[0].upstream_base_url = url;
    const result = await signBindingChange(world, record);
    assert.equal(result.ok, true, `${url}: ${result.findings.filter((f) => !f.ok).map((f) => f.detail).join("; ")}`);
  }
});

const FIXTURE_ROOT = fileURLToPath(new URL("./fixtures/web-data/", import.meta.url));
// Fixtures come from `uv run python -m sittings.e2e_local`; ids are read from its rows.json.
const REAL_IDS = JSON.parse(
  readFileSync(join(FIXTURE_ROOT, "rows.json"), "utf8"),
).rows.map((row) => row.sitting_id);

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

// Large fixtures are stored gzipped so they stay out of text diffs.
function readParsed(path) {
  const gz = `${path}.gz`;
  const text = existsSync(gz) ? gunzipSync(readFileSync(gz)).toString("utf8") : readFileSync(path, "utf8");
  return parseJson(text);
}

// Bound files are read from the repository; verifyReceipt re-hashes them, so a file that
// changed since the fixture was sealed fails loudly. Add its old bytes under
// fixtures/web-data/blobs/<sha256> when that happens.
function readBoundBlob(file) {
  const kept = join(FIXTURE_ROOT, "blobs", file.sha256);
  return new Uint8Array(readFileSync(existsSync(kept) ? kept : join(REPO_ROOT, file.path)));
}

function loadReal(id) {
  const receipt = readParsed(join(FIXTURE_ROOT, "receipts", `${id}.receipt.json`));
  const record = readParsed(join(FIXTURE_ROOT, "records", `${id}.record.json`));
  const pubkeys = readParsed(join(FIXTURE_ROOT, "sittings-pubkey.json"));
  const rows = readParsed(join(FIXTURE_ROOT, "rows.json"));
  const row = rows.rows.find((item) => item.sitting_id === id);
  const blobs = new Map();
  for (const group of [record.corpus, record.scorer]) {
    for (const file of group.files) {
      if (!blobs.has(file.sha256)) {
        blobs.set(file.sha256, readBoundBlob(file));
      }
    }
  }
  return { id, receipt, record, pubkeys, row, blobs };
}

function realOptions(loaded, extra = {}) {
  return {
    pubkeys: loaded.pubkeys,
    fetchBlob: blobFetcher(loaded.blobs),
    record: loaded.record,
    ...extra,
  };
}

test("published receipts verify and tampered copies are refused field by field", async () => {
  const loaded = REAL_IDS.map(loadReal);
  const levels = new Set();
  for (const sitting of loaded) {
    const spki = b64ToBytes(sitting.pubkeys.keys[0].spki_der_b64);
    assert.equal(await checksealKeyid(spki), sitting.pubkeys.keys[0].keyid);
    assert.equal(sitting.receipt.keyid, sitting.pubkeys.keys[0].keyid);
    const result = await verifyReceipt(sitting.receipt, realOptions(sitting));
    assert.equal(result.ok, true, `${sitting.id}\n${result.findings.filter((item) => !item.ok).map((item) => `${item.field}: ${item.detail}`).join("\n")}`);
    assert.equal(result.fingerprint, sitting.receipt.keyid);
    assert.equal(finding(result, "signature").ok, true);
    assert.match(finding(result, "keyid").detail, /fingerprint/);
    assert.equal(finding(result, "corpus").ok, true);
    assert.equal(finding(result, "scorer").ok, true);
    assert.equal(finding(result, "record").ok, true);
    assert.equal(finding(result, "answers").ok, true);
    assert.equal(finding(result, "model_binding.evidence").ok, true);
    assert.equal(finding(result, "model_binding.binds").detail, sitting.record.model_binding.binds.join("\n"));
    assert.equal(finding(result, "model_binding.does_not_bind").detail, sitting.record.model_binding.does_not_bind.join("\n"));
    assert.match(finding(result, "not_checked").detail, /not checked in browser: uv run python -m sittings\.receipt verify --receipt /);
    assert.match(finding(result, "not_checked").detail, /--record /);
    assert.match(finding(result, "not_checked").detail, /--pubkeys /);
    assert.match(finding(result, "not_checked").detail, /--blob-root /);
    assert.match(finding(result, "not_checked").detail, /--repo-root /);
    levels.add(sitting.record.model_binding.level);
    if (sitting.record.model_binding.level === "host-relayed") {
      assert.equal(finding(result, "model_binding.relay").ok, true);
    } else {
      assert.equal(result.findings.some((item) => item.field === "model_binding.relay"), false);
    }
    assert.equal(rowAccepted(sitting.row, result.predicate, sitting.record), true);
    for (const [field] of DISPLAY_FIELDS) {
      const altered = { ...sitting.row };
      const current = altered[field];
      if (typeof current === "string") altered[field] = `${current}-tampered`;
      else if (current == null) altered[field] = "tampered";
      else altered[field] = (current instanceof JsonNumber ? current.number : current) + 1;
      assert.equal(rowMatchesPredicate(altered, result.predicate), false, `${sitting.id} ${field}`);
      assert.equal(rowAccepted(altered, result.predicate, sitting.record), false, `${sitting.id} ${field}`);
    }

    const flipped = structuredClone(sitting.receipt);
    const sig = b64ToBytes(flipped.envelope.signatures[0].sig);
    sig[0] ^= 0xff;
    flipped.envelope.signatures[0].sig = bytesToB64(sig);
    const sigResult = await verifyReceipt(flipped, realOptions(sitting));
    assert.equal(sigResult.ok, false);
    assert.equal(finding(sigResult, "signature").ok, false);
    assert.equal(finding(sigResult, "keyid").ok, true);
    assert.equal(finding(sigResult, "corpus").ok, true);

    const wrapped = structuredClone(sitting.receipt);
    wrapped.keyid = "ed25519:0123456789abcdef";
    const wrapResult = await verifyReceipt(wrapped, realOptions(sitting));
    assert.equal(wrapResult.ok, false);
    assert.equal(finding(wrapResult, "keyid").ok, false);
    assert.equal(finding(wrapResult, "signature").ok, true);

    const stated = structuredClone(sitting.pubkeys);
    stated.keys[0].keyid = "ed25519:0000000000000000";
    const statedResult = await verifyReceipt(sitting.receipt, realOptions(sitting, { pubkeys: stated }));
    assert.equal(statedResult.ok, false);
    assert.equal(finding(statedResult, "keyid").ok, false);
    assert.equal(finding(statedResult, "signature").ok, true);

    const payload = b64ToBytes(sitting.receipt.envelope.payload);
    const labelAt = new TextDecoder().decode(payload).indexOf(sitting.record.agent_label);
    assert.ok(labelAt > 0);
    const payloadCopy = payload.slice();
    payloadCopy[labelAt] ^= 0x20;
    const payloadReceipt = structuredClone(sitting.receipt);
    payloadReceipt.envelope.payload = bytesToB64(payloadCopy);
    const payloadResult = await verifyReceipt(payloadReceipt, realOptions(sitting));
    assert.equal(payloadResult.ok, false);
    assert.equal(finding(payloadResult, "signature").ok, false);

    for (const field of ["corpus", "scorer"]) {
      const file = sitting.record[field].files[0];
      const blobs = new Map(sitting.blobs);
      const tampered = new Uint8Array(blobs.get(file.sha256));
      tampered[0] ^= 0xff;
      blobs.set(file.sha256, tampered);
      const blobResult = await verifyReceipt(sitting.receipt, realOptions(sitting, { fetchBlob: blobFetcher(blobs) }));
      assert.equal(blobResult.ok, false);
      assert.equal(finding(blobResult, field).ok, false);
      assert.ok(finding(blobResult, field).detail.includes(file.path));
      assert.equal(finding(blobResult, field === "corpus" ? "scorer" : "corpus").ok, true);
      assert.equal(finding(blobResult, "signature").ok, true);
    }

    const finished = parseJson(canonicalJSON(sitting.record));
    finished.finished_at = "2020-01-01T00:00:00Z";
    const finishedResult = await verifyReceipt(sitting.receipt, realOptions(sitting, { record: finished }));
    assert.equal(finishedResult.ok, false);
    assert.equal(finding(finishedResult, "record").ok, false);
    assert.equal(finding(finishedResult, "answers").ok, true);
    assert.equal(finding(finishedResult, "signature").ok, true);
    assert.equal(finding(finishedResult, "model_binding.evidence").ok, true);

    const answered = parseJson(canonicalJSON(sitting.record));
    answered.answers[0].answer = `${answered.answers[0].answer}.`;
    const answerResult = await verifyReceipt(sitting.receipt, realOptions(sitting, { record: answered }));
    assert.equal(answerResult.ok, false);
    assert.equal(finding(answerResult, "answers").ok, false);
    assert.equal(finding(answerResult, "record").ok, false);
    assert.equal(finding(answerResult, "signature").ok, true);
    if (sitting.record.model_binding.level === "host-relayed") {
      assert.equal(finding(answerResult, "model_binding.relay").ok, false);
    }

    const unpublished = parseJson(canonicalJSON(sitting.record));
    unpublished.score.publishable = false;
    unpublished.score.not_publishable_reasons = ["partial finish"];
    assert.equal(recordIsPublishable(unpublished), false);
    assert.equal(rowAccepted(sitting.row, result.predicate, unpublished), false);
  }
  assert.deepEqual([...levels].sort(), ["host-relayed", "unbound"]);
});

test("pages use the T1 receipt wording", () => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const files = [];
  for (const dir of ["lib", "instruments", "verify"]) {
    for (const name of readdirSync(join(root, dir))) {
      if (/\.(mjs|html|css)$/.test(name)) files.push(join(root, dir, name));
    }
  }
  files.push(join(root, "site.css"));
  const wording = "operator-signed T1 receipt (CheckSeal DSSE + Ed25519)";
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    assert.doesNotMatch(text, /CheckSeal seal/i, file);
    assert.doesNotMatch(text, /verified model/i, file);
  }
  assert.ok(readFileSync(join(root, "verify", "index.html"), "utf8").includes(wording));
  assert.ok(readFileSync(join(root, "instruments", "index.html"), "utf8").includes(wording));
});
