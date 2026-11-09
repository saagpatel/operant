import { el, clear } from "../lib/dom.mjs";
import {
  RECEIPT_WORDING,
  SITTING_ID,
  displayRate,
  displayScalar,
  fullRecheckCommand,
  isRateKey,
  parseJson,
  verifyReceipt,
} from "../lib/verify.mjs";

const keyNotice = document.querySelector("#key-notice");
const form = document.querySelector("#check");
const receiptText = document.querySelector("#receipt-text");
const recordText = document.querySelector("#record-text");
const result = document.querySelector("#result");

let pubkeys = { format: "operant.sittings-pubkey/v1", keys: [] };

function readFile(input, target) {
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    target.value = await file.text();
  });
}

readFile(document.querySelector("#receipt-file"), receiptText);
readFile(document.querySelector("#record-file"), recordText);

async function fetchBlob(sha256) {
  if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error("blob name is not a sha256");
  const res = await fetch(`../data/blobs/${sha256}`);
  if (!res.ok) throw new Error(`blob ${sha256} is not published (${res.status})`);
  return new Uint8Array(await res.arrayBuffer());
}

function addPair(dl, label, value) {
  const wrap = el("div", { className: "pair" });
  wrap.append(el("dt", { text: label }));
  wrap.append(el("dd", { text: value == null ? "" : String(value) }));
  dl.append(wrap);
}

function renderList(parent, title, items) {
  parent.append(el("h3", { text: title }));
  const ul = el("ul", { className: "lists" });
  if (!Array.isArray(items) || items.length === 0) {
    ul.append(el("li", { text: "(none listed)" }));
  } else {
    for (const item of items) {
      ul.append(el("li", { text: typeof item === "string" ? item : displayScalar(item) }));
    }
  }
  parent.append(ul);
}

function renderHashes(parent, predicate, statement) {
  parent.append(el("h2", { text: "Bound hashes" }));
  const dl = el("dl");
  addPair(dl, "Corpus aggregate", predicate?.corpus?.sha256);
  for (const file of predicate?.corpus?.files || []) {
    if (!file || typeof file !== "object") continue;
    addPair(dl, file.path, `${file.sha256} (${displayScalar(file.bytes)} bytes)`);
  }
  addPair(dl, "Scorer aggregate", predicate?.scorer?.sha256);
  for (const file of predicate?.scorer?.files || []) {
    if (!file || typeof file !== "object") continue;
    addPair(dl, file.path, `${file.sha256} (${displayScalar(file.bytes)} bytes)`);
  }
  addPair(dl, "Answers", predicate?.answers_sha256);
  addPair(dl, "Subject", statement?.subject?.[0]?.digest?.sha256);
  parent.append(dl);
}

function scoreText(key, value) {
  if (isRateKey(key)) return displayRate(value);
  return displayScalar(value);
}

function renderScore(parent, score) {
  parent.append(el("h2", { text: "Signed score" }));
  parent.append(el("p", { className: "note", text: "Rescoring is not checked in browser." }));
  if (!score || typeof score !== "object") {
    parent.append(el("p", { text: "No score in the signed predicate." }));
    return;
  }
  const order = [
    "ocs", "accuracy", "tpr", "fpr", "safe_correct_rate",
    "n_scored", "n_cases", "n_unanswered", "n_unparseable", "n_bypass_failures",
    "band", "publishable",
  ];
  const keys = [
    ...order.filter((key) => key in score),
    ...Object.keys(score).filter((key) => !order.includes(key) && key !== "not_publishable_reasons").sort(),
  ];
  const dl = el("dl");
  for (const key of keys) addPair(dl, key, scoreText(key, score[key]));
  parent.append(dl);
  const reasons = score.not_publishable_reasons;
  if (Array.isArray(reasons) && reasons.length > 0) {
    renderList(parent, "not_publishable_reasons", reasons);
  }
}

