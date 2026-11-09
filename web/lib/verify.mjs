// Browser and Node verifier for operant.sitting-receipt/v1.
//
// Canonical JSON matches sittings/canonical.py:
//   json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
// Keys in this format are ASCII. Sorting still uses Unicode code points so an
// astral key cannot drift from Python the way UTF-16 code-unit order would.
//
// DSSE PAE and the base64 helper are copied from
// ~/Projects/checkseal/js/checkseal_verify.mjs (CheckSeal's browser verifier).
// That file imports a raw 32-byte Ed25519 key. Here the published key is SPKI,
// so the verify step uses importKey("spki", ..., { name: "Ed25519" }).

const PAYLOAD_TYPE = "application/vnd.in-toto+json";
const STATEMENT_TYPE = "https://in-toto.io/Statement/v1";
const PREDICATE_TYPE = "https://operant.saagarpatel.dev/sitting-receipt/v1";
const RECEIPT_FORMAT = "operant.sitting-receipt/v1";
const BINDING_FORMAT = "operant.model-binding/v1";
const PUBKEY_FORMAT = "operant.sittings-pubkey/v1";

// Level names from the shared contract. Packet 02 may refine this set; an
// unknown level fails closed rather than being displayed as a stronger claim.
const BINDING_LEVELS = new Set([
  "relay-attested",
  "relay-observed",
  "fingerprint-consistent",
  "unbound",
]);

export const SITTING_ID = /^sit_[a-z2-7]{26}$/;

const textEncoder = new TextEncoder();

export class JsonNumber {
  constructor(literal) {
    this.literal = literal;
    this.float = /[.eE]/.test(literal);
    this.number = Number(literal);
  }
}

// --- canonical JSON -------------------------------------------------------

function formatPythonFloat(n) {
  // Match CPython json.dumps for a float: shortest round-trip, scientific
  // notation when the decimal point is <= -4 or > 16, exponent sign and at
  // least two exponent digits, and ".0" on a whole number (Py_DTSF_ADD_DOT_0).
  if (Object.is(n, -0)) return "-0.0";
  if (!Number.isFinite(n)) throw new TypeError("non-finite numbers are not canonical JSON");
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  const [mant, expPart] = abs.toExponential().split("e");
  const exp = Number(expPart);
  const digits = mant.replace(".", "");
  const decpt = exp + 1;
  if (decpt <= -4 || decpt > 16) {
    let mantissa = digits[0];
    if (digits.length > 1) mantissa += `.${digits.slice(1)}`;
    const esign = exp < 0 ? "-" : "+";
    const eabs = String(Math.abs(exp)).padStart(2, "0");
    return `${sign}${mantissa}e${esign}${eabs}`;
  }
  let out;
  if (decpt <= 0) out = `0.${"0".repeat(-decpt)}${digits}`;
  else if (decpt >= digits.length) out = `${digits}${"0".repeat(decpt - digits.length)}.0`;
  else out = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  return sign + out;
}

function encodeString(s) {
  let out = '"';
  for (let i = 0; i < s.length; ) {
    const cp = s.codePointAt(i);
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

function encodeNumber(value) {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("non-finite numbers are not canonical JSON");
    // A plain JS number has no int/float tag. Safe integers follow Python ints
    // (`1`). Floats follow Python floats (`0.5`, `1.0`). JSON text keeps the
    // tag via JsonNumber, which is how a record file's `0.0` stays `0.0`.
    if (!Object.is(value, -0) && Number.isSafeInteger(value)) return String(value);
    return formatPythonFloat(value);
  }
  if (!value.float) {
    if (Number.isSafeInteger(value.number)) return String(value.number);
    const lit = value.literal;
    const m = /^(-?)0*(\d+)$/.exec(lit);
    if (!m) throw new TypeError(`not an integer literal: ${lit}`);
    const digits = m[2].replace(/^0+(?=\d)/, "");
    return `${m[1]}${digits}`;
  }
  return formatPythonFloat(value.number);
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
    const keys = Object.keys(value).sort(compareCodePoints);
    const parts = keys.map((key) => {
      if (value[key] === undefined) throw new TypeError(`undefined at ${key}`);
      return `${encodeString(key)}:${canonicalJSON(value[key])}`;
    });
    return `{${parts.join(",")}}`;
  }
  throw new TypeError(`cannot canonicalize ${typeof value}`);
}

