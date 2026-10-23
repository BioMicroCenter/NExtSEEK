# scripts/nfcore_pack

Drafts a new nf-core pipeline's Nessie files from its pinned `nextflow_schema.json`,
`assets/schema_input.json` and `tests/*.nf.test.snap`. The logic is
`NessieAI/chat_nextseek/src/chat_nextseek/seqera/pack_draft.py`, tested by
`NessieAI/tests/chat_nextseek/test_pack_draft.py`; this folder holds only the CLI.

| File | Does | Writes |
|---|---|---|
| `draft.py` | drafts the catalog row, params menu, samplesheet columns and map skeleton, and lists schema traps | `outputs/nfcore_pack/<pipeline>-<revision>/draft.json` |

The drafts are for a person to edit. Nothing here changes the catalog, the templates or the maps.
