# Control Plus Calibration Narrative

OPERANT and Cross-Provider Egress Guard answer different trust questions.

OPERANT asks: "Does this agent make the right operating decision when it should
act, refuse, escalate, reroute, or stay on a sanctioned path?"

Cross-Provider Egress Guard asks: "Will this local agent be stopped before
sending data to a destination I did not allow?"

Together, they give an operator a calibration receipt and a control receipt.
They should not be collapsed into one claim.

## Where MCPAudit And mcpforge Fit

MCPAudit and mcpforge sit closer to the MCP server/tooling layer:

- mcpforge helps create or package MCP server surfaces.
- MCPAudit inspects MCP server and configuration risk.
- Egress Guard enforces local destination and connector policy across agent
  harnesses.
- OPERANT scores operating-decision calibration under a benchmark corpus.

The practical sequence is:

1. Build or package a server surface with mcpforge.
2. Inspect the MCP surface with MCPAudit.
3. Run the agent behind Egress Guard so network/send-class behavior is
   destination-controlled.
4. Run OPERANT to produce a self-reported calibration receipt for the agent and
   operator contract.

Passing one layer is not evidence that the others passed.

## What A User Can Produce

A credible public-lab or demo bundle can include:

- An MCPAudit result or SARIF artifact for the MCP/server surface.
- An Egress Guard local receipt showing default-deny and provider parity checks.
- An OPERANT report card and badge showing the agent's OCS under a named
  operator contract and corpus.
- A short limits section saying the result is self-reported unless an independent
  reviewer reruns the commands.

Avoid calling this "certified" until an actual third-party certification process
exists. The honest current language is "pilot", "self-reported", "open
benchmark", and "receipt-producing".
