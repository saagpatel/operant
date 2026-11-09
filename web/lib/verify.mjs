// Browser and Node verifier for an operator-signed T1 receipt
// (CheckSeal DSSE + Ed25519).
//
// Canonical JSON matches sittings/canonical.py: UTF-8 JSON with sorted keys,
// separators (",", ":"), and ensure_ascii=False. Floats, non-ASCII keys, and
// integers outside ±(2^53−1) are rejected. Signatures are checked over the
// raw decoded DSSE payload bytes, never over a re-serialization.
//
// DSSE PAE and the base64 helper are copied from
// ~/Projects/checkseal/js/checkseal_verify.mjs (CheckSeal's browser verifier).
// That file imports a raw 32-byte Ed25519 key. Here the published key is SPKI,
// so the verify step uses importKey("spki", ..., { name: "Ed25519" }) and the
// CheckSeal keyid is derived from the raw 32 bytes inside that SPKI.

const PAYLOAD_TYPE = "application/vnd.in-toto+json";
const STATEMENT_TYPE = "https://in-toto.io/Statement/v1";
const PREDICATE_TYPE = "https://operant.saagarpatel.dev/sitting-receipt/v1";
const RECEIPT_FORMAT = "operant.sitting-receipt/v1";
const BINDING_FORMAT = "operant.model-binding/v1";
const PUBKEY_FORMAT = "operant.sittings-pubkey/v1";
export const RECEIPT_WORDING = "operator-signed T1 receipt (CheckSeal DSSE + Ed25519)";

// Fixed v1 policy, pinned to Python by model-binding-policy-v1.json.
export const MODEL_BINDING_POLICY = {
  "format": "operant.model-binding/v1",
  "binding_fields": [
    "binds",
    "claimed_model",
    "does_not_bind",
    "evidence",
    "evidence_sha256",
    "format",
    "level",
    "observed_model"
  ],
  "levels": {
    "unbound": {
      "binds": [],
      "does_not_bind": [
        "Which model produced the answers; the claimed model is the submitter's unverified statement.",
        "The provider's honesty about what it served; no provider signature or weights are checked.",
        "That the answer did not come from a lookup of the public corpus, including its public expected decisions.",
        "Anything about the submitter's setup outside relayed calls."
      ],
      "evidence_fields": [
        "calls",
        "claim_status"
      ]
    },
    "host-relayed": {
      "binds": [
        "For every case the host constructed the final request with the case prompt, sitting id and case id and sent it to an operator-allowlisted provider.",
        "Every recorded answer equals that call's completion text exactly.",
        "The provider reported one consistent model; the claimed and observed model comparison is recorded."
      ],
      "does_not_bind": [
        "The provider's honesty about what it served; no provider signature or weights are checked.",
        "That the answer did not come from a lookup of the public corpus, including its public expected decisions.",
        "Anything about the submitter's setup outside relayed calls."
      ],
      "evidence_fields": [
        "calls",
        "claim_status"
      ]
    }
  },
  "call_fields": [
    "call_id",
    "case_id",
    "completion_text",
    "finished_at",
    "provider",
    "request_body_b64",
    "request_sha256",
    "requested_model",
    "response_body_b64",
    "response_header_model",
    "response_model",
    "response_sha256",
    "sitting_id",
    "started_at",
    "upstream_base_url"
  ],
  "claim_status": {
    "unknown": "observed_model is null",
    "match": "claimed_model equals observed_model",
    "mismatch": "claimed_model differs from observed_model"
  },
  "observed_model": "the sole distinct nonempty response_model across calls, otherwise null"
};
const KEY_ID = /^ed25519:[0-9a-f]{16}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const MAX_SAFE_DIGITS = "9007199254740991";
const ED25519_SPKI_PREFIX = Uint8Array.of(
  0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00,
);

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

export const SITTING_ID = /^sit_[a-z2-7]{26}$/;
const RATE_KEYS = new Set(["ocs", "tpr", "fpr", "accuracy", "safe_correct_rate"]);

const textEncoder = new TextEncoder();

export class JsonNumber {
  constructor(literal) {
    this.literal = literal;
    this.float = /[.eE]/.test(literal);
    this.number = Number(literal);
  }
}

// --- canonical JSON -------------------------------------------------------

function encodeString(s) {
  let out = '"';
  for (let i = 0; i < s.length; ) {
    const cp = s.codePointAt(i);
    if (cp >= 0xd800 && cp <= 0xdfff) throw new TypeError("unpaired surrogate in string");
    if (cp === 0x22) out += '\\"';
    else if (cp === 0x5c) out += "\\\\";
    else if (cp === 0x08) out += "\\b";
    else if (cp === 0x0c) out += "\\f";
    else if (cp === 0x0a) out += "\\n";
    else if (cp === 0x0d) out += "\\r";
    else if (cp === 0x09) out += "\\t";
    else if (cp < 0x20) out += `\\u${cp.toString(16).padStart(4, "0")}`;
    else out += String.fromCodePoint(cp);
    i += cp > 0xffff ? 2 : 1;
  }
  return `${out}"`;
}

function compareCodePoints(a, b) {
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const ca = a.codePointAt(i);
    const cb = b.codePointAt(j);
    if (ca !== cb) return ca < cb ? -1 : 1;
    i += ca > 0xffff ? 2 : 1;
    j += cb > 0xffff ? 2 : 1;
  }
  if (i < a.length) return 1;
  if (j < b.length) return -1;
  return 0;
}

function keyIsAscii(key) {
  for (let i = 0; i < key.length; i++) {
    if (key.charCodeAt(i) > 0x7f) return false;
  }
  return true;
}

