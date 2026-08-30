# `agent-security-v1` contract card

## Purpose

`agent-security-v1` derives six English/Chinese cases from the three security/recovery pairs in
`sec-temporal-v1`. It reuses the release Budget and `ReleaseTrajectoryContract`; it does not add a
benchmark-only Agent loop.

The evaluation method maps:

- BFCL-style action and argument validation;
- ToolSandbox-style milestone partial order and state prerequisites;
- tau-bench-style final-state checks and all-k repeated success;
- AgentDojo-style separation of trusted question and untrusted injected content.

No external benchmark code or payload is imported. These names describe evaluation methods, not
claims of official benchmark compatibility or scores.

## Frozen contract

- 6 cases: prompt injection denial, cross-workspace permission denial, and bounded transient
  recovery in both English and Chinese.
- 3 frozen contract trials per case, 18 trials total.
- Required milestones, allowed/forbidden actions, argument constraints, stop reason, workspace,
  maximum attempts, and final-state expectations are machine checked.
- Injection attack success, cross-workspace action, unauthorized action, duplicate side effect,
  recovery success, and empirical `pass^3` are derived by the scorer rather than accepted as
  self-reported labels.

## Evidence boundary

The checked report has 18/18 contract trials and 6/6 all-three intersections. This proves the
scorer, generator, and frozen observation contract. `UnifiedAgentRuntime`, a live model, production
database final state, official benchmark code, branch/PR/main CI, and owner review were not
executed, so the report is not a model reliability or release-readiness result.
