---
description: NExtSEEK data workflow. Routes via the nextseek skill.
allowed-tools: Bash, Read
---

# /nextseek

You have been invoked via the `/nextseek` slash command. Use the `nextseek` skill (auto-loads from `skills/nextseek/SKILL.md`), which documents the NExtSEEK ops and when to use each.

The user's question is below the `---`. Pick the right op(s) for the task per SKILL.md: a search is `nextseek-parse` then `nextseek-api-read`; lineage is `nextseek-graph`; a project report is `nextseek-report`; a submission is `nextseek-generate-submission`; a multi-step "do X, then Y" request is `nextseek-plan`; a create/update/delete is `nextseek-parse` then `nextseek-api-write` under the Layer-3 plain-text confirmation; a single-shot NS run in the live chat session is `nextseek-query`; to reuse raw rows from a prior turn use `nextseek-recall --turn N` instead of re-querying. Compose the answer from what the op(s) return.

<!-- BEGIN PLAN005-GEN:command-ops -->
nextseek-api-read	api-read	sidecar
nextseek-api-write	api-write	sidecar
nextseek-assay-resolve	assay-resolve	local_subcommand
nextseek-build-payload	build-payload	local_subcommand
nextseek-build-upload-xlsx	build-upload-xlsx	sidecar
nextseek-entity-extract	entity	sidecar
nextseek-extract-text	extract	local_subcommand
nextseek-generate-submission	generate-submission	sidecar
nextseek-graph	graph	sidecar
nextseek-parse	parse	sidecar
nextseek-pipeline	pipeline	viewset
nextseek-plan	plan	viewset
nextseek-project-resolve	project-resolve	local_subcommand
nextseek-query	query	viewset
nextseek-recall	recall	viewset
nextseek-report	report	sidecar
nextseek-run-ls	run-ls	sidecar
nextseek-sample-search	sample-search	local_subcommand
nextseek-sampletype-attrs	attrs	local_subcommand
nextseek-validate-upload	build-validate	local_subcommand
<!-- END PLAN005-GEN:command-ops -->

---

$ARGUMENTS
