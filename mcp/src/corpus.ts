// The baked OPERANT corpus + typed accessors.
// corpus.generated.ts is produced by scripts/build-corpus.mjs from the sibling
// data files; a placeholder is committed so the project type-checks before the
// first build:corpus run.

import generated from "./corpus.generated";
import type {
	AxisFile,
	AxisName,
	CalibrationProfile,
	CasePair,
	CaseRecord,
	OperantCorpus,
} from "./types";

export const corpus: OperantCorpus = generated;

export const allProfiles = (): CalibrationProfile[] =>
	corpus.calibration.models;

export const axisFile = (axis: AxisName): AxisFile => corpus.axes[axis];

export const axisCaseItems = (axis: AxisName): (CasePair | CaseRecord)[] => {
	const f = corpus.axes[axis];
	return (f.pairs ?? f.cases ?? []) as (CasePair | CaseRecord)[];
};

export * from "./types";