function renderResult(report) {
  clear(result);
  const failed = report.findings.filter((item) => !item.ok).length;
  const summary = el("p", {
    className: report.ok ? "summary" : "summary bad",
    text: report.ok
      ? "All browser checks passed. Rescoring is not checked in browser."
      : `${failed} browser check${failed === 1 ? "" : "s"} failed.`,
  });
  result.append(summary);
  result.append(el("p", { text: RECEIPT_WORDING }));

  result.append(el("h2", { text: "Key fingerprint" }));
  result.append(el("p", {
    className: "fingerprint",
    text: report.fingerprint || "(fingerprint not agreed)",
  }));

  result.append(el("h2", { text: "Checks" }));
  const list = el("ul", { className: "findings" });
  for (const item of report.findings) {
    const note = item.field === "not_checked";
    const kind = note ? "note-row" : item.ok ? "pass" : "fail";
    const mark = note ? "note" : item.ok ? "pass" : "fail";
    const li = el("li", { className: kind });
    li.append(el("span", { className: "mark", text: mark }));
    li.append(el("span", { className: "field", text: note ? "not checked in browser" : item.field }));
    li.append(el("span", { className: "detail", text: item.detail }));
    list.append(li);
  }
  result.append(list);

  const predicate = report.predicate;
  renderHashes(result, predicate, report.statement);
  renderScore(result, predicate?.score);

  result.append(el("h2", { text: "Model binding" }));
  const binding = predicate?.model_binding;
  const level = binding && typeof binding.level === "string" ? binding.level : "(missing)";
  result.append(el("p", { text: `Binding level: ${level}` }));
  if (level === "host-relayed") {
    result.append(el("p", {
      className: "note",
      text: "Host-relayed answers were compared in the browser with the recorded completion text of each relay call, and the evidence hashes were recomputed.",
    }));
  }
  renderList(result, "Binds", binding?.binds);
  renderList(result, "Does not bind", binding?.does_not_bind);

  result.append(el("h2", { text: "What this receipt binds" }));
  renderList(result, "Binds", predicate?.seal_binds);
  renderList(result, "Does not bind", predicate?.seal_does_not_bind);

  result.append(el("h2", { text: "Full re-check" }));
  result.append(el("p", { text: "Rescoring is not checked in browser." }));
  result.append(el("pre", { className: "command", text: fullRecheckCommand(predicate?.sitting_id) }));
}

async function checkCurrent() {
  clear(result);
  if (!receiptText.value.trim()) {
    result.append(el("p", { className: "summary bad", text: "Paste a receipt first." }));
    return;
  }
  let receipt;
  try {
    receipt = parseJson(receiptText.value);
  } catch (err) {
    result.append(el("p", { className: "summary bad", text: `Receipt JSON could not be read (${err.message}).` }));
    return;
  }
  let record;
  if (recordText.value.trim()) {
    try {
      record = parseJson(recordText.value);
    } catch (err) {
      result.append(el("p", { className: "summary bad", text: `Record JSON could not be read (${err.message}).` }));
      return;
    }
  }
  const report = await verifyReceipt(receipt, { pubkeys, fetchBlob, record });
  renderResult(report);
}

const starting = start();

form.addEventListener("submit", (event) => {
  event.preventDefault();
  starting.then(() => checkCurrent());
});

async function loadPublished(id) {
  if (!SITTING_ID.test(id)) {
    result.append(el("p", { className: "summary bad", text: "Receipt ids look like sit_ followed by 26 lowercase base32 characters." }));
    return;
  }
  try {
    const res = await fetch(`../data/receipts/${id}.receipt.json`);
    if (!res.ok) throw new Error(String(res.status));
    receiptText.value = await res.text();
  } catch {
    result.append(el("p", { className: "note", text: "That receipt is not in the published data directory. You can paste one below." }));
    return;
  }
  try {
    const res = await fetch(`../data/records/${id}.record.json`);
    if (res.ok) recordText.value = await res.text();
  } catch {
    recordText.value = "";
  }
  await checkCurrent();
}

async function start() {
  try {
    const res = await fetch("../data/sittings-pubkey.json");
    if (!res.ok) throw new Error(String(res.status));
    const parsed = parseJson(await res.text());
    if (!parsed || parsed.format !== "operant.sittings-pubkey/v1" || !Array.isArray(parsed.keys)) {
      throw new Error("format");
    }
    pubkeys = parsed;
    keyNotice.textContent = "Checking signatures against the published key set.";
  } catch {
    keyNotice.textContent = "No published signing key was loaded. Signatures will fail until the key set is available.";
  }
  const id = new URLSearchParams(location.search).get("receipt");
  if (id) await loadPublished(id);
}
