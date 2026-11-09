# luria/refs

Scripts that stage reference files on Luria for the pipelines Nessie launches. The owner runs
them on Luria; agents only check their syntax.

| File | Stages | Read by |
|---|---|---|
| `stage_dna_refs.sh` | the GATK GRCh38 bundle under `<refs_root>/GATK.GRCh38/`, with iGenomes' prebuilt BWA index in `BWAIndex/` | `run_script.LURIA_GENOMES`, `LURIA_GENOME_RESOURCES` |

A filename change must be made in both the script and `run_script.py`;
`NessieAI/tests/chat_nextseek/test_stage_dna_refs.py` fails until they agree.
