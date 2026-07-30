#!/usr/bin/env bash
# Seed the NExtSEEK issue-conventions labels (docs/ISSUE-CONVENTIONS.md).
# Idempotent: `gh label create --force` updates color/description if the label exists.
# OUTWARD-FACING: touches github.com — run only with maintainer approval.
set -euo pipefail
REPO="${1:-BMCBCC/NExtSEEK}"
create() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }

# type: exactly one per issue (validator: scripts/validate_issue.py ISSUE_TYPES)
create "type: bug"             "d73a4a" "Existing functionality behaves incorrectly against its intended behavior"
create "type: enhancement"     "a2eeef" "New capability, or an improvement to correct behavior"
create "type: task"            "bfdadc" "Neither bug nor feature: refactor, port, migration, test debt, process"
create "type: docs"            "0075ca" "Documentation-only (in-repo docs, docstrings, OpenAPI examples)"
create "type: performance"     "fbca04" "Correct but too slow or resource-hungry"
create "type: security"        "b60205" "Injection surface, secrets exposure, authz gap - regardless of proven exploitability"
create "type: data-hygiene"    "c2e0c6" "Bad/missing/orphaned data in live stores; the code may be fine"
create "type: design-question" "d876e3" "A maintainer decision is needed before code; closing = recording the ruling"
create "type: ops"             "e99695" "Deployment, installation, infrastructure, or operational degradation"

create "needs-ruling" "5319e7" "Blocked on a maintainer decision"

# area: open set; these are the seeded starters (validator SEEDED_AREAS)
AREA="c5def5"
create "area: cc_assistant"   "$AREA" "Container-CC subsystem: nextseek_api/cc_assistant/, docker/cc-runtime/, agent plugin + skills"
create "area: chat_nextseek"  "$AREA" "NS pipeline package: router, entity/parser/api agents, prompts, catalogs"
create "area: nextseek_api"   "$AREA" "REST API layer not covered by a narrower area"
create "area: seek-proxy"     "$AREA" "SEEK passthrough ViewSet family (samples, studies, investigations, ...)"
create "area: ui"             "$AREA" "Chat frontend, embedded bundle, templates, static assets"
create "area: upload"         "$AREA" "Classic sample/datafile upload paths"
create "area: batch-upload"   "$AREA" "Batch-upload plugin family (_batch_upload_*.py, bins, skill)"
create "area: sample-search"  "$AREA" "Sample search / advanced_search code paths"
create "area: project-search" "$AREA" "Project/investigation/study discovery paths"
create "area: router"         "$AREA" "BAML route decision, route_capabilities, history context"
create "area: schema-rag"     "$AREA" "nextseek_api/schema_rag/ ingest/embedding/query"
create "area: search-solr"    "$AREA" "SEEK/Solr indexing and search"
create "area: graph-neo4j"    "$AREA" "Neo4j data, sync, graph queries"
create "area: deployment"     "$AREA" "Compose topology, images, env/config delivery, live-instance concerns"
create "area: installer"      "$AREA" "startup.sh / startup/ install, reset, doctor, seeding"

echo "Seeded $(gh label list --repo "$REPO" --limit 100 | grep -c -e '^type: ' -e '^area: ' -e '^needs-ruling') conventions labels on $REPO"
