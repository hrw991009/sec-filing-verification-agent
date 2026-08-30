# sec-temporal-v1 Language Review Sample

> Status: pending independent owner review. Unchecked boxes are release blockers, not completed evidence.

## Review Rules

For each sampled pair, compare the English and Chinese questions against the single shared gold object in `evals/scenarios/sec-temporal-v1.json`. Confirm that both languages request the same filer, period, `as_of` boundary, financial concept, operation, unit, and business response. Do not change gold to make a translation pass.

| Pair | Category | Same intent and cutoff | Financial terminology | Natural Chinese | No answer leakage |
| --- | --- | --- | --- | --- | --- |
| `p01-revenue-2020` | direct fact | [ ] | [ ] | [ ] | [ ] |
| `p03-cash-2022` | direct fact | [ ] | [ ] | [ ] | [ ] |
| `p06-lease-liability-2023` | table/text | [ ] | [ ] | [ ] | [ ] |
| `p08-americas-segment-table-2024` | table/text | [ ] | [ ] | [ ] | [ ] |
| `p10-gross-margin-2020` | calculation | [ ] | [ ] | [ ] | [ ] |
| `p16-revenue-growth-2022-2023` | cross-period | [ ] | [ ] | [ ] | [ ] |
| `p20-quest-restatement-reason` | amendment | [ ] | [ ] | [ ] | [ ] |
| `p24-cash-scope-conflict-2024` | custom/conflict | [ ] | [ ] | [ ] | [ ] |
| `p26-quest-before-amendment` | no-answer/cutoff | [ ] | [ ] | [ ] | [ ] |
| `p28-prompt-injection-denial` | security | [ ] | [ ] | [ ] | [ ] |

## Sign-Off

- Reviewer: [ ]
- Review date: [ ]
- Manifest SHA-256 matches `evals/reports/sec-temporal-v1.json`: [ ]
- All 40 checks completed with issues resolved or recorded: [ ]
- Notes/issue references: [ ]
