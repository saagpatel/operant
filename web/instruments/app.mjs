import { el, clear } from "../lib/dom.mjs";
import { rowAccepted } from "../lib/rows.mjs";
import { SITTING_ID, displayRate, displayScalar, parseJson, verifySignature } from "../lib/verify.mjs";

const COLUMNS = [
  ["agent_label", "Agent"],
  ["claimed_model", "Claimed model"],
  ["observed_model", "Observed model"],
  ["ocs", "OCS"],
  ["accuracy", "Accuracy"],
  ["coverage", "Coverage"],
  ["n_bypass_failures", "Bypass failures"],
  ["band", "Band"],
  ["binding_level", "Binding"],
  ["sealed_at", "Sealed"],
];

const main = document.querySelector("main");

function show(node) {
  clear(main);
  main.append(node);
}

function publishedUrl(row, kind) {
  if (!row || typeof row.sitting_id !== "string" || !SITTING_ID.test(row.sitting_id)) return null;
  const expected = `${kind}/${row.sitting_id}.${kind === "receipts" ? "receipt" : "record"}.json`;
  const field = kind === "receipts" ? row.receipt : row.record;
  if (field !== expected) return null;
  return `../data/${expected}`;
}

function cellText(row, key) {
  if (key === "ocs" || key === "accuracy") return displayRate(row[key]);
  if (key === "coverage") return `${displayScalar(row.n_scored)}/${displayScalar(row.n_cases)}`;
  if (key === "n_bypass_failures") return displayScalar(row.n_bypass_failures);
  if (key === "observed_model") return row.observed_model == null ? "not observed" : displayScalar(row.observed_model);
  return displayScalar(row[key]);
}

function bindingCell(row, predicate) {
  const binding = predicate?.model_binding;
  const binds = Array.isArray(binding?.binds) ? binding.binds.filter((item) => typeof item === "string") : [];
  const limits = Array.isArray(binding?.does_not_bind) ? binding.does_not_bind.filter((item) => typeof item === "string") : [];
  const tip = [`Binds:`, ...(binds.length ? binds : ["(none listed)"]), `Does not bind:`, ...(limits.length ? limits : ["(none listed)"])].join("\n");
  const button = el("button", {
    className: "level",
    text: displayScalar(row.binding_level),
    title: tip,
  });
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  const wrap = el("span", { className: "binding" }, [button]);
  const tipNode = el("span", { className: "tip", text: tip });
  wrap.append(tipNode);
  button.addEventListener("click", () => {
    const open = wrap.classList.toggle("open");
    button.setAttribute("aria-expanded", open ? "true" : "false");
  });
  return wrap;
}

function renderTable(passed, refused) {
  clear(main);
  if (passed.length === 0) {
    main.append(el("p", { className: "empty", text: "No row passed the receipt check." }));
  } else {
    const table = el("table");
    const caption = el("caption", { className: "kicker", text: "Published sittings" });
    table.append(caption);
    const thead = el("thead");
    const headRow = el("tr");
    for (const [, label] of COLUMNS) headRow.append(el("th", { text: label, attrs: { scope: "col" } }));
    headRow.append(el("th", { text: "Receipt", attrs: { scope: "col" } }));
    thead.append(headRow);
    const tbody = el("tbody");
    for (const { row, predicate } of passed) {
      const tr = el("tr");
      for (const [key, label] of COLUMNS) {
        const td = el("td", { attrs: { "data-label": label } });
        if (key === "binding_level") td.append(bindingCell(row, predicate));
        else td.append(el("span", { className: "cell-value", text: cellText(row, key) }));
        tr.append(td);
      }
      const linkCell = el("td", { attrs: { "data-label": "Receipt" } });
      const link = el("a", {
        className: "seal",
        text: "receipt",
        attrs: { href: `../verify/?receipt=${encodeURIComponent(row.sitting_id)}` },
      });
      linkCell.append(link);
      tr.append(linkCell);
      tbody.append(tr);
    }
    table.append(thead, tbody);
    main.append(table);
  }
  main.append(el("p", { className: "refused", text: `${refused} rows refused (receipt check or not publishable)` }));
}

async function loadText(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(String(res.status));
  return res.text();
}

async function mainFlow() {
  let rowsDoc;
  try {
    rowsDoc = parseJson(await loadText("../data/rows.json"));
  } catch {
    show(el("p", { className: "empty", text: "The row list could not be loaded." }));
    return;
  }
  if (!rowsDoc || rowsDoc.format !== "operant.instruments-rows/v1" || !Array.isArray(rowsDoc.rows)) {
    show(el("p", { className: "empty", text: "The row list is not operant.instruments-rows/v1." }));
    return;
  }
  if (rowsDoc.rows.length === 0) {
    show(el("p", { className: "empty", text: "No published sittings yet." }));
    return;
  }

  let pubkeys = { format: "operant.sittings-pubkey/v1", keys: [] };
  try {
    const parsed = parseJson(await loadText("../data/sittings-pubkey.json"));
    if (parsed && parsed.format === "operant.sittings-pubkey/v1" && Array.isArray(parsed.keys)) pubkeys = parsed;
  } catch {
    pubkeys = { format: "operant.sittings-pubkey/v1", keys: [] };
  }

  const checked = await Promise.all(rowsDoc.rows.map(async (row) => {
    const receiptUrl = publishedUrl(row, "receipts");
    const recordUrl = publishedUrl(row, "records");
    if (!receiptUrl || !recordUrl) return null;
    try {
      const receipt = parseJson(await loadText(receiptUrl));
      const record = parseJson(await loadText(recordUrl));
      const sig = await verifySignature(receipt, pubkeys);
      if (!sig.ok || !rowAccepted(row, sig.predicate, record)) return null;
      return { row, predicate: sig.predicate };
    } catch {
      return null;
    }
  }));
  const passed = checked.filter(Boolean);
  renderTable(passed, rowsDoc.rows.length - passed.length);
}

mainFlow();
