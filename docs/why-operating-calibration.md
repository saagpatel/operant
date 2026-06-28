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
  and renewal** (the 76% specifically concerns Okta's own *Cross App Access* product, so
  read it as demand for the vendor's SKU). A Dataiku/Harris poll of 800+ (812) data
  leaders found **75% cite trust in
  agent deployments as a concern** — agents that "drift from intended behavior, act on
  ungoverned data, or execute decisions no one can audit." Both are vendor-commissioned
  (Okta sells agent identity; Dataiku sells governance) — flagged as interested parties,
  but they are primary survey data with disclosed methodology.

## The white space: bidirectional calibration as one comparable number

Several benchmarks measure one or even both error directions, but none deliver a
**single comparable score over matched malign/benign twins** where over-refusing benign
work and acting on injected instructions are penalized together. (Over-refusal benchmarks
like OR-Bench and XSTest, and agent benchmarks like AgentHarm, do measure both directions
— but as *separate* metrics on *unpaired* prompts, not one bidirectional score over
surface-matched twins.)

| Benchmark | Measures | Bidirectional? |
|---|---|---|
| **AgentDojo** (NeurIPS 2024) | indirect prompt-injection resistance + task utility | No — injection ASR + utility, not over-refusal |
| **τ-bench / τ²-bench** (Sierra) | policy compliance + tool-use reliability (pass@k) | No — capability/reliability |
| **OR-Bench / FalseReject** (2024–25) | over-refusal of benign prompts (calibration) | Single-prompt, **in isolation** — "cannot reveal the shape of the refusal boundary" |
| **ODCV-Bench** (arXiv 2512.20798, Dec 2025) | constraint violation under KPI pressure; rewards correct escalation | Nearest analog, but research-grade and violation-leaning; emerging preprint |
| **XSTest** (NAACL 2024) | exaggerated-safety / over-refusal on safe vs near-identical unsafe contrast prompts | Both directions, separate rates; chat not agentic; the contrast-pair ancestor |
| **AgentHarm** (ICLR 2025) | harmfulness of LLM agents on multi-step tool tasks, with matched benign counterparts | Both directions, separate metrics; twins are operator-channel (no instruction-origin contrast) |
| **ST-WebAgentBench** (ICML 2025) | web-agent policy compliance; Completion-under-Policy; consent/escalation scored | Combined policy metric + scores escalation; no matched malign/benign twins |
| **OPERANT / OCS** | withhold on guarded/adversarial **and** proceed on matched benign twins, as one score | **Yes** — refuse-all = 0, proceed-all = 0 |

OR-Bench found a **0.878 Spearman correlation between safety and over-refusal** — safer
models over-refuse more (Cui et al. 2024). That is the exact tension OCS scores in one
number: the matched malign/benign pair design means you cannot win by being uniformly
cautious *or* uniformly permissive.

## Methodological lineage (what OCS builds on)

OPERANT's contribution is the *integration*, not the parts. Each ingredient has clear
prior art it builds on and should be read against:

- **The metric.** OCS = TPR − FPR is **Youden's J** (Youden 1950) / **informedness**
  (Powers 2011) — a single-operating-point discrimination statistic, applied here to
  agent operating decisions. OPERANT does not claim the metric as novel.
- **The decision.** Withhold vs proceed is the **reject / abstention / learning-to-defer**
  problem (Chow 1970; Geifman & El-Yaniv, NeurIPS 2017; Madras et al., NeurIPS 2018;
  Mozannar & Sontag, ICML 2020); ESCALATE is specifically defer-to-a-human.
- **The cases.** Matched malign/benign twins are a **contrast-set / minimal-pair** design
  (Gardner et al., EMNLP 2020; Kaushik et al., ICLR 2020; Ribeiro et al., CheckList,
  ACL 2020).
- **The closest agentic neighbors.** AgentHarm (matched twins, separate metrics, no
  provenance axis), AgentDojo (operator-vs-injected provenance, no surface twins), and
  ST-WebAgentBench (escalation + combined metric, no twins) each occupy one leg of
  OPERANT's design; none combine all of them into one bidirectional score.

**The open niche** is the combination: a single bidirectional score over surface-matched
twins that differ specifically in **instruction origin + blast radius**, with escalation
/ reroute / sanctioned-path scored as first-class correct actions.

## Anchor to the standards, don't compete with them

A standardized agent-trust number is increasingly *expected* in 2026:

- **OWASP Top 10 for Agentic Applications** (2026, released 2025-12-09) — described as the
  first peer-reviewed agentic-security framework; **ASI09 is "Human-Agent Trust
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
AgentDojo (NeurIPS 2024); AgentHarm (ICLR 2025); τ-bench (Sierra); OR-Bench (Cui et al.
2024); FalseReject (arXiv 2505.08054); XSTest (Röttger et al., NAACL 2024);
ST-WebAgentBench (ICML 2025); ODCV-Bench (arXiv 2512.20798); OWASP Agentic Top 10
(2025-12-09); OWASP AIVSS (2026). Methodological lineage: Youden (1950); Powers (2011);
Chow (1970); Geifman & El-Yaniv (NeurIPS 2017); Madras et al. (NeurIPS 2018); Mozannar &
Sontag (ICML 2020); Gardner et al. (EMNLP 2020); Kaushik et al. (ICLR 2020); Ribeiro et
al. (ACL 2020); Zhao et al. (NeurIPS 2021). Forecasts are directional; vendor surveys are
flagged inline._