function canonicalIntegerLiteral(literal) {
  const match = /^(-?)(0|[1-9]\d*)$/.exec(literal);
  if (!match) throw new TypeError("floats are not canonical values");
  const digits = match[2];
  if (digits.length > MAX_SAFE_DIGITS.length || (digits.length === MAX_SAFE_DIGITS.length && digits > MAX_SAFE_DIGITS)) {
    throw new TypeError("integer outside interoperable safe range");
  }
  if (digits === "0") return "0";
  return `${match[1]}${digits}`;
}

function encodeNumber(value) {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || !Number.isInteger(value)) {
      throw new TypeError("floats are not canonical values");
    }
    if (!Number.isSafeInteger(value)) throw new TypeError("integer outside interoperable safe range");
    return Object.is(value, -0) ? "0" : String(value);
  }
  if (value.float) throw new TypeError("floats are not canonical values");
  return canonicalIntegerLiteral(value.literal);
}

export function canonicalJSON(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") return encodeString(value);
  if (typeof value === "number" || value instanceof JsonNumber) return encodeNumber(value);
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJSON(item)).join(",")}]`;
  if (typeof value === "object") {
    const proto = Object.getPrototypeOf(value);
    if (proto !== Object.prototype && proto !== null) {
      throw new TypeError("cannot canonicalize this object");
    }
    if (Object.getOwnPropertySymbols(value).length > 0) {
      throw new TypeError("cannot canonicalize this object");
    }
    const keys = Object.keys(value).sort(compareCodePoints);
    const parts = keys.map((key) => {
      if (!keyIsAscii(key)) throw new TypeError("object keys must be ASCII strings");
      if (value[key] === undefined) throw new TypeError(`undefined at ${key}`);
      return `${encodeString(key)}:${canonicalJSON(value[key])}`;
    });
    return `{${parts.join(",")}}`;
  }
  throw new TypeError(`cannot canonicalize ${typeof value}`);
}

export function parseJson(text, { rejectDuplicateKeys = false } = {}) {
  if (typeof text !== "string") throw new SyntaxError("JSON text must be a string");
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0;

  const value = parseValue(0);
  skipWs();
  if (i !== text.length) throw new SyntaxError("trailing data after JSON value");
  return value;

  function skipWs() {
    while (i < text.length) {
      const c = text.charCodeAt(i);
      if (c === 32 || c === 9 || c === 10 || c === 13) i += 1;
      else break;
    }
  }

  function parseValue(depth) {
    if (depth > 64) throw new SyntaxError("JSON nesting is too deep");
    skipWs();
    if (i >= text.length) throw new SyntaxError("unexpected end of JSON");
    const c = text[i];
    if (c === "{") return parseObject(depth);
    if (c === "[") return parseArray(depth);
    if (c === '"') return parseString();
    if (c === "t") return parseLiteral("true", true);
    if (c === "f") return parseLiteral("false", false);
    if (c === "n") return parseLiteral("null", null);
    if (c === "-" || (c >= "0" && c <= "9")) return parseNumber();
    throw new SyntaxError(`unexpected ${c}`);
  }

  function parseLiteral(word, value) {
    if (text.slice(i, i + word.length) !== word) throw new SyntaxError(`expected ${word}`);
    i += word.length;
    return value;
  }

  function parseNumber() {
    const start = i;
    if (text[i] === "-") i += 1;
    if (text[i] === "0") i += 1;
    else if (text[i] >= "1" && text[i] <= "9") {
      while (text[i] >= "0" && text[i] <= "9") i += 1;
    } else throw new SyntaxError("bad number");
    if (text[i] === ".") {
      i += 1;
      if (!(text[i] >= "0" && text[i] <= "9")) throw new SyntaxError("bad fraction");
      while (text[i] >= "0" && text[i] <= "9") i += 1;
    }
    if (text[i] === "e" || text[i] === "E") {
      i += 1;
      if (text[i] === "+" || text[i] === "-") i += 1;
      if (!(text[i] >= "0" && text[i] <= "9")) throw new SyntaxError("bad exponent");
      while (text[i] >= "0" && text[i] <= "9") i += 1;
    }
    return new JsonNumber(text.slice(start, i));
  }

  function parseString() {
    i += 1;
    let out = "";
    while (i < text.length) {
      const c = text[i];
      if (c === '"') {
        i += 1;
        return out;
      }
      if (c === "\\") {
        i += 1;
        const e = text[i];
        i += 1;
        if (e === '"') out += '"';
        else if (e === "\\") out += "\\";
        else if (e === "/") out += "/";
        else if (e === "b") out += "\b";
        else if (e === "f") out += "\f";
        else if (e === "n") out += "\n";
        else if (e === "r") out += "\r";
        else if (e === "t") out += "\t";
        else if (e === "u") {
          const hex = text.slice(i, i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new SyntaxError("bad unicode escape");
          i += 4;
          let cp = Number.parseInt(hex, 16);
          if (cp >= 0xd800 && cp <= 0xdbff && text[i] === "\\" && text[i + 1] === "u") {
            const hex2 = text.slice(i + 2, i + 6);
            if (/^[0-9a-fA-F]{4}$/.test(hex2)) {
              const cp2 = Number.parseInt(hex2, 16);
              if (cp2 >= 0xdc00 && cp2 <= 0xdfff) {
                i += 6;
                cp = 0x10000 + ((cp - 0xd800) << 10) + (cp2 - 0xdc00);
              }
            }
          }
          out += String.fromCodePoint(cp);
        } else throw new SyntaxError("bad escape");
        continue;
      }
      if (c.charCodeAt(0) < 0x20) throw new SyntaxError("raw control character in string");
      out += c;
      i += 1;
    }
    throw new SyntaxError("unterminated string");
  }

  function parseObject(depth) {
    i += 1;
    const obj = Object.create(null);
    skipWs();
    if (text[i] === "}") {
      i += 1;
      return obj;
    }
    while (i < text.length) {
      skipWs();
      if (text[i] !== '"') throw new SyntaxError("expected object key");
      const key = parseString();
      if (rejectDuplicateKeys && Object.hasOwn(obj, key)) throw new SyntaxError("duplicate JSON key");
      skipWs();
      if (text[i] !== ":") throw new SyntaxError("expected colon");
      i += 1;
      obj[key] = parseValue(depth + 1);
      skipWs();
      if (text[i] === ",") {
        i += 1;
        continue;
      }
      if (text[i] === "}") {
        i += 1;
        return obj;
      }
      throw new SyntaxError("expected comma or end of object");
    }
    throw new SyntaxError("unterminated object");
  }

  function parseArray(depth) {
    i += 1;
    const arr = [];
    skipWs();
    if (text[i] === "]") {
      i += 1;
      return arr;
    }
    while (i < text.length) {
      arr.push(parseValue(depth + 1));
      skipWs();
      if (text[i] === ",") {
        i += 1;
        continue;
      }
      if (text[i] === "]") {
        i += 1;
        return arr;
      }
      throw new SyntaxError("expected comma or end of array");
    }
    throw new SyntaxError("unterminated array");
  }
}

export function displayScalar(value) {
  if (typeof value === "string") return value;
  if (value == null) return "";
  return canonicalJSON(value);
}

// Trim a 6-decimal rate string for display. The digits stay the rate's own
// characters; this does not parse the rate as a float.
export function displayRate(value) {
  if (typeof value !== "string") return displayScalar(value);
  const match = /^(-?)(\d+)\.(\d{6})$/.exec(value);
  if (!match) return value;
  const fraction = match[3].replace(/0+$/, "");
  if (fraction.length === 0) return `${match[1]}${match[2]}`;
  return `${match[1]}${match[2]}.${fraction}`;
}

export function isRateKey(key) {
  return RATE_KEYS.has(key);
}

export async function sha256Hex(bytes) {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const copy =
    view.byteOffset === 0 && view.byteLength === view.buffer.byteLength
      ? view
      : view.slice();
  const digest = await crypto.subtle.digest("SHA-256", copy);
  let hex = "";
  for (const b of new Uint8Array(digest)) hex += b.toString(16).padStart(2, "0");
  return hex;
}

async function canonicalSha256(value) {
  return sha256Hex(textEncoder.encode(canonicalJSON(value)));
}

function canonicalEqual(left, right) {
  try {
    return canonicalJSON(left) === canonicalJSON(right);
  } catch {
    return false;
  }
}

// --- DSSE, copied from ~/Projects/checkseal/js/checkseal_verify.mjs -------

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function pae(payloadType, payload) {
  const enc = new TextEncoder();
  const head = enc.encode(`DSSEv1 ${enc.encode(payloadType).length} ${payloadType} ${payload.length} `);
  const out = new Uint8Array(head.length + payload.length);
  out.set(head, 0);
  out.set(payload, head.length);
  return out;
}

function decodeB64(b64) {
  if (typeof b64 !== "string" || b64.length === 0) throw new Error("empty base64");
  const cleaned = b64.replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
  const pad = cleaned.length % 4 === 0 ? "" : "=".repeat(4 - (cleaned.length % 4));
  return b64ToBytes(cleaned + pad);
}

function pemToSpki(pem) {
  const body = String(pem).replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  return decodeB64(body);
}

function bytesEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

function rawEd25519FromSpki(spki) {
  if (!(spki instanceof Uint8Array) || spki.length !== ED25519_SPKI_PREFIX.length + 32) {
    throw new Error("Ed25519 SPKI has the wrong length");
  }
  for (let i = 0; i < ED25519_SPKI_PREFIX.length; i++) {
    if (spki[i] !== ED25519_SPKI_PREFIX[i]) throw new Error("SPKI is not an Ed25519 public key");
  }
  return spki.subarray(ED25519_SPKI_PREFIX.length);
}

export async function checksealKeyid(spki) {
  const hex = await sha256Hex(rawEd25519FromSpki(spki));
  return `ed25519:${hex.slice(0, 16)}`;
}

function trustedKeys(pubkeys) {
  if (!pubkeys || pubkeys.format !== PUBKEY_FORMAT || !Array.isArray(pubkeys.keys)) return [];
  return pubkeys.keys.filter((key) => key && typeof key.keyid === "string" && key.alg === "ed25519");
}

function spkiBytes(key) {
  const hasSpki = typeof key.spki_der_b64 === "string" && key.spki_der_b64.length > 0;
  const hasPem = typeof key.public_key_pem === "string" && key.public_key_pem.includes("BEGIN");
  if (!hasSpki && !hasPem) throw new Error("missing SPKI public key");
  const spki = hasSpki ? decodeB64(key.spki_der_b64) : null;
  const pem = hasPem ? pemToSpki(key.public_key_pem) : null;
  if (spki && pem && !bytesEqual(spki, pem)) throw new Error("trusted key SPKI and PEM disagree");
  return spki || pem;
}

async function prepareTrustedKeys(pubkeys) {
  const prepared = [];
  for (const key of trustedKeys(pubkeys)) {
    try {
      const spki = spkiBytes(key);
      prepared.push({ stated: key.keyid, derived: await checksealKeyid(spki), spki, error: "" });
    } catch (err) {
      prepared.push({ stated: key.keyid, derived: "", spki: null, error: err.message });
    }
  }
  return prepared;
}

async function verifyRawSignature(payloadType, payloadBytes, sigB64, spki) {
  // `payloadBytes` is the base64-decoded payload. Do not re-encode it.
  const signed = pae(payloadType, payloadBytes);
  const cryptoKey = await crypto.subtle.importKey("spki", spki, { name: "Ed25519" }, false, ["verify"]);
  const sigBytes = decodeB64(sigB64);
  return crypto.subtle.verify({ name: "Ed25519" }, cryptoKey, sigBytes, signed);
}

export function fullRecheckCommand(sittingId) {
  const known = typeof sittingId === "string" && SITTING_ID.test(sittingId);
  const receipt = known ? `web/data/receipts/${sittingId}.receipt.json` : "RECEIPT.json";
  const record = known ? `web/data/records/${sittingId}.record.json` : "RECORD.json";
  const pubkeys = known ? "web/data/sittings-pubkey.json" : "sittings-pubkey.json";
  return `uv run python -m sittings.receipt verify --receipt ${receipt} --record ${record} --pubkeys ${pubkeys} --blob-root web/data/blobs --repo-root .`;
}

function push(findings, field, ok, detail) {
  findings.push({ field, ok, detail });
}

function asBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  return null;
}

function countOf(value) {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value >= 0 ? value : undefined;
  }
  if (value instanceof JsonNumber) {
    if (value.float || !Number.isSafeInteger(value.number) || value.number < 0) return undefined;
    try {
      if (canonicalIntegerLiteral(value.literal) !== String(value.number)) return undefined;
    } catch {
      return undefined;
    }
    return value.number;
  }
  return undefined;
}

function sameStringList(left, right) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === right.length
    && left.every((item, index) => item === right[index]);
}

function validRelativePath(path) {
  if (typeof path !== "string" || path.length === 0 || path.includes("\\") || path.includes("\u0000")) return false;
  if (path.startsWith("/")) return false;
  const parts = path.split("/");
  return parts.every((part) => part.length > 0 && part !== "." && part !== ".." && !part.includes(":"));
}

function manifestKeysOk(value, expected) {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

async function checkManifest(field, manifest, fetchBlob) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest) || !manifestKeysOk(manifest, ["sha256", "files"])) {
    return { field, ok: false, detail: `${field} manifest is missing` };
  }
  if (!DIGEST.test(manifest.sha256 || "")) {
    return { field, ok: false, detail: `${field} aggregate digest is missing` };
  }
  const files = manifest.files;
  if (!Array.isArray(files) || files.length === 0) {
    return { field, ok: false, detail: `${field} manifest has no files` };
  }
  for (let index = 0; index < files.length; index++) {
    const file = files[index];
    if (!file || typeof file !== "object" || Array.isArray(file) || !manifestKeysOk(file, ["path", "sha256", "bytes"])) {
      return { field, ok: false, detail: `${field} file entry is malformed` };
    }
    if (!validRelativePath(file.path)) {
      return { field, ok: false, detail: `${field} file entry has no path` };
    }
    if (!DIGEST.test(file.sha256 || "")) {
      return { field, ok: false, detail: `${field} file ${file.path} has no sha256` };
    }
    if (countOf(file.bytes) === undefined) {
      return { field, ok: false, detail: `${field} file ${file.path} has no byte length` };
    }
    if (index > 0 && compareCodePoints(files[index - 1].path, file.path) >= 0) {
      return { field, ok: false, detail: `${field} paths are not sorted and unique` };
    }
  }
  const pairs = files.map((file) => ({ path: file.path, sha256: file.sha256 }));
  let aggregate = "";
  try {
    aggregate = await canonicalSha256(pairs);
  } catch (err) {
    return { field, ok: false, detail: `${field} aggregate could not be hashed (${err.message})` };
  }
  if (aggregate !== manifest.sha256) {
    return {
      field,
      ok: false,
      detail: `${field} aggregate ${manifest.sha256} does not match recomputed ${aggregate}`,
    };
  }
  if (typeof fetchBlob !== "function") {
    return { field, ok: false, detail: `${field} file bytes were not provided for re-hash` };
  }
  const problems = [];
  for (const file of files) {
    let raw;
    try {
      raw = await fetchBlob(file.sha256);
    } catch (err) {
      problems.push(`${file.path}: ${err.message}`);
      continue;
    }
    const bytes = asBytes(raw);
    if (!bytes) {
      problems.push(`${file.path}: blob ${file.sha256} missing`);
      continue;
    }
    const got = await sha256Hex(bytes);
    if (got !== file.sha256) problems.push(`${file.path}: blob sha256 ${got} != ${file.sha256}`);
    const size = countOf(file.bytes);
    if (bytes.length !== size) problems.push(`${file.path}: byte length ${bytes.length} != ${size}`);
  }
  if (problems.length > 0) return { field, ok: false, detail: problems.join("; ") };
  return { field, ok: true, detail: `${field} aggregate ${aggregate}; ${files.length} file blob(s) match` };
}

function exactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && sameStringList(Object.keys(value).sort(), [...keys].sort());
}

// Relay evidence uses Python's base64.b64decode(validate=True), unlike the
// forgiving base64 helper used for public-key input.
function relayBody(value) {
  if (typeof value !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error("invalid base64");
  }
  return b64ToBytes(value);
}

function relayBaseUrl(value) {
  if (typeof value !== "string") return false;
  // Mirror urlsplit's lexical checks, without WHATWG host/path normalization
  // (which would turn http://127.1 into the allowlisted literal loopback).
  const cleaned = value.replace(/^[\x00-\x20]+/, "").replace(/[\t\r\n]/g, "");
  const parts = /^(https?):\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/i.exec(cleaned);
  if (!parts || !["", "/v1"].includes(parts[3]) || parts[4] || parts[5]) return false;
  const authority = parts[2];
  if (!authority || authority.includes("@") || authority.endsWith(":")) return false;
  const normalized = authority.replace(/[:?#]/g, "").normalize("NFKC");
  if (/[/?#@:]/.test(normalized)) return false;
  let host;
  let port;
  if (authority.includes("[") || authority.includes("]")) {
    const bracketed = /^\[([^\[\]]+)\](?::(.*))?$/.exec(authority);
    if (!bracketed) return false;
    [, host, port] = bracketed;
    if (host.startsWith("v")) {
      if (!/^v[0-9a-fA-F]+\..+$/.test(host)) return false;
    } else {
      const [address, scope, extraScope] = host.split("%");
      if (scope === "" || extraScope !== undefined) return false;
      try {
        new URL(`https://[${address}]`); // IPv6 syntax only; no request is made.
      } catch {
        return false;
      }
    }
  } else {
    [host, ...port] = authority.split(":");
    port = port.length ? port.join(":") : undefined;
  }
  return Boolean(host) && (parts[1].toLowerCase() === "https" || host === "127.0.0.1")
    && (port === undefined || (/^[0-9]+$/.test(port) && Number(port) <= 65535));
}