export function parseJson(text) {
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

function trustedKeys(pubkeys) {
  const list = Array.isArray(pubkeys) ? pubkeys : pubkeys?.keys;
  if (!Array.isArray(list)) return [];
  return list.filter((key) => key && typeof key.keyid === "string" && key.alg === "ed25519");
}

function spkiBytes(key) {
  if (typeof key.spki_der_b64 === "string" && key.spki_der_b64.length > 0) {
    return decodeB64(key.spki_der_b64);
  }
  if (typeof key.public_key_pem === "string" && key.public_key_pem.includes("BEGIN")) {
    return pemToSpki(key.public_key_pem);
  }
  throw new Error("missing SPKI public key");
}

async function signatureMatches(payloadType, payloadBytes, signatures, keyid, keys) {
  const signed = pae(payloadType, payloadBytes);
  const candidates = (signatures || []).filter((sig) => sig && sig.keyid === keyid && typeof sig.sig === "string");
  if (candidates.length === 0) return { ok: false, detail: "no signature for the receipt keyid" };
  let last = "Ed25519 signature did not match the trusted key";
  for (const key of keys) {
    let cryptoKey;
    try {
      cryptoKey = await crypto.subtle.importKey("spki", spkiBytes(key), { name: "Ed25519" }, false, ["verify"]);
    } catch (err) {
      last = `Ed25519 public key could not be imported (${err.name || "Error"})`;
      continue;
    }
    for (const sig of candidates) {
      let sigBytes;
      try {
        sigBytes = decodeB64(sig.sig);
      } catch {
        last = "signature is not base64";
        continue;
      }
      const ok = await crypto.subtle.verify({ name: "Ed25519" }, cryptoKey, sigBytes, signed);
      if (ok) return { ok: true, detail: `Ed25519 signature over the DSSE PAE matches keyid ${keyid}` };
      last = "Ed25519 signature did not match the trusted key";
    }
  }
  return { ok: false, detail: last };
}

export function fullRecheckCommand(sittingId) {
  if (typeof sittingId === "string" && SITTING_ID.test(sittingId)) {
    return `uv run python -m sittings.receipt verify web/data/receipts/${sittingId}.receipt.json`;
  }
  return "uv run python -m sittings.receipt verify RECEIPT.json";
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
  const n = value instanceof JsonNumber ? value.number : value;
  if (typeof n !== "number" || !Number.isSafeInteger(n) || n < 0) return undefined;
  return n;
}

async function checkManifest(field, manifest, fetchBlob) {
  if (!manifest || typeof manifest !== "object" || !Array.isArray(manifest.files)) {
    return { field, ok: false, detail: `${field} manifest is missing` };
  }
  const files = manifest.files;
  const seen = new Set();
  for (const file of files) {
    if (!file || typeof file.path !== "string" || file.path.length === 0 || file.path.includes("\u0000")) {
      return { field, ok: false, detail: `${field} file entry has no path` };
    }
    if (!/^[0-9a-f]{64}$/.test(file.sha256 || "")) {
      return { field, ok: false, detail: `${field} file ${file.path} has no sha256` };
    }
    if (countOf(file.bytes) === undefined) {
      return { field, ok: false, detail: `${field} file ${file.path} has no byte length` };
    }
    if (seen.has(file.path)) return { field, ok: false, detail: `${field} lists ${file.path} more than once` };
    seen.add(file.path);
  }
  const pairs = files.map((file) => ({ path: file.path, sha256: file.sha256 }));
  pairs.sort((a, b) => compareCodePoints(a.path, b.path));
  const aggregate = await canonicalSha256(pairs);
  if (aggregate !== manifest.sha256) {
    return {
      field,
      ok: false,
      detail: `${field} aggregate ${manifest.sha256 || "(missing)"} does not match recomputed ${aggregate}`,
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

function stringList(value) {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

async function checkBinding(binding) {
  const findings = [];
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
  const levelKnown = BINDING_LEVELS.has(binding.level);
  push(
    findings,
    "model_binding.level",
    levelKnown,
    levelKnown ? `binding level: ${binding.level}` : `unknown binding level ${JSON.stringify(binding.level)}`,
  );
  let evidenceOk = false;
  let evidenceDetail = "evidence could not be hashed";
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
  push(
    findings,
    "model_binding.binds",
    stringList(binding.binds),
    stringList(binding.binds) ? binding.binds.join("\n") : "binds is not a list of strings",
  );
  push(
    findings,
    "model_binding.does_not_bind",
    stringList(binding.does_not_bind),
    stringList(binding.does_not_bind) ? binding.does_not_bind.join("\n") : "does_not_bind is not a list of strings",
  );
  return findings;
}

async function openReceipt(receipt, pubkeys) {
  const findings = [];
  let payloadBytes = null;
  let payloadText = "";
  let statement = null;
  let predicate = null;

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

  const keyid = typeof receipt?.keyid === "string" ? receipt.keyid : "";
  const keys = trustedKeys(pubkeys).filter((key) => key.keyid === keyid);
  if (keyid && keys.length > 0) push(findings, "keyid", true, `keyid ${keyid} is in the trusted set`);
  else push(findings, "keyid", false, keyid ? `keyid ${keyid} is not in the trusted set` : "receipt has no keyid");

  if (payloadBytes && keys.length > 0) {
    try {
      const sig = await signatureMatches(
        receipt.envelope.payloadType,
        payloadBytes,
        receipt.envelope.signatures,
        keyid,
        keys,
      );
      push(findings, "signature", sig.ok, sig.detail);
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
  const digestOk = typeof digest === "string" && /^[0-9a-f]{64}$/.test(digest);
  push(
    findings,
    "subject",
    Boolean(nameOk && digestOk),
    nameOk && digestOk ? `subject ${subject.name} digest ${digest}` : "subject name or digest is missing",
  );

  return { findings, statement, predicate, payloadBytes };
}

export async function verifySignature(receipt, pubkeys) {
  try {
    const opened = await openReceipt(receipt, pubkeys);
    const ok = opened.findings.every((item) => item.ok);
    return { ok, findings: opened.findings, predicate: opened.predicate };
  } catch (err) {
    return {
      ok: false,
      findings: [{ field: "receipt", ok: false, detail: `verifier error: ${err.message}` }],
      predicate: null,
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
        const recordOk = subjectHash === recordHash;
        push(
          findings,
          "record",
          recordOk,
          recordOk
            ? `canonical record sha256 ${recordHash}`
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

    findings.push(...(await checkBinding(predicate?.model_binding)));
    const command = fullRecheckCommand(predicate?.sitting_id);
    push(
      findings,
      "not_checked",
      true,
      `Score re-execution is not done in the browser. It needs the Python scorer. Run: ${command}`,
    );
    return {
      ok: findings.every((item) => item.ok),
      findings,
      predicate,
      statement,
      notChecked: [command],
    };
  } catch (err) {
    const command = fullRecheckCommand();
    return {
      ok: false,
      findings: [{ field: "receipt", ok: false, detail: `verifier error: ${err.message}` }],
      predicate: null,
      statement: null,
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
