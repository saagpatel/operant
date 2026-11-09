// Displayed row vs signed predicate. The instruments table renders a row only
// when the displayed fields match and the record is publishable.

import { JsonNumber } from "./verify.mjs";

function integerValue(value) {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) ? value : undefined;
  }
  if (value instanceof JsonNumber) {
    if (value.float || !Number.isSafeInteger(value.number)) return undefined;
    const match = /^(-?)(0|[1-9]\d*)$/.exec(value.literal);
    if (!match) return undefined;
    const digits = match[2];
    const limit = "9007199254740991";
    if (digits.length > limit.length || (digits.length === limit.length && digits > limit)) return undefined;
    if (digits === "0") return 0;
    return value.number;
  }
  return undefined;
}

function sameValue(displayed, signed) {
  const leftNumeric = typeof displayed === "number" || displayed instanceof JsonNumber;
  const rightNumeric = typeof signed === "number" || signed instanceof JsonNumber;
  if (leftNumeric || rightNumeric) {
    const left = integerValue(displayed);
    const right = integerValue(signed);
    return left !== undefined && right !== undefined && left === right;
  }
  return Object.is(displayed, signed);
}

export const DISPLAY_FIELDS = [
  ["sitting_id", (predicate) => predicate.sitting_id],
  ["agent_label", (predicate) => predicate.agent_label],
  ["claimed_model", (predicate) => predicate.claimed_model],
  ["observed_model", (predicate) => predicate.model_binding?.observed_model],
  ["ocs", (predicate) => predicate.score?.ocs],
  ["accuracy", (predicate) => predicate.score?.accuracy],
  ["n_scored", (predicate) => predicate.score?.n_scored],
  ["n_cases", (predicate) => predicate.score?.n_cases],
  ["n_bypass_failures", (predicate) => predicate.score?.n_bypass_failures],
  ["band", (predicate) => predicate.score?.band],
  ["binding_level", (predicate) => predicate.model_binding?.level],
  ["sealed_at", (predicate) => predicate.sealed_at],
];

export function rowMatchesPredicate(row, predicate) {
  if (!row || typeof row !== "object" || !predicate || typeof predicate !== "object") return false;
  return DISPLAY_FIELDS.every(([field, read]) => sameValue(row[field], read(predicate)));
}

export function recordIsPublishable(record) {
  const score = record?.score;
  if (!score || typeof score !== "object" || Array.isArray(score)) return false;
  if (score.publishable !== true) return false;
  return Array.isArray(score.not_publishable_reasons) && score.not_publishable_reasons.length === 0;
}

export function rowAccepted(row, predicate, record) {
  if (!rowMatchesPredicate(row, predicate) || !recordIsPublishable(record)) return false;
  if (!recordIsPublishable({ score: predicate.score })) return false;
  return DISPLAY_FIELDS.every(([field]) => {
    if (field === "sealed_at") return true;
    return sameValue(recordValue(record, field), row[field]);
  });
}

function recordValue(record, field) {
  if (field === "observed_model" || field === "binding_level") {
    const binding = record?.model_binding;
    return field === "observed_model" ? binding?.observed_model : binding?.level;
  }
  if (field === "ocs" || field === "accuracy" || field === "n_scored" || field === "n_cases" || field === "n_bypass_failures" || field === "band") {
    return record?.score?.[field];
  }
  return record?.[field];
}
