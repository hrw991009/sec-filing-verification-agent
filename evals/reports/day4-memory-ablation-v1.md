# Day 4 Memory off/on deterministic ablation

The two cases use the same question, model fixture, prompt, Runtime, and budget. Only the versioned Context profile changes from Memory off to Memory on.

| Mode | Task quality | Pollution | Conflict handling | Input tokens | Latency |
|---|---:|---:|---:|---:|---:|
| Off | 0 / 1 | 0 / 1 | 0 / 1 | 180 | 24 ms |
| On | 1 / 1 | 0 / 1 | 1 / 1 | 244 | 31 ms |
| On − off | +1.0 | 0.0 | +1.0 | +64 | +7 ms |

For this frozen preference-recall input, confirmed Memory improves the rule-scored result and conflict handling at a 64-token and 7 ms fixture cost without adding pollution. This is a single deterministic ablation, not evidence that Memory improves every task or live Provider quality.