function relayInstant(value) {
  // Keep microseconds: Date.parse alone silently accepts impossible dates and
  // loses the precision needed to reject reversed calls within one millisecond.
  const match = typeof value === "string" && /^(\d{4})(?:(?:-(\d{2})-(\d{2})|(\d{2})(\d{2}))|(?:-W(\d{2})(?:-([1-7]))?|W(\d{2})([1-7])?))(.*)Z$/u.exec(value);
  if (!match) throw new Error("invalid relay timestamp");
  const year = Number(match[1]);
  const month = Number(match[2] || match[4]);
  const day = Number(match[3] || match[5]);
  const date = new Date(0);
  if (year < 1) throw new Error("invalid relay timestamp");
  if (match[6] || match[8]) {
    const week = Number(match[6] || match[8]);
    const weekday = Number(match[7] || match[9] || 1);
    date.setUTCFullYear(year, 0, 4);
    const jan4Day = date.getUTCDay() || 7;
    date.setUTCDate(4 - jan4Day + 1 + (week - 1) * 7 + weekday - 1);
    const thursday = new Date(date);
    thursday.setUTCDate(date.getUTCDate() + 4 - weekday);
    if (week < 1 || week > 53 || thursday.getUTCFullYear() !== year) throw new Error("invalid relay timestamp");
  } else {
    date.setUTCFullYear(year, month - 1, day);
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
      throw new Error("invalid relay timestamp");
    }
  }
  if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) throw new Error("invalid relay timestamp");
  // datetime.fromisoformat accepts extended/basic ISO dates, an arbitrary
  // one-character separator, and reduced-precision times. A bare date plus the
  // appended +00:00 is parsed by Python as a naive midnight time.
  const remainder = match[10];
  const time = remainder ? [...remainder].slice(1).join("") : "00:00";
  const clock = /^(\d{2})(?:(?::(\d{2})(?::(\d{2}))?)|(?:(\d{2})(\d{2})?))?(?:[.,](\d+))?$/.exec(time);
  if (!clock) throw new Error("invalid relay timestamp");
  const hour = Number(clock[1]);
  const minute = Number(clock[2] || clock[4] || 0);
  const second = Number(clock[3] || clock[5] || 0);
  date.setUTCHours(hour, minute, second, 0);
  if (hour > 23 || minute > 59 || second > 59) {
    throw new Error("invalid relay timestamp");
  }
  return { aware: Boolean(remainder), micros: BigInt(date.getTime()) * 1000n + BigInt((clock[6] || "").padEnd(6, "0").slice(0, 6)) };
}

