// Displayed row vs signed predicate. The instruments table renders a row only
// when this matches and the receipt signature checks.

import { JsonNumber } from "./verify.mjs";

function numberOf(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value instanceof JsonNumber && Number.isFinite(value.number)) return value.number;
  return undefined;
}

function sameValue(displayed, signed) {
  const left = numberOf(displayed);
  const right = numberOf(signed);
  if (left !== undefined || right !== undefined) {
    return left !== undefined && right !== undefined && left === right;
  }
  return typeof displayed === "string" && typeof signed === "string" && displayed === signed;
}

const FIELDS = [
  ["sitting_id", (predicate) => predicate.sitting_id],
  ["agent_label", (predicate) => predicate.agent_label],
  ["claimed_model", (predicate) => predicate.claimed_model],
  ["ocs", (predicate) => predicate.score?.ocs],
  ["tpr", (predicate) => predicate.score?.tpr],
  ["fpr", (predicate) => predicate.score?.fpr],
  ["band", (predicate) => predicate.score?.band],
  ["binding_level", (predicate) => predicate.model_binding?.level],
  ["sealed_at", (predicate) => predicate.sealed_at],
];

export function rowMatchesPredicate(row, predicate) {
  if (!row || typeof row !== "object" || !predicate || typeof predicate !== "object") return false;
  return FIELDS.every(([field, read]) => sameValue(row[field], read(predicate)));
}
