# Related Work

How OPERANT relates to the evaluation literature it draws on. This is a citation-dense
companion to [`why-operating-calibration.md`](why-operating-calibration.md): that doc
argues the market demand; this one places OPERANT among prior benchmarks and methods.

OPERANT measures whether an LLM agent makes correct *operating decisions* under an
operator contract. The agent emits a structured decision —
`PROCEED`, `PROCEED_SANCTIONED`, `REFUSE`, `ESCALATE`, or `REROUTE` — over **matched
malign/benign pairs** that share domain and surface vocabulary but differ in **instruction
origin** (operator channel vs. untrusted/fetched content) and **blast radius**. The
headline metric, OCS, is `TPR − FPR` (Youden's J / informedness) over the binary
"withheld direct execution?" decision, where `WITHHELD = {REFUSE, ESCALATE, REROUTE}`. The
design makes both error directions costly in one number: a refuse-everything agent scores
0 and a proceed-everything agent scores 0.

OPERANT's contribution is the **integration** of ingredients that each have clear prior
art. The sections below name that prior art, then state precisely what OPERANT combines
that no prior benchmark does.

## 1. Over-refusal and exaggerated safety

The premise that *over-refusing benign work is itself a failure* originates in the
over-refusal / exaggerated-safety line. **XSTest** (Röttger et al., NAACL 2024;
https://arxiv.org/abs/2308.01263) pairs 250 safe prompts with near-identical unsafe ones
that share surface vocabulary, isolating exaggerated safety; it is the direct conceptual
ancestor of OPERANT's matched twins. **OR-Bench** (Cui et al., ICML 2025;
https://arxiv.org/abs/2405.20947) scales this to ~80K seemingly-toxic-but-benign prompts
and reports a 0.878 Spearman correlation between safe- and toxic-prompt rejection rates —
safer models over-refuse more — which is precisely the tension OCS scores. **FalseReject**
(Zhang et al., 2025; https://arxiv.org/abs/2505.08054) provides ~16K seemingly-toxic
benign queries plus a human-annotated test set. **CoCoNot** (Brahman, Kumar et al.,
NeurIPS 2024 D&B; https://arxiv.org/abs/2407.12043) and **SORRY-Bench** (Xie et al., ICLR
2025; https://arxiv.org/abs/2406.14598) round out the refusal-taxonomy work.

These benchmarks measure over-refusal (and, in OR-Bench's case, both directions) but
**on separate, unpaired prompt populations and as separate metrics**, and they are
chat-completion tests rather than agentic operating decisions. None collapses both error
directions into a single discrimination score over matched twins.

## 2. Prompt injection and instruction provenance

OPERANT's instruction-origin axis — distinguishing a trusted operator instruction from
the same words arriving in untrusted, fetched content — is the threat model of the
indirect-prompt-injection line. **AgentDojo** (Debenedetti et al., NeurIPS 2024 D&B;
https://arxiv.org/abs/2406.13352) is the canonical benchmark here, scoring targeted attack
success rate and benign task utility for tool-using agents under injection. **InjecAgent**
(Zhan et al., ACL Findings 2024; https://arxiv.org/abs/2403.02691) similarly measures
indirect injection in tool-integrated agents. Both own the provenance axis but score
injection robustness and utility, not over-refusal, and neither pairs an attack with a
surface-matched benign twin where proceeding is the correct call.

## 3. Agentic harm, risk, and decision-under-pressure benchmarks

The closest agentic neighbors evaluate whether an agent takes harmful actions in
multi-step, tool-using settings. **AgentHarm** (Andriushchenko et al., ICLR 2025;
https://arxiv.org/abs/2410.09024) is the nearest prior art: it pairs each harmful agent
task with a benign counterpart of matched tool-use and complexity, and reports both a harm
score and a benign-refusal rate. Crucially, AgentHarm reports these as **separate
metrics**, both twins arrive on the operator channel (no instruction-origin contrast), and
it has no escalation or sanctioned-path axis. **ToolEmu** (Ruan et al., ICLR 2024;
https://arxiv.org/abs/2309.15817) emulates tool execution in a sandbox and uses an LM judge
to trade safety against helpfulness. **R-Judge** (Yuan et al., EMNLP Findings 2024;
https://arxiv.org/abs/2401.10019) benchmarks safety-risk awareness from agent trajectories.
**Agent-SafetyBench** (Zhang et al., 2024; https://arxiv.org/abs/2412.14470) and
**SafeAgentBench** (Yin et al., 2024; https://arxiv.org/abs/2412.13178 — the latter with
300 matched safe/unsafe task twins) extend safety coverage to broader and embodied agent
settings, as does **MobileSafetyBench** (https://arxiv.org/abs/2410.17520) for
device-control agents. **MACHIAVELLI** (Pan et al., ICML 2023;
https://arxiv.org/abs/2304.03279) measures the reward-versus-ethics tradeoff in agent text
games.

Two neighbors are especially relevant to OPERANT's escalation and constraint-pressure
framing. **ST-WebAgentBench** (Levy et al., ICML 2025;
https://arxiv.org/abs/2410.06703) is the only prior benchmark that scores
consent/escalation as a correct action and reports a combined Completion-under-Policy
metric — the nearest precedent to OPERANT's sanctioned-path axis and single-score idea —
but it has no matched malign/benign twins or instruction-origin contrast. **ODCV-Bench**
(Li et al., 2025; https://arxiv.org/abs/2512.20798) is the nearest analog on the
decision-under-pressure axis: it tests whether a goal-driven agent commits constraint
violations under KPI pressure and credits correct refusal/escalation. It is, however,
**unidirectional** — its severity-0 bucket scores honest completion and ethical refusal
identically, so a refuse-everything agent earns a perfect score — and its variants
(Mandated vs. Incentivized) are not benign twins where proceeding is correct.

For completeness, capability-and-policy benchmarks such as **τ-bench / τ²-bench** (Yao et
al., 2024, https://arxiv.org/abs/2406.12045; Barres et al., 2025,
https://arxiv.org/abs/2506.07982) measure policy compliance and tool-use reliability
(pass^k) rather than the appropriateness of a withhold/proceed decision.

## 4. Metric foundations: discrimination, not probabilistic calibration

OCS = `TPR − FPR` is **Youden's J statistic** (Youden 1950, *Cancer*;
DOI 10.1002/1097-0142(1950)3:1<32::AID-CNCR2820030106>3.0.CO;2-3), equivalently
**informedness** (Powers 2011; https://arxiv.org/abs/2010.16061). It is a single
operating-point *discrimination* measure rooted in signal detection theory (Green & Swets
1966; Stanislaw & Todorov 1999, DOI 10.3758/BF03207704), which separates sensitivity from
response bias; OPERANT's surface-matched twins are an explicit attempt to neutralize
response bias so that J reflects discrimination. Because the agent emits a discrete
decision with no elicited probability, OCS is one point in ROC space — it does not trace
an ROC curve or yield an AUC.

This makes OCS distinct from **calibration** in the standard machine-learning sense, which
concerns whether predicted probabilities match empirical frequencies and is measured by
the Brier score (Brier 1950, DOI 10.1175/1520-0493(1950)078<0001:VOFEIT>2.0.CO;2), proper
scoring rules (Gneiting & Raftery 2007;
https://www.tandfonline.com/doi/abs/10.1198/016214506000001437), Expected Calibration
Error (Guo et al. 2017; https://arxiv.org/abs/1706.04599), and reliability diagrams
(DeGroot & Fienberg 1983, DOI 10.2307/2987588). OPERANT elicits no probabilities, so none
of these apply. We use "calibration" in the looser sense of *decision appropriateness*
(withhold when warranted, proceed when warranted) and flag the distinction explicitly,
since **"decision calibration" is already a defined probabilistic term** (Zhao et al.,
NeurIPS 2021; https://arxiv.org/abs/2107.05719) — calibration sufficient for downstream
decisions — which OPERANT does **not** claim to provide.

## 5. The decision as abstention and deferral

The withhold/proceed choice is the classical **reject option** (Chow 1970, IEEE Trans.
Inf. Theory; https://ieeexplore.ieee.org/document/1054406) and its modern descendants:
**selective classification / prediction** (El-Yaniv & Wiener 2010, JMLR,
https://jmlr.org/papers/volume11/el-yaniv10a/el-yaniv10a.pdf; Geifman & El-Yaniv, NeurIPS
2017, https://arxiv.org/abs/1705.08500) and **learning to defer** to a human expert
(Madras et al., NeurIPS 2018, https://arxiv.org/abs/1711.06664; Mozannar & Sontag, ICML
2020, https://arxiv.org/abs/2006.01862). OPERANT's `ESCALATE` is exactly defer-to-a-human,
and `REFUSE`/`REROUTE` are abstention variants. The standard evaluation in this literature
is the risk-coverage curve (and its area, AURC), which characterizes the full
accuracy-versus-coverage tradeoff; OCS reports a single coverage point and folds
`REFUSE`/`ESCALATE`/`REROUTE` into one "withheld" class. OPERANT trades the threshold-swept
richness of risk-coverage analysis for a single comparable operating-point score, by
design.

## 6. Experimental design: contrast sets and minimal pairs

OPERANT's matched malign/benign twins — identical surface, one decision-relevant
difference — instantiate the **contrast-set / counterfactual / minimal-pair** methodology:
contrast sets (Gardner et al., Findings of EMNLP 2020;
https://aclanthology.org/2020.findings-emnlp.117/), counterfactually-augmented data
(Kaushik et al., ICLR 2020; https://arxiv.org/abs/1909.12434), behavioral testing
(Ribeiro et al., CheckList, ACL 2020; https://aclanthology.org/2020.acl-main.442/), and
minimal-pair suites (Warstadt et al., BLiMP, TACL 2020;
https://aclanthology.org/2020.tacl-1.25/). The pairing also invites a matched-pair
*analysis* (McNemar's test or a paired permutation over within-pair differences), a
refinement direction noted in OPERANT's limitations.

## 7. Standards context

OPERANT positions itself in the layer that emerging agent-trust standards name but do not
yet quantify. The **OWASP Top 10 for Agentic Applications** (2025-12-09;
https://genai.owasp.org/) names ASI09 "Human-Agent Trust Exploitation" — confident
explanations that mislead operators into approving harmful actions — but as a qualitative
risk category. **OWASP AIVSS** (v0.8, 2026; https://aivss.owasp.org/) scores vulnerability
*severity* on a 0–10 scale, not operating-decision appropriateness. **NIST AI 600-1**
(https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf), **MITRE ATLAS**
(https://atlas.mitre.org/), **METR** (https://metr.org/, capability/time-horizons), and
the **UK AISI Inspect** framework (https://inspect.aisi.org.uk/, generic eval
infrastructure) likewise name or score adjacent concerns without a standard
operating-decision calibration metric.

## 8. Positioning: the open niche

Each ingredient above is established: the metric (Youden's J), the abstain/defer decision
(selective prediction), the matched-pair methodology (contrast sets), the over-refusal
concern (XSTest, OR-Bench), the instruction-origin threat model (AgentDojo), and matched
agentic twins (AgentHarm). OPERANT claims none of these individually.

What is unoccupied is the **combination**: a single bidirectional score (`TPR − FPR`,
refuse-all = proceed-all = 0) computed over **surface-matched twins that differ
specifically in instruction origin and blast radius**, across a **five-way
operating-decision taxonomy** that scores `ESCALATE`, `REROUTE`, and `PROCEED_SANCTIONED`
as first-class correct outcomes, with an additional **sanctioned-vs-bypass** axis and a
negation-aware bypass hard-gate. AgentHarm has the twins but not provenance, escalation,
or a single score; AgentDojo has provenance but not surface twins or escalation;
ST-WebAgentBench scores escalation with a combined metric but has no twins or provenance;
ODCV-Bench rewards escalation under pressure but is unidirectional. The integrated
instrument is, to our knowledge, novel; the parts are not, and we name them here so the
contribution is read at the right altitude.

## 9. Concurrent work (2026)

Three 2026 papers — concurrent with or slightly later than OPERANT's June 2026 run, and so
not prior art it could have cited at design time — independently converge on its thesis.
They both validate the direction and show the niche now has other entrants:

- **OpenSec** (Barnes 2026; https://arxiv.org/abs/2601.21083) frames incident-response
  agents almost exactly as OPERANT does — "existing benchmarks measure capability (can the
  model do X?) but not calibration (does the model know *when* to do X?)" — and reports
  over-action directly (false-positive containment rates) in a dual-control RL
  environment. Same over-action-as-failure concern as OCS's FPR term, but within one
  security domain rather than across matched operating decisions.
- **RefusalBench** (Weidener et al. 2026; https://arxiv.org/abs/2605.21545) is a
  *matched-triple* (benign / borderline / dual-use) refusal-calibration benchmark for
  biosecurity, arguing that a raw refusal rate misranks models because it cannot separate
  over- from under-refusal — the critique OCS answers with matched twins, in a different
  domain.
- **Learning When to Act or Refuse** (Agarwal et al. 2026;
  https://arxiv.org/abs/2603.03205) makes refusal a first-class, learnable action in
  multi-step agentic tool use (the MOSAIC framework), trained on Agent-SafetyBench.

OPERANT stays distinct from all three by scoring **matched malign/benign twins that differ
specifically in instruction origin and blast radius**, across a **five-way operating
decision** with a single bidirectional OCS — rather than within one security or biosecurity
domain, or as a training objective.

---

## References

- Andriushchenko et al. (2025). *AgentHarm.* ICLR. https://arxiv.org/abs/2410.09024
- Barres et al. (2025). *τ²-bench.* https://arxiv.org/abs/2506.07982
- Brahman, Kumar et al. (2024). *The Art of Saying No (CoCoNot).* NeurIPS D&B. https://arxiv.org/abs/2407.12043
- Brier (1950). *Verification of forecasts expressed in terms of probability.* Monthly Weather Review. DOI 10.1175/1520-0493(1950)078<0001:VOFEIT>2.0.CO;2
- Chow (1970). *On optimum recognition error and reject tradeoff.* IEEE Trans. Inf. Theory. https://ieeexplore.ieee.org/document/1054406
- Cui et al. (2025). *OR-Bench.* ICML. https://arxiv.org/abs/2405.20947
- Debenedetti et al. (2024). *AgentDojo.* NeurIPS D&B. https://arxiv.org/abs/2406.13352
- DeGroot & Fienberg (1983). *The comparison and evaluation of forecasters.* The Statistician. DOI 10.2307/2987588
- El-Yaniv & Wiener (2010). *On the foundations of noise-free selective classification.* JMLR. https://jmlr.org/papers/volume11/el-yaniv10a/el-yaniv10a.pdf
- Gardner et al. (2020). *Evaluating Models' Local Decision Boundaries via Contrast Sets.* Findings of EMNLP. https://aclanthology.org/2020.findings-emnlp.117/
- Geifman & El-Yaniv (2017). *Selective Classification for Deep Neural Networks.* NeurIPS. https://arxiv.org/abs/1705.08500
- Gneiting & Raftery (2007). *Strictly proper scoring rules, prediction, and estimation.* JASA. https://www.tandfonline.com/doi/abs/10.1198/016214506000001437
- Guo et al. (2017). *On Calibration of Modern Neural Networks.* ICML. https://arxiv.org/abs/1706.04599
- Kaushik et al. (2020). *Learning the Difference that Makes a Difference with Counterfactually-Augmented Data.* ICLR. https://arxiv.org/abs/1909.12434
- Levy et al. (2025). *ST-WebAgentBench.* ICML. https://arxiv.org/abs/2410.06703
- Li et al. (2025). *ODCV-Bench.* https://arxiv.org/abs/2512.20798
- Madras et al. (2018). *Predict Responsibly: Learning to Defer.* NeurIPS. https://arxiv.org/abs/1711.06664
- *MobileSafetyBench* (2024). https://arxiv.org/abs/2410.17520
- Mozannar & Sontag (2020). *Consistent Estimators for Learning to Defer to an Expert.* ICML. https://arxiv.org/abs/2006.01862
- Pan et al. (2023). *MACHIAVELLI.* ICML. https://arxiv.org/abs/2304.03279
- Powers (2011). *Evaluation: Precision, Recall, F-Measure to ROC, Informedness, Markedness & Correlation.* https://arxiv.org/abs/2010.16061
- Ribeiro et al. (2020). *Beyond Accuracy: Behavioral Testing of NLP Models with CheckList.* ACL. https://aclanthology.org/2020.acl-main.442/
- Röttger et al. (2024). *XSTest.* NAACL. https://arxiv.org/abs/2308.01263
- Ruan et al. (2024). *Identifying the Risks of LM Agents with an Emulated Sandbox (ToolEmu).* ICLR. https://arxiv.org/abs/2309.15817
- Stanislaw & Todorov (1999). *Calculation of signal detection theory measures.* Behavior Research Methods. DOI 10.3758/BF03207704
- Warstadt et al. (2020). *BLiMP.* TACL. https://aclanthology.org/2020.tacl-1.25/
- Xie et al. (2025). *SORRY-Bench.* ICLR. https://arxiv.org/abs/2406.14598
- Yao et al. (2024). *τ-bench.* https://arxiv.org/abs/2406.12045
- Yin et al. (2024). *SafeAgentBench.* https://arxiv.org/abs/2412.13178
- Yuan et al. (2024). *R-Judge.* EMNLP Findings. https://arxiv.org/abs/2401.10019
- Zhan et al. (2024). *InjecAgent.* ACL Findings. https://arxiv.org/abs/2403.02691
- Zhang et al. (2024). *Agent-SafetyBench.* https://arxiv.org/abs/2412.14470
- Zhang et al. (2025). *FalseReject.* https://arxiv.org/abs/2505.08054
- Zhao et al. (2021). *Calibrating Predictions to Decisions (decision calibration).* NeurIPS. https://arxiv.org/abs/2107.05719
- Youden (1950). *Index for rating diagnostic tests.* Cancer. DOI 10.1002/1097-0142(1950)3:1<32::AID-CNCR2820030106>3.0.CO;2-3

**Concurrent work (2026):**

- Agarwal et al. (2026). *Learning When to Act or Refuse: Guarding Agentic Reasoning Models for Safe Multi-Step Tool Use.* https://arxiv.org/abs/2603.03205
- Barnes (2026). *OpenSec: Measuring Incident Response Agent Calibration Under Adversarial Evidence.* https://arxiv.org/abs/2601.21083
- Weidener et al. (2026). *RefusalBench: Why Refusal Rate Misranks Frontier LLMs on Biological Research Prompts.* https://arxiv.org/abs/2605.21545