async function checkRelayCalls(calls, record) {
  const fail = (detail) => ({ field: "model_binding.evidence.calls", ok: false, detail });
  if (!Array.isArray(calls)) return fail("calls is not a list");
  if (calls.length && (!record || !Array.isArray(record.case_order))) {
    return fail("no record supplied to check relay context");
  }
  const ids = new Set();
  const cases = new Set();
  for (const call of calls) {
    if (!exactKeys(call, MODEL_BINDING_POLICY.call_fields)) return fail("invalid relay call schema");
    if (typeof call.call_id !== "string" || !/^call_[0-9a-f]{32}$/.test(call.call_id)) return fail("invalid relay call id");
    if (ids.has(call.call_id) || cases.has(call.case_id)) return fail("duplicate relay call or case");
    ids.add(call.call_id);
    cases.add(call.case_id);
    if (call.sitting_id !== record.sitting_id || !record.case_order.includes(call.case_id)) return fail("invalid relay context");
    if (!relayBaseUrl(call.upstream_base_url)) return fail("invalid operator upstream base URL");
    if (![call.provider, call.requested_model, call.started_at, call.finished_at].every((v) => typeof v === "string" && v.length > 0)) return fail("invalid relay metadata");
    if (![call.response_model, call.response_header_model].every((v) => v === null || typeof v === "string")) return fail("invalid observed model");
    const bodies = {};
    for (const kind of ["request", "response"]) {
      let raw;
      try {
        raw = relayBody(call[`${kind}_body_b64`]);
      } catch {
        return fail(`relay call ${call.call_id} ${kind} body is not base64`);
      }
      if (await sha256Hex(raw) !== call[`${kind}_sha256`]) return fail(`relay call ${call.call_id} ${kind} hash does not match`);
      try {
        const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw);
        if (text.charCodeAt(0) === 0xfeff) throw new Error("JSON BOM");
        bodies[kind] = parseJson(text, { rejectDuplicateKeys: true });
      } catch {
        return fail(`relay call ${call.call_id} ${kind} is not valid JSON`);
      }
    }
    const prompt = bodies.request?.messages?.[0]?.content;
    const expectedRequest = {
      model: call.requested_model,
      messages: [{ role: "user", content: prompt }],
      metadata: { sitting_id: record.sitting_id, case_id: call.case_id },
      stream: false,
    };
    if (typeof prompt !== "string" || !canonicalEqual(bodies.request, expectedRequest)) return fail("request is not host-built");
    const response = bodies.response;
    if (!Array.isArray(response?.choices) || response.choices.length !== 1
        || typeof call.completion_text !== "string"
        || response.choices[0]?.message?.content !== call.completion_text
        || (response.model ?? null) !== call.response_model) return fail("response completion or model is invalid");
    try {
      const start = relayInstant(call.started_at);
      const finish = relayInstant(call.finished_at);
      if (start.aware !== finish.aware || start.micros > finish.micros) return fail("invalid relay time order");
    } catch {
      return fail("invalid relay timestamp");
    }
  }
  return { field: "model_binding.evidence.calls", ok: true, detail: "relay calls have the exact v1 schema, valid bodies, context, and metadata" };
}

