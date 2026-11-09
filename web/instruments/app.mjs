import { el, clear } from "../lib/dom.mjs";
import { rowMatchesPredicate } from "../lib/rows.mjs";
import { SITTING_ID, displayScalar, parseJson, verifySignature } from "../lib/verify.mjs";

const COLUMNS = [
  ["agent_label", "Agent"],
  ["claimed_model", "Claimed model"],
  ["ocs", "OCS"],
  ["tpr", "TPR"],
  ["fpr", "FPR"],
  ["band", "Band"],
  ["binding_level", "Binding"],
  ["sealed_at", "Sealed"],
];

const main = document.querySelector("main");

function show(node) {
  clear(main);
  main.append(node);
}

function receiptUrl(row) {
  if (!row || typeof row.sitting_id !== "string" || !SITTING_ID.test(row.sitting_id)) return null;
  const expected = `receipts/${row.sitting_id}.receipt.json`;
  if (row.receipt !== expected) return null;
  return `../data/${expected}`;
}

function bindingCell(row, predicate) {
  const binds = predicate?.model_binding?.binds;
  const tip = Array.isArray(binds) ? binds.filter((item) => typeof item === "string").join("\n") : "";
  const button = el("button", {
    className: "level",
    text: displayScalar(row.binding_level),
    title: tip,
  });
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  const wrap = el("span", { className: "binding" }, [button]);
  if (tip) {
    const tipNode = el("span", { className: "tip", text: tip });
    wrap.append(tipNode);
    button.addEventListener("click", () => {
      const open = wrap.classList.toggle("open");
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  return wrap;
}

function renderTable(passed, refused) {
  clear(main);
  if (passed.length === 0) {
    main.append(el("p", { className: "empty", text: "No row passed the seal check." }));
  } else {
    const table = el("table");
    const caption = el("caption", { className: "kicker", text: "Sealed sittings" });
    table.append(caption);
    const thead = el("thead");
    const headRow = el("tr");
    for (const [, label] of COLUMNS) headRow.append(el("th", { text: label, attrs: { scope: "col" } }));
    headRow.append(el("th", { text: "Seal", attrs: { scope: "col" } }));
    thead.append(headRow);
    const tbody = el("tbody");
    for (const { row, predicate } of passed) {
      const tr = el("tr");
      for (const [key, label] of COLUMNS) {
        const td = el("td", { attrs: { "data-label": label } });
        if (key === "binding_level") td.append(bindingCell(row, predicate));
        else td.append(el("span", { className: "cell-value", text: displayScalar(row[key]) }));
        tr.append(td);
      }
      const seal = el("td", { attrs: { "data-label": "Seal" } });
      const link = el("a", {
        className: "seal",
        text: "sealed",
        attrs: { href: `../verify/?receipt=${encodeURIComponent(row.sitting_id)}` },
      });
      seal.append(link);
      tr.append(seal);
      tbody.append(tr);
    }
    table.append(thead, tbody);
    main.append(table);
  }
  main.append(el("p", { className: "refused", text: `${refused} rows refused (failed seal check)` }));
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
    show(el("p", { className: "empty", text: "No sealed sittings yet." }));
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
    const url = receiptUrl(row);
    if (!url) return null;
    try {
      const receipt = parseJson(await loadText(url));
      const sig = await verifySignature(receipt, pubkeys);
      if (!sig.ok || !rowMatchesPredicate(row, sig.predicate)) return null;
      return { row, predicate: sig.predicate };
    } catch {
      return null;
    }
  }));
  const passed = checked.filter(Boolean);
  renderTable(passed, rowsDoc.rows.length - passed.length);
}

mainFlow();
