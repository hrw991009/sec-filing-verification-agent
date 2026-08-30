# SEC release blocker taxonomy

- Release-blocking items: 9
- Observed runtime failures: `0`

| Blocker | Category | Layer | Affected cases |
|---|---|---|---:|
| `global-a0-a4-common-cases-missing` | `comparability` | `deterministic_contract` | 24 |
| `retrieval-recall-at-5-missing` | `quality` | `deterministic_contract` | 10 |
| `runtime-binding-missing` | `runtime_evidence` | `deterministic_contract` | 66 |
| `offline-predictions-missing` | `offline_execution` | `offline_capability` | 3351 |
| `external-license-review-missing` | `governance` | `offline_capability` | 541 |
| `live-dependencies-not-executed` | `live_execution` | `live_model` | 244 |
| `live-repetitions-missing` | `live_execution` | `live_model` | 310 |
| `language-owner-review-missing` | `governance` | `deterministic_contract` | 10 |
| `remote-ci-owner-closeout-missing` | `governance` | `deterministic_contract` | 0 |

Missing execution evidence is classified as a release blocker, not as an observed model or runtime failure.