async function checkRelay(binding, record, callsFinding) {
  const fail = (detail) => ({ field: "model_binding.relay", ok: false, detail });
  if (!callsFinding.ok) return fail(callsFinding.detail);
  const calls = binding.evidence.calls;
  if (!calls.length) return fail("host-relayed evidence has no relay calls");
  if (!record || !Array.isArray(record.answers)) return fail("host-relayed answers were not rechecked because no record was supplied");
  const byId = new Map();
  const cases = new Set();
  const requests = new Set();
  for (const call of calls) {
    cases.add(call.case_id);
    requests.add(JSON.stringify([call.provider, call.upstream_base_url, call.requested_model]));
    byId.set(call.call_id, call);
  }
  if (!Array.isArray(record.case_order) || record.case_order.length !== cases.size
      || new Set(record.case_order).size !== cases.size || !record.case_order.every((id) => cases.has(id))
      || requests.size !== 1 || binding.claimed_model !== record.claimed_model) {
    return { field: "model_binding.relay", ok: false, detail: "relay cases, request metadata, or claimed model do not match" };
  }
  const answers = record.answers;
  if (answers.length !== calls.length) {
    return {
      field: "model_binding.relay",
      ok: false,
      detail: `answers (${answers.length}) do not match relay calls (${calls.length})`,
    };
  }
  const used = new Set();
  const models = new Set();
  for (const answer of answers) {
    if (!answer || typeof answer.answer !== "string" || typeof answer.case_id !== "string") {
      return { field: "model_binding.relay", ok: false, detail: "an answer is missing text" };
    }
    const ids = answer.relay_call_ids;
    if (!Array.isArray(ids) || ids.length !== 1 || typeof ids[0] !== "string") {
      return { field: "model_binding.relay", ok: false, detail: `answer ${answer.case_id} is not tied to one relay call` };
    }
    const call = byId.get(ids[0]);
    if (!call || used.has(ids[0])) {
      return { field: "model_binding.relay", ok: false, detail: `answer ${answer.case_id} relay call is missing` };
    }
    used.add(ids[0]);
    if (call.case_id !== answer.case_id) {
      return {
        field: "model_binding.relay",
        ok: false,
        detail: `answer ${answer.case_id} does not match relay case ${call.case_id}`,
      };
    }
    if (answer.answer !== call.completion_text) {
      return {
        field: "model_binding.relay",
        ok: false,
        detail: `answer ${answer.case_id} does not equal the recorded completion text`,
      };
    }
    const request = parseJson(new TextDecoder("utf-8", { fatal: true }).decode(relayBody(call.request_body_b64)), { rejectDuplicateKeys: true });
    if (await sha256Hex(textEncoder.encode(request.messages[0].content)) !== answer.prompt_sha256) {
      return fail(`relay call ${call.call_id} request or prompt hash is invalid`);
    }
    const model = call.response_model;
    if (!model || (call.response_header_model !== null && call.response_header_model !== model)) {
      return fail(`relay call ${call.call_id} response completion or model is invalid`);
    }
    models.add(model);
  }
  if (used.size !== calls.length) {
    return { field: "model_binding.relay", ok: false, detail: "a relay call is not tied to an answer" };
  }
  const observed = models.size === 1 ? models.values().next().value : null;
  const claimStatus = observed === null ? "unknown" : record.claimed_model === observed ? "match" : "mismatch";
  if (observed === null || binding.observed_model !== observed || binding.evidence.claim_status !== claimStatus) {
    return { field: "model_binding.relay", ok: false, detail: "relay models, observed model, or claim status do not match" };
  }
  return {
    field: "model_binding.relay",
    ok: true,
    detail: `each of ${answers.length} answers equals the recorded completion text; evidence body hashes match`,
  };
}

