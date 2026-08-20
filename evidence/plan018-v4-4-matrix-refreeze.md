# Plan 018 V4-4 matrix re-freeze (ruling B remediation)

**Date:** 2026-08-12  
**Ruling:** B — discordance only for quality winners  
**Prior freeze invalidated:** contract change + acceptance runner remediation

## Changes

| Scenario | Fixture | GT | Notes |
|----------|---------|-----|-------|
| `quality_eq_ns_faster` | 8× `both_succeed`, NS 1s vs CC 2s | `strong_ns` | Valid under B (0 discordant, latency edge ≥20%) |
| `quality_eq_cc_faster` | 8× `both_succeed`, CC faster | `strong_cc` | Same |
| `adversarial_outliers` | mostly `both_succeed`, ~0.5% latency gap, 25% both_fail | `indecisive` | Rebalanced from directional discordant mix |

## Fingerprint

```
contract: v14b-ruling-b
sha256: see matrix_fingerprint() in recovery_matrix.py
```

Seeds unchanged: `(11, 22, 33, 44, 55)` — 40 slots total.

## MCMC config bump (maintainer-authorized re-freeze)

Baseline V14FitConfig was 300 warmup / 500 samples. Remediation used one bump to **600 / 2000 / 2 chains** so R-hat ≤1.01 and ESS ≥400 hold on worst seeds while staying within 60m wall (~633s for 40 slots).

Latency MCMC is skipped (diagnostics not required) for families below `min_retained_pairs` (below_min_support scenario).

## Maintainer authorization

Re-freeze and full 40 MCMC re-run: **LOCKED yes** (2026-08-12).
