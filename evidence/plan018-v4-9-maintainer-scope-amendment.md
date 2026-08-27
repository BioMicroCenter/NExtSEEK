# Plan 018 V4-9 maintainer scope amendment — initial functional release

Date: 2026-08-13

## Maintainer decisions

- Prioritize a working Bayesian router and live end-to-end proof before the
  exhaustive per-module coverage campaign.
- Use the transferred human grades as ground truth for the
  `functional_success` axis only. Runtime success, artifact validity,
  disposition/exclusion, and conservation remain independently derived.
- Run a small capped judge-versus-human comparison as a sanity check. Judge
  output does not gate the initial fit, publication, activation, or routing.
- Follow the latest worktree-local deploy skill and its repository-root
  `DEPLOYMENT.md`. Keep durable rollback in the configured image registry,
  not as a duplicate long-lived local rollback image.

## Initial-release success conditions

1. The generated dynamic BAML classifier accepts the current corpus-owned
   families and actually receives its `TypeBuilder` at runtime.
2. The human-functional-grade generation is derived from authenticated source
   evidence, publishes immutably, records honest provenance/debt, and activates
   only through a separate compare-and-swap operation.
3. Flag-off behavior preserves the legacy router. Flag-on behavior uses the
   compatible active posterior, while missing, stale, corrupt, incompatible,
   or unavailable store state fails open to legacy routing.
4. A disposable full-path proof and a deployed live canary exercise real
   classification, active-generation selection, posterior routing, and safe
   fallback.
5. Deployment uses committed `origin/dev`, the mandatory baked-secret gate,
   a migration-aware mode-0600 dump, immutable registry rollback identity, and
   the full post-deploy/OI-3 checks. Recovery is forward-only and
   non-destructive.

## Explicit residual debt

- The original V4-9 95% statement-and-branch coverage gates, complete mutation
  campaign, stored three-judge replay, and exhaustive disposable mixed-version
  matrix remain follow-up work. Existing failing coverage evidence stays
  authoritative and must not be relabeled PASS.
- The initial human-grade model is intentionally non-MCMC and
  non-authoritative. Publication requires an explicit
  `initial_human_grade` override and records that debt.
- The transferred evidence contains the source git identity but not all four
  immutable historical OCI image digests. Publication must record
  `legacy_git_sha_only`; it must never fabricate a `stack-v1` identity.

The final cold reviewer must grade both the original task success conditions
and this explicit initial-release amendment, clearly separating deferred
original-plan debt from failure of the amended functional-release objective.