async function checkBinding(binding, record, claimedModel) {
  const findings = [];
  push(findings, "model_binding.schema", exactKeys(binding, MODEL_BINDING_POLICY.binding_fields), "exact v1 binding keys");
  if (!binding || typeof binding !== "object" || Array.isArray(binding)) {
    push(findings, "model_binding.format", false, "model binding is missing");
    push(findings, "model_binding.level", false, "model binding is missing");
    push(findings, "model_binding.evidence", false, "model binding is missing");
    push(findings, "model_binding.binds", false, "model binding is missing");
    push(findings, "model_binding.does_not_bind", false, "model binding is missing");
    return findings;
  }
  push(
    findings,
    "model_binding.format",
    binding.format === BINDING_FORMAT,
    binding.format === BINDING_FORMAT ? BINDING_FORMAT : `format is ${binding.format}`,
  );
  const levelKnown = typeof binding.level === "string" && Object.hasOwn(MODEL_BINDING_POLICY.levels, binding.level);
  const policy = levelKnown ? MODEL_BINDING_POLICY.levels[binding.level] : null;
  push(
    findings,
    "model_binding.level",
    levelKnown,
    levelKnown ? `binding level: ${binding.level}` : `unknown binding level ${JSON.stringify(binding.level)}`,
  );
  const evidenceSchemaOk = Boolean(policy && exactKeys(binding.evidence, policy.evidence_fields)
    && Array.isArray(binding.evidence.calls));
  push(findings, "model_binding.evidence.schema", evidenceSchemaOk, "evidence must contain exactly calls (a list) and claim_status");
  let evidenceOk = false;
  let evidenceDetail;
  try {
    const got = await canonicalSha256(binding.evidence);
    evidenceOk = got === binding.evidence_sha256;
    evidenceDetail = evidenceOk
      ? `evidence_sha256 ${got}`
      : `evidence_sha256 ${binding.evidence_sha256} does not match recomputed ${got}`;
  } catch (err) {
    evidenceDetail = `evidence could not be hashed (${err.message})`;
  }
  push(findings, "model_binding.evidence", evidenceOk, evidenceDetail);
  push(findings, "model_binding.evidence_sha256", evidenceOk, evidenceDetail);
  const bindsOk = Boolean(policy && sameStringList(binding.binds, policy.binds));
  const limitsOk = Boolean(policy && sameStringList(binding.does_not_bind, policy.does_not_bind));
  push(
    findings,
    "model_binding.binds",
    bindsOk,
    bindsOk ? binding.binds.join("\n") : "binds does not match the fixed v1 policy for this level",
  );
  push(
    findings,
    "model_binding.does_not_bind",
    limitsOk,
    limitsOk ? binding.does_not_bind.join("\n") : "does_not_bind does not match the fixed v1 policy for this level",
  );
  push(findings, "model_binding.claimed_model", typeof binding.claimed_model === "string"
    && binding.claimed_model === claimedModel, "claimed model must equal the signed predicate claim");
  const callsFinding = await checkRelayCalls(binding.evidence?.calls, record);
  findings.push(callsFinding);
  const models = new Set((Array.isArray(binding.evidence?.calls) ? binding.evidence.calls : [])
    .map((call) => call?.response_model).filter((model) => typeof model === "string" && model.length > 0));
  const observed = models.size === 1 ? models.values().next().value : null;
  const claimStatus = observed === null ? "unknown" : claimedModel === observed ? "match" : "mismatch";
  push(findings, "model_binding.observed_model", callsFinding.ok && binding.observed_model === observed,
    "observed model must be the sole distinct nonempty response_model across calls, otherwise null");
  push(findings, "model_binding.evidence.claim_status", evidenceSchemaOk && callsFinding.ok
    && binding.evidence.claim_status === claimStatus, "claim_status must derive from claimed and observed models");
  if (binding.level === "host-relayed") findings.push(await checkRelay(binding, record, callsFinding));
  return findings;
}

