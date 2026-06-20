# Why operating-decision calibration — and why a comparable score

Framing for the self-serve OCS runner. This explains the demand it speaks to and how
OCS differs from the benchmarks that already exist. Sources are cited inline; forecasts
are labeled as forecasts, and vendor-commissioned survey data is flagged as such.

## The 2026 buyer gate

The pain a calibration score speaks to is not "is this agent smart" — it is **"prove
this agent won't do something catastrophic once it has production access."**

- Gartner forecasts that **over 40% of agentic-AI projects will be canceled by end of
  2027**, citing escalating cost, unclear value, and **inadequate risk controls**
  (Gartner press release, 2025-06-25). This is an analyst **forecast**, not a measured
  outcome — treat the number as directional. The "inadequate risk controls" driver is
  the load-bearing one here.
- The same release popularized **"agent washing"** — rebranding chatbots/RPA as
  "agents" without substantive agentic capability — and Gartner repeated the warning in
  a 2026-05-20 release about supply-chain planning tooling. The narrative has
  persistence, not a one-off headline.
- Buyer-side, security has become a **deployment gate**: an Okta/AlphaSights survey of
  150 IT/security decision-makers (Jan 2026) found **69% say security concerns are
  slowing agent adoption** and **76% say cross-app agent access influences purchasing
  and renewal**. A Dataiku/Harris poll of 800 data leaders found **75% cite trust in
  agent deployments as a concern** — agents that "drift from intended behavior, act on
  ungoverned data, or execute decisions no one can audit." Both are vendor-commissioned
  (Okta sells agent identity; Dataiku sells governance) — flagged as interested parties,
  but they are primary survey data with disclosed methodology.

## The white space: bidirectional calibration as one comparable number

Several benchmarks each cover one slice of agent trust. None deliver a single,
comparable score where **over-refusing benign work is a failure alongside acting on
injected instructions**:

| Benchmark | Measures | Bidirectional? |
|---|---|---|
| **AgentDojo** (NeurIPS 2024) | indirect prompt-injection resistance + task utility | No — injection ASR + utility, not over-refusal |
| **τ-bench / τ²-bench** (Sierra) | policy compliance + tool-use reliability (pass@k) | No — capability/reliability |
| **OR-Bench / FalseReject** (2024–25) | over-refusal of benign prompts (calibration) | Single-prompt, **in isolation** — "cannot reveal the shape of the refusal boundary" |
| **ODCV-Bench** (arXiv 2512.20798, Dec 2025) | constraint violation under KPI pressure; rewards correct escalation | Nearest analog, but research-grade and violation-leaning; emerging preprint |
| **OPERANT / OCS** | withhold on guarded/adversarial **and** proceed on matched benign twins, as one score | **Yes** — refuse-all = 0, proceed-all = 0 |

OR-Bench found a **0.878 Spearman correlation between safety and over-refusal** — safer
models over-refuse more (Cui et al. 2024). That is the exact tension OCS scores in one
number: the matched malign/benign pair design means you cannot win by being uniformly
cautious *or* uniformly permissive.

## Anchor to the standards, don't compete with them

A standardized agent-trust number is increasingly *expected* in 2026:

- **OWASP Top 10 for Agentic Applications** (2026, released 2025-12-09) — first
  peer-reviewed agentic-security framework; **ASI09 is "Human-Agent Trust
  Exploitation"**: confident, polished explanations that mislead operators into
  approving harmful actions. That maps directly to OPERANT's over-escalation /
  sanctioned-path axes.
- **OWASP AIVSS** (AI Vulnerability Scoring System, v0.8, March 2026) — extends CVSS
  with agentic amplifiers and produces a **0–10 contextual score**. It scores
  *vulnerability severity*, not operating-decision calibration — so OCS sits in the
  layer those frameworks name but do not yet measure.

**How OCS frames itself:** the operating-decision calibration layer — a single,
reproducible, bidirectional score for whether an agent withholds when it should and
proceeds when it should — that the forming agent-trust standards point at but don't
quantify. It is self-reported and open (run it yourself, show your work), explicitly
**not** a certification authority.

## Honesty constraints baked into the report

1. Gartner's 40% is labeled a **forecast**, never an outcome.
2. The reference Claude bands are labeled **not a certified leaderboard** (small n,
   single provider, single-operator authorship).
3. The output is a **self-reported** score, comparable only across identical operator
   contract + case corpus — the report records both.

_Sources: Gartner (2025-06-25, 2026-05-20); Okta/AlphaSights (Jan 2026); Dataiku/Harris;
AgentDojo (NeurIPS 2024); τ-bench (Sierra); OR-Bench (Cui et al. 2024); FalseReject
(arXiv 2505.08054); ODCV-Bench (arXiv 2512.20798); OWASP Agentic Top 10 (2025-12-09);
OWASP AIVSS (2026). Forecasts are directional; vendor surveys are flagged inline._
