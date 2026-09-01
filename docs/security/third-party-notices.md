# Third-party dependency and notice record

This record covers the locked dependency graph used by the SEC disclosure verifier. It is an
engineering inventory for release review, not a substitute for owner or legal approval.

## Automated gates

- Python packages are enumerated by `pip-licenses==5.5.5` from the locked `uv` environment.
  Any license outside the explicit `pyproject.toml` allowlist fails the gate. The private backend
  distribution, plus Semgrep-only `face` and `peewee`, are excluded from the metadata gate and
  reviewed below.
- Node packages are enumerated by `license-checker-rseidelsohn==5.0.1` from the frozen pnpm
  installation. Private workspace packages are excluded; any third-party license expression
  outside the command's explicit allowlist fails the gate.
- `pnpm run security:semgrep`, Python and Node vulnerability audits, Gitleaks, lockfile checks and
  this non-empty record are separate CI gates. Passing them does not close the owner license review.
- Semgrep 1.175.0 cannot parse the valid Python 3.13 `type AccessTokenPublicKeys = ...` declaration
  in `core/config.py`. That file is an explicit scanner exclusion and remains covered by Ruff,
  strict mypy and configuration tests; the exclusion must be removed when the pinned parser
  supports the syntax.

## Manual clarifications and notices

| Component | Scope | License/notice handling |
|---|---|---|
| `face==26.0.1` | Semgrep development dependency | Wheel `LICENSE` is BSD-3-Clause; not linked into or shipped with the backend runtime. |
| `peewee==3.19.0` | Semgrep development dependency | Wheel `LICENSE` is MIT; not linked into or shipped with the backend runtime. |
| `semgrep==1.175.0` | CI/development scanner | LGPL-2.1-or-later tool executed as a separate process; not linked into the product runtime. |
| Apache ECharts `6.1.0` | Web runtime dependency | Apache-2.0. Preserve the dependency's bundled `LICENSE` and Apache `NOTICE` in any redistributed web artifact. |
| MinIO server and client images | Separate Compose infrastructure | AGPL-3.0 obligations must be reviewed before redistributing modified images or offering a hosted service. The project does not copy their binaries into its application packages. |

External SEC material and benchmark datasets are governed by their source and dataset cards, not
by this dependency record. FinanceBench rights, public benchmark use, third-party image
redistribution, and the final production artifact remain release blockers until the recorded owner
review is complete.