async function openReceipt(receipt, pubkeys) {
  const findings = [];
  let payloadBytes = null;
  let payloadText = "";
  let statement = null;
  let predicate = null;
  let fingerprint = null;

  const outerOk = receipt?.receipt_format === RECEIPT_FORMAT;
  try {
    const envelope = receipt?.envelope;
    if (!envelope || typeof envelope !== "object") throw new Error("missing envelope");
    if (envelope.payloadType !== PAYLOAD_TYPE) throw new Error(`payloadType is ${envelope.payloadType}`);
    payloadBytes = decodeB64(envelope.payload);
    payloadText = new TextDecoder("utf-8", { fatal: true }).decode(payloadBytes);
    statement = parseJson(payloadText);
    if (!statement || typeof statement !== "object" || Array.isArray(statement)) throw new Error("statement is not an object");
    if (statement._type !== STATEMENT_TYPE) throw new Error(`_type is ${statement._type}`);
    if (canonicalJSON(statement) !== payloadText) throw new Error("payload is not canonical JSON");
    predicate = statement.predicate;
    if (!predicate || typeof predicate !== "object" || Array.isArray(predicate)) throw new Error("predicate is missing");
    push(findings, "payload", true, "statement payload is canonical JSON");
  } catch (err) {
    push(findings, "payload", false, `payload is not a canonical Statement (${err.message})`);
    statement = null;
    predicate = null;
  }

  const innerOk = predicate == null || predicate.receipt_format === RECEIPT_FORMAT;
  push(
    findings,
    "receipt_format",
    outerOk && innerOk,
    outerOk && innerOk ? RECEIPT_FORMAT : `receipt_format ${receipt?.receipt_format}`,
  );

  const typeOk = statement?.predicateType === PREDICATE_TYPE;
  push(
    findings,
    "predicate_type",
    typeOk,
    typeOk ? PREDICATE_TYPE : "predicateType is not the sitting receipt predicate",
  );

  const bindsOk = sameStringList(predicate?.seal_binds, SEAL_BINDS);
  push(findings, "seal_binds", bindsOk, bindsOk ? "fixed receipt claims" : "receipt claims do not match v1");
  const limitsOk = sameStringList(predicate?.seal_does_not_bind, SEAL_DOES_NOT_BIND);
  push(
    findings,
    "seal_does_not_bind",
    limitsOk,
    limitsOk ? "fixed receipt limits" : "receipt limits do not match v1",
  );

  const prepared = await prepareTrustedKeys(pubkeys);
  const signatures = Array.isArray(receipt?.envelope?.signatures) ? receipt.envelope.signatures : [];
  const wrapper = typeof receipt?.keyid === "string" ? receipt.keyid : "";
  const signature = signatures.length === 1 ? signatures[0] : null;
  const sigKeyid = signature && typeof signature.keyid === "string" ? signature.keyid : "";
  const sigB64 = signature && typeof signature.sig === "string" ? signature.sig : "";
  const derivedMatch = prepared.filter((item) => item.derived && item.derived === sigKeyid);
  const verifySpki = derivedMatch.length === 1 ? derivedMatch[0].spki : null;

  const statedMatch = prepared.filter((item) => item.stated === wrapper && item.derived === wrapper);
  const agreed = KEY_ID.test(wrapper) && signatures.length === 1 && sigKeyid === wrapper && statedMatch.length === 1 && !statedMatch[0].error;
  if (agreed) {
    fingerprint = wrapper;
    push(findings, "keyid", true, `fingerprint ${wrapper}; wrapper, signature, and trusted key keyid agree`);
  } else if (!wrapper) {
    push(findings, "keyid", false, "receipt has no keyid");
  } else if (!KEY_ID.test(wrapper)) {
    push(findings, "keyid", false, `invalid wrapper keyid ${wrapper}`);
  } else if (signatures.length !== 1 || sigKeyid !== wrapper) {
    push(findings, "keyid", false, "wrapper and signature keyids disagree");
  } else if (prepared.some((item) => item.stated === wrapper && item.error)) {
    push(findings, "keyid", false, prepared.find((item) => item.stated === wrapper).error);
  } else if (prepared.some((item) => item.stated === wrapper && item.derived && item.derived !== wrapper)) {
    push(findings, "keyid", false, "trusted key keyid does not match the CheckSeal fingerprint");
  } else {
    push(findings, "keyid", false, `keyid ${wrapper} is not in the trusted set`);
  }

  if (payloadBytes && verifySpki && sigB64 && typeof receipt?.envelope?.payloadType === "string") {
    try {
      const ok = await verifyRawSignature(receipt.envelope.payloadType, payloadBytes, sigB64, verifySpki);
      push(
        findings,
        "signature",
        ok,
        ok ? "trusted Ed25519 signature valid over the raw payload bytes" : "Ed25519 signature did not match the trusted key",
      );
    } catch (err) {
      push(findings, "signature", false, `Ed25519 check failed (${err.message})`);
    }
  } else if (!payloadBytes) {
    push(findings, "signature", false, "no payload bytes to check");
  } else {
    push(findings, "signature", false, "no trusted key for this keyid");
  }

  const sittingId = typeof predicate?.sitting_id === "string" ? predicate.sitting_id : "";
  const subject = statement?.subject?.[0];
  const digest = subject?.digest?.sha256;
  const nameOk = subject?.name === `operant-sitting:${sittingId}` && sittingId.length > 0;
  const digestOk = typeof digest === "string" && DIGEST.test(digest);
  push(
    findings,
    "subject",
    Boolean(nameOk && digestOk),
    nameOk && digestOk ? `subject ${subject.name} digest ${digest}` : "subject name or digest is missing",
  );

  return { findings, statement, predicate, payloadBytes, fingerprint };
}

export async function verifySignature(receipt, pubkeys) {
  try {
    const opened = await openReceipt(receipt, pubkeys);
    const ok = opened.findings.every((item) => item.ok);
    return { ok, findings: opened.findings, predicate: opened.predicate, fingerprint: opened.fingerprint };
  } catch (err) {
    return {
      ok: false,
      findings: [{ field: "receipt", ok: false, detail: `verifier error: ${err.message}` }],
      predicate: null,
      fingerprint: null,
    };
  }
}

export async function verifyReceipt(receipt, options = {}) {
  try {
    const opened = await openReceipt(receipt, options.pubkeys);
    const findings = opened.findings.slice();
    const { statement, predicate } = opened;
    findings.push(await checkManifest("corpus", predicate?.corpus, options.fetchBlob));
    findings.push(await checkManifest("scorer", predicate?.scorer, options.fetchBlob));

    if (options.record && typeof options.record === "object") {
      let recordHash = "";
      let answersHash = "";
      try {
        recordHash = await canonicalSha256(options.record);
        answersHash = await canonicalSha256(options.record.answers);
      } catch (err) {
        push(findings, "record", false, `record could not be canonicalized (${err.message})`);
        push(findings, "answers", false, "answers could not be canonicalized");
        recordHash = "";
      }
      if (recordHash) {
        const subjectHash = statement?.subject?.[0]?.digest?.sha256;
        const consistent = Boolean(
          predicate
          && predicate.sitting_id === options.record.sitting_id
          && predicate.agent_label === options.record.agent_label
          && predicate.claimed_model === options.record.claimed_model
          && canonicalEqual(predicate.corpus, options.record.corpus)
          && canonicalEqual(predicate.scorer, options.record.scorer)
          && canonicalEqual(predicate.score, options.record.score)
          && canonicalEqual(predicate.model_binding, options.record.model_binding),
        );
        const recordOk = subjectHash === recordHash && consistent;
        push(
          findings,
          "record",
          recordOk,
          recordOk
            ? `canonical record sha256 ${recordHash}`
            : subjectHash === recordHash
              ? "signed predicate does not match the record"
              : `canonical record sha256 ${recordHash} != subject ${subjectHash || "(missing)"}`,
        );
        const answersOk = predicate?.answers_sha256 === answersHash;
        push(
          findings,
          "answers",
          answersOk,
          answersOk
            ? `answers_sha256 ${answersHash}`
            : `answers_sha256 ${predicate?.answers_sha256 || "(missing)"} != recomputed ${answersHash}`,
        );
      }
    } else {
      push(findings, "record", false, "no record supplied; subject digest was not recomputed");
      push(findings, "answers", false, "no record supplied; answers_sha256 was not recomputed");
    }

    findings.push(...(await checkBinding(predicate?.model_binding, options.record, predicate?.claimed_model)));
    const command = fullRecheckCommand(predicate?.sitting_id);
    push(findings, "not_checked", true, `not checked in browser: ${command}`);
    return {
      ok: findings.every((item) => item.ok),
      findings,
      predicate,
      statement,
      fingerprint: opened.fingerprint,
      notChecked: [command],
    };
  } catch (err) {
    const command = fullRecheckCommand();
    return {
      ok: false,
      findings: [{ field: "receipt", ok: false, detail: `verifier error: ${err.message}` }],
      predicate: null,
      statement: null,
      fingerprint: null,
      notChecked: [command],
    };
  }
}

export const formats = {
  PAYLOAD_TYPE,
  STATEMENT_TYPE,
  PREDICATE_TYPE,
  RECEIPT_FORMAT,
  BINDING_FORMAT,
  PUBKEY_FORMAT,
};
