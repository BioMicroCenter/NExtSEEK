# Corpus question inventory — manual review

**381 variants / 447 turns** across 16 families.
Generated from `chat_nextseek/e2e/catalog.json` + `nessie_tests/overlay.json` via `corpus.merged()`.

Two problems to look for, per the 2026-07-29 review:

1. **Bad question** — the premise is false, so no answer can be right. Example: every `GBM` question,
   because GBM does not exist as a Study or Investigation title. Three of the seven failures in the
   seed-0 rerun were this, and all three were scored as product failures.
2. **Criteria that cannot judge the answer** — the assertion checks plan shape only, or pins a value
   that is a model choice (parameter names, one of several valid endpoints, exact reply wording).

Suggested disposition per row: `KEEP` / `FIX-CRITERIA` / `FIX-QUESTION` / `DELETE`.

## Counts by family

| family | turns |
|---|---|
| graph_query | 59 |
| nessie_green | 4 |
| nessie_repro | 6 |
| nessie_route | 7 |
| pipeline_nfcore | 35 |
| refine_and_recall | 95 |
| reporting | 64 |
| routing_graph | 1 |
| routing_lab | 2 |
| search_advanced | 67 |
| search_parents_by_child | 18 |
| search_retrieve | 20 |
| search_tree | 16 |
| system_question | 27 |
| unsupported | 7 |
| writes_unsupported | 19 |

## Questions


### graph_query

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `graph.assay_flow_protocols` | main | Show me samples associated with flow cytometry protocols. | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.assay_short_read` | main | Find all DNA samples associated with Short Read Sequencing. | `parser_plan.mode` eq `graph_query`; `neo4j_ok` true; `graph_result.parameters` nonempty; `graph_cypher` contains `DERIVED_FROM`; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_d_seq_samples_in_metne` | main | Find me D.SEQ samples in MetNet | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_d_seq_samples_in_the_s` | main | Find me D.SEQ samples in the SHA lab from project IMPACT | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_nhp_samples_in_impact` | main | Find me NHP samples in IMPACT | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_nhp_samples_in_the_met` | main | Find me NHP samples in the MetNet project | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_pbmc_tissues_in_the_gb` | main | Find me PBMC (tissues) in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_pbmcs_in_the_gbm_study` | main | Find me PBMCs in the GBM Study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_pbmcs_in_the_gbm_study_2` | main | Find me PBMCs in the GBM study that have Sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_pbmcs_tissues_in_the_g` | main | Find me PBMCs (tissues) in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_studies_in_metnet` | main | Find me studies in MetNet | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_result.count` gte `1`; `graph_result.count` lte `25`; `graph_not_truncated` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_tissue_samples_in_impa` | main | Find me tissue samples in IMPACT | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_me_tissues_in_the_gbm_stu` | main | Find me tissues in the GBM study that are PBMCs | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_samples_in_the_srp_projec` | main | Find samples in the SRP project with keyword NDMA or that were processed by assay  Chemical Challenge | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_samples_in_the_srp_projec_2` | main | Find samples in the SRP project with keyword NDMA or that were processed by assay Chemical Challenge | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_samples_processed_via_rna` | main | Find samples processed via RNA extraction | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.find_samples_that_underwent_si` | main | Find samples that underwent single cell sequencing | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_mice_are_in_the_datab` | main | How many mice are in the database | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_mice_have_ndma` | main | How many mice have NDMA | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_monkeys_exist_in_the` | main | how many monkeys exist in the database | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_patients_are_in_the_g` | main | How many patients are in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_pbmcs_are_in_the_gbm` | main | How many PBMCs are in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_studies_are_there_in` | main | How many studies are there in the IMPACT project | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_tis_samples_exist_acr` | main | How many TIS samples exist across all studies | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.how_many_tissue_samples_are_in` | main | How many tissue samples are in the CSBC investigation | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.investigations_nhp_seq` | main | Which investigations have NHP samples with sequencing data? | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.mice_with_seq` | main | Find me all mice that have sequencing data. | `parser_plan.mode` eq `graph_query`; `graph_cypher` contains `DERIVED_FROM`; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.nhp_srp` | main | Show me all NHPs in the SRP project. | `parser_plan.mode` eq `graph_query`; `neo4j_ok` true; `graph_result.parameters.project` matches_re `(?i)srp`; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.pbmc_gbm_seq` | main | Find PBMC samples from the GBM study that also have sequencing data. | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.show_me_all_nhps_in_the_impact` | main | Show me all NHPs in the Impact project | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.show_me_all_tissues_in_the_gbm` | main | Show me all tissues in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.show_me_samples_in_the_gbm_stu` | main | Show me samples in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.show_me_samples_processed_via` | main | Show me samples processed via single cell | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.show_me_samples_processed_via_2` | main | Show me samples processed via mass spectrometry | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.studies_in_griffith` | main | What studies are in the Griffith project? | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.tissue_cell_impact` | main | Find all tissue and cell samples from IMPACT. | `parser_plan.mode` eq `graph_query`; `neo4j_ok` true; `graph_result.total` gte `10000`; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_investigations_exist_in_t` | main | What investigations exist in the database | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_mice_are_associated_with` | main | What mice are associated with the Water Study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_mice_are_in_the_impact_st` | main | What mice are in the Impact study | `parser_plan.mode` eq `graph_query`; `neo4j_ok` true; `graph_result.count` gte `1`; `graph_not_truncated` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_patients_have_pbmc_sample` | main | What patients have PBMC samples | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_pbmc_tissues_in_the_gbm_s` | main | what PBMC tissues in the GBM study have sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_pbmcs_exist_in_the_gbm_st` | main | What PBMCs exist in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_pbmcs_in_the_gbm_study_ha` | main | What PBMCs in the GBM study have RNAseq data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_pbmcs_in_the_gbm_study_ha_2` | main | what PBMCs in the GBM study have sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_pbmcs_tissues_in_the_gbm` | main | what PBMCs tissues in the GBM study have sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_result.parameters.project` matches_re `(?i)gbm`; `graph_result.parameters.child_type` eq `D.SEQ`; `graph_result.parameters.parent_type` nonempty; `graph_cypher` contains `DERIVED_FROM` | base | |
| `graph.what_projects_have_mouse_sampl` | main | What projects have mouse samples | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_projects_have_samples_tha` | main | What projects have samples that undergo Short Read Sequencing | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_protocols_exist_for_the_i` | main | What protocols exist for the IMPACT project | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_samples_exist_in_the_gbm` | main | What samples exist in the GBM study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_sequencing_data_exists_in` | main | What sequencing data exists in the GBM Study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_studies_are_in_the_impact` | main | What studies are in the Impact Project | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_studies_have_monkeys` | main | What studies have monkeys | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_studies_have_nhp_samples` | main | what studies have NHP samples with flow and sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_studies_have_nhp_samples_2` | main | What studies have NHP samples | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_tissues_are_in_the_gbm_st` | main | what tissues are in the gbm study | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.what_tissues_in_the_gbm_study` | main | what tissues in the GBM study have sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.which_investigations_have_nhp` | main | Which investigations have NHP samples with both flow and sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.which_monkeys_have_imaging_and` | main | Which monkeys have imaging and sequencing data | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |
| `graph.which_tissue_samples_underwent` | main | Which tissue samples underwent immunohistochemistry | `parser_plan.mode` eq `graph_query`; `graph_cypher` nonempty; `neo4j_ok` true; `graph_outcome_observed` true; `graph_truncation_disclosed` true | base | |

### nessie_green

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `green.global_count` | main | How many samples are in the database? | `route` eq `nextseek_query`; `last_reply` matches_re `50,?88[0-9]` | overlay | |
| `green.mus_ndma` | main | Find mice treated with NDMA. | `route` eq `nextseek_query`; `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_ok` true; `api_result_meta.row_count` gte `1` | overlay | |
| `green.refine_recall` | refine | Just the 4 week ones. | `parser_plan.mode` eq `refine_last_search`; `api_ok` true | overlay | |
| `green.refine_recall` | seed | Find samples from a 4 week study. | `route` eq `nextseek_query`; `api_ok` true; `api_plan.requestBody.filter_searchText` nonempty; `api_result_meta.row_count` lte `20000` | overlay | |

### nessie_repro

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `repro.cypher_uid_dot` | main | Find sequencing data for NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB. | `last_reply` mentions `D.SEQ-220823SHA` | overlay | |
| `repro.eof_truncation_reporter` | main | Write me an nf-core rnaseq report for the last results. | `last_reply` matches_re `(?s)^(?!.*could not be completed).*$` | overlay | |
| `repro.parent_attr_aggregate` | aggregate | Give me the unique counts of sex and species of all of those monkeys. | `last_reply` matches_re `(male|female|specie|mulatta|fascicularis)` | overlay | |
| `repro.parent_attr_aggregate` | seed | Find me sequencing data associated with non human primates. | `route` eq `nextseek_query` | overlay | |
| `repro.thin_bundle_recall` | recall | Which of those are RNA-seq? | `bundle.has_extra_keys` true | overlay | |
| `repro.thin_bundle_recall` | seed | Find sequencing data for the parents of NHP samples. | `route` eq `nextseek_query` | overlay | |

### nessie_route

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `route.cc_open_ended_analysis` | main | Is treatment A significantly better than treatment B based on our sequencing data? | `route` eq `container_cc` | overlay | |
| `route.cc_reingest` | main | Build a NExtSEEK upload sheet from the nf-core rnaseq outputs. | `route` eq `container_cc` | overlay | |
| `route.cc_write_investigation` | main | Create me investigation TEST-CD with project id 2 | `route` eq `container_cc` | overlay | |
| `route.ns_advanced` | main | Find me mice treated with NDMA. | `route` eq `nextseek_query` | overlay | |
| `route.ns_pipeline_launch` | main | Launch an nf-core rnaseq run for D.SEQ-240910LAU-135. | `route` eq `nextseek_query` | overlay | |
| `route.ns_plain_study_membership` | main | What mice are in the Impact study? | `route` eq `nextseek_query` | overlay | |
| `route.unrelated` | main | What's the weather in Boston tomorrow? | `route` eq `unrelated` | overlay | |

### pipeline_nfcore

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `pipeline.activation_rnaseq` | setup_search | Find me mice treated with NDMA. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_ok` true | base | |
| `pipeline.activation_rnaseq` | trigger_pipeline | Run rnaseq on those mice grouped by Treatment1. | `pipeline_agent.active` true; `ui_text.assistant_reply` nonempty | base | |
| `pipeline.build_a_single_cell_rna_seq_pi` | main | Build a single-cell RNA-seq pipeline CSV for D.SEQ-241114SHA-5-PUB | `last_reply` nonempty | base | |
| `pipeline.build_an_nfcore_samplesheet_fo` | main | Build an nfcore samplesheet for these | `last_reply` matches_re `(which|what|clarify|specify|UID|search|don't have|no .*(pinned|prior|previous))` | base | |
| `pipeline.build_me_a_nfcore_pipeline_for` | main | Build me a Nfcore pipeline for A.GEX-240710KAM-2-PUB | `last_reply` nonempty | base | |
| `pipeline.build_me_a_nfcore_rnaseq_sampl` | main | build me a nfcore rnaseq samplesheet for 	D.SEQ-240709KAM-2-PUB | `last_reply` nonempty | base | |
| `pipeline.build_me_a_samplesheet_for_nf` | main | build me a samplesheet for nf-core | `last_reply` nonempty | base | |
| `pipeline.build_me_an_nf_core_sampleshee` | main | Build me an nf-core samplesheet | `last_reply` nonempty | base | |
| `pipeline.build_me_an_nf_core_sampleshee_2` | main | Build me an nf-core samplesheet for those mice | `last_reply` nonempty | base | |
| `pipeline.build_me_an_nf_core_sampleshee_3` | main | Build me an nf-core samplesheet for those | `last_reply` nonempty | base | |
| `pipeline.build_me_an_nf_core_sheet` | main | build me an nf core sheet | `last_reply` nonempty | base | |
| `pipeline.build_me_an_nfcore_samplesheet` | main | build me an nfcore samplesheet for those samples | `last_reply` nonempty | base | |
| `pipeline.create_an_nf_core_samplesheet` | main | Create an nf-core samplesheet for D.SEQ-240709KAM-4-PUB and D.SEQ-241219BRY-2-PUB | `route` eq `nextseek_query`; `last_reply` mentions `D.SEQ-240709KAM-4-PUB` | base | |
| `pipeline.edit_after_sanity` | edit_groupby | Actually use Treatment1 instead of Sex for grouping. | `pipeline_agent.active` true | base | |
| `pipeline.edit_after_sanity` | issue_directive | Run rnaseq on those mice grouped by Sex. | `pipeline_agent.active` true | base | |
| `pipeline.edit_after_sanity` | setup_search | Find me mice treated with NDMA. | `entity_sampletype_codes` contains `MUS`; `api_ok` true | base | |
| `pipeline.edit_after_sanity` | submit | submit | `pipeline_agent.active` true; `api_artifact.samplesheet.csv` true | base | |
| `pipeline.end_to_end_emit` | confirm_submit | submit | `pipeline_agent.active` true; `api_artifact.samplesheet.csv` true; `api_artifact.samplesheet.csv.rows_gte` gte `1`; `pipeline_agent.launch_plan.params.aligner` nonempty; `pipeline_agent.launch_plan.params.pseudo_aligner` nonempty; `pipeline_agent.launch_plan.params.genome` nonempty | base | |
| `pipeline.end_to_end_emit` | issue_directive | Run rnaseq on those mice grouped by Treatment1. | `pipeline_agent.active` true | base | |
| `pipeline.end_to_end_emit` | setup_search | Find me mice treated with NDMA. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_ok` true | base | |
| `pipeline.generate_an_nf_core_rna_seq_sa` | main | Generate an nf-core RNA-seq samplesheet for D.SEQ-221031SHA-67-PUB | `last_reply` nonempty | base | |
| `pipeline.happy_path_nhp_rnaseq` | issue_directive | Run rnaseq on these monkeys, group by Treatment1. | `pipeline_agent.active` true | base | |
| `pipeline.happy_path_nhp_rnaseq` | setup_search | Find me NHP samples with RNA-seq data. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `NHP`; `api_ok` true | base | |
| `pipeline.happy_path_nhp_rnaseq` | submit | submit | `pipeline_agent.active` true; `api_artifact.samplesheet.csv` true | base | |
| `pipeline.happy_path_scrnaseq` | issue_directive | Build a single-cell RNA-seq pipeline for D.SEQ-241114SHA-5-PUB. | `pipeline_agent.active` true | base | |
| `pipeline.happy_path_scrnaseq` | submit | submit | `pipeline_agent.active` true; `pipeline_agent.pipeline_key` eq `scrnaseq`; `api_artifact.samplesheet.csv` true; `pipeline_agent.launch_plan.params.protocol` nonempty; `pipeline_agent.launch_plan.params.aligner` nonempty | base | |
| `pipeline.make_me_an_nfcore_samplesheet` | main | Make me an nfcore samplesheet for the sequencing samples associated with NHP-220630FLY-1-PUB | `last_reply` nonempty | base | |
| `pipeline.question_submode` | main | what nf-core pipelines are available? | `last_reply` nonempty | base | |
| `pipeline.reject_non_directive` | main | what's NDMA? | `pipeline_agent.active` eq `False` | base | |
| `pipeline.sarek_multi_cohort` | issue_directive | Run sarek on those samples, split into cohorts by LibraryStrategy. | `pipeline_agent.active` true | base | |
| `pipeline.sarek_multi_cohort` | setup_search | Find me D.SEQ samples in the IMPACT project. | `api_ok` true | base | |
| `pipeline.sarek_multi_cohort` | submit | submit | `pipeline_agent.active` true; `pipeline_agent.pipeline_key` eq `sarek`; `api_artifact.samplesheet.csv` true | base | |
| `pipeline.tower_submit` | issue_directive | Run rnaseq on those mice grouped by Treatment1, submit to Tower. | `pipeline_agent.active` true | base | |
| `pipeline.tower_submit` | setup_search | Find me mice treated with NDMA. | `api_ok` true | base | |
| `pipeline.tower_submit` | submit | submit | `pipeline_agent.active` true; `last_reply` mentions `Tower` | base | |

### refine_and_recall

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `refrec.ah_got_it_retry_that_search_wi` | followup | Ah got it. Retry that search with project id = 2 | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.ah_got_it_retry_that_search_wi` | seed | Create me investigation "TEST WOW TEST" | _(none)_ | base | |
| `refrec.and_how_many_dna_samples_did_w` | followup | And how many DNA samples did we find | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.and_how_many_dna_samples_did_w` | seed | How many D.SEQ samples did the very first search in this session return | _(none)_ | base | |
| `refrec.can_you_run_that_again_but_wit` | followup | Can you run that again, but with project id 2 | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.can_you_run_that_again_but_wit` | seed | Create a new investigation "TEST API CD" for me | _(none)_ | base | |
| `refrec.can_you_summarize_what_those_r` | followup | Can you summarize what those results contained | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.can_you_summarize_what_those_r` | seed | Find mice treated with NDMA | `api_ok` true | base | |
| `refrec.can_you_use_the_advanced_searc` | followup | can you use the advanced search endpoint instead | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.can_you_use_the_advanced_searc` | seed | how many monkeys exist in the database | _(none)_ | base | |
| `refrec.chained_filter` | count | How many were returned in that search? | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.chained_filter` | extract_uids | Of those, give me the first 3 UIDs. | `parser_plan.mode` eq `ask_about_last_results`; `chat_log.length` gte `3`; `last_reply` matches_re `D\.SEQ` | base | |
| `refrec.chained_filter` | seed | Find me all D.SEQ samples for the SHA lab. | `parser_plan.mode` eq `new_search`; `api_ok` true | base | |
| `refrec.filter_the_last_search_to_cd8` | followup | Filter the last search to CD8-depleted animals | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.filter_the_last_search_to_cd8` | seed | Find all NHP samples in the database | `api_ok` true | base | |
| `refrec.from_the_above_search_which_sa` | followup | from the above search, which samples have tumor values | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.from_the_above_search_which_sa` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.from_the_last_search_which_sam` | followup | From the last search, which samples came from the most recent year | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.from_the_last_search_which_sam` | seed | Find mice treated with NDMA | `api_ok` true | base | |
| `refrec.from_those_results_keep_only_t` | followup | From those results, keep only the CD8-depleted ones | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.from_those_results_keep_only_t` | seed | Find all NHP samples in the database | `api_ok` true | base | |
| `refrec.going_way_back_how_many_ndma_m` | followup | Going way back — how many NDMA mice did the very first search return | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.going_way_back_how_many_ndma_m` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.how_many_d_seq_impact_samples` | followup | How many D.SEQ IMPACT samples did the first search return | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.how_many_d_seq_impact_samples` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.how_many_d_seq_samples_did_the` | followup | How many D.SEQ samples did the very first search in this session return | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.how_many_d_seq_samples_did_the` | seed | Find me studies in MetNet | `api_ok` true | base | |
| `refrec.how_many_distinct_labs_are_rep` | followup | How many distinct labs are represented across those 264 samples | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.how_many_distinct_labs_are_rep` | seed | Of those, which are actually from the SHA lab? Give me UIDs with their Lab field value | _(none)_ | base | |
| `refrec.how_many_ndma_mice_did_the_fir` | followup | How many NDMA mice did the first search return | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.how_many_ndma_mice_did_the_fir` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.how_many_results_did_that_retu` | followup | How many results did that return | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.how_many_results_did_that_retu` | seed | Find mice treated with NDMA | `api_ok` true | base | |
| `refrec.just_show_me_the_first_3_uids` | followup | Just show me the first 3 UIDs from that search | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.just_show_me_the_first_3_uids` | seed | Of those, which ones are from the SHA lab specifically? Give me UIDs | _(none)_ | base | |
| `refrec.memory_how_many` | recall | How many results came back from that last search? | `parser_plan.mode` eq `ask_about_last_results`; `last_reply` matches_re `\b\d+\b` | base | |
| `refrec.memory_how_many` | seed | Find me mice treated with NDMA. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_ok` true | base | |
| `refrec.memory_scientists` | recall | What scientists are listed on those samples? | `parser_plan.mode` eq `ask_about_last_results`; `last_reply` mentions `Scientist` | base | |
| `refrec.memory_scientists` | seed | Find me D.SEQ samples in the SHA lab. | `parser_plan.mode` eq `new_search`; `api_ok` true | base | |
| `refrec.memory_unique_types` | recall | What unique sample types are in those results? | `parser_plan.mode` eq `ask_about_last_results`; `last_reply` mentions `TIS` | base | |
| `refrec.memory_unique_types` | seed | Show me tissue and cell samples from IMPACT. | `parser_plan.mode` eq `new_search`; `api_ok` true | base | |
| `refrec.now_filter_those_to_only_cd8_d` | followup | Now filter those to only CD8-depleted animals | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.now_filter_those_to_only_cd8_d` | seed | Find all NHP samples in the database | `api_ok` true | base | |
| `refrec.of_the_ndma_mice_which_are_mal` | followup | Of the NDMA mice, which are male | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_the_ndma_mice_which_are_mal` | seed | What about those NHP samples — how many were there | _(none)_ | base | |
| `refrec.of_those_mice_can_you_summariz` | followup | Of those mice, can you summarize the sex statistics of it | `parser_plan.mode` eq `ask_about_last_results`; `api_result_meta.source_mode` eq `new_search`; `last_reply` matches_re `(male|female|sex)` | base | |
| `refrec.of_those_mice_can_you_summariz` | seed | Find me mice associated with ndma | `api_ok` true; `api_result_meta.row_count` gte `1` | base | |
| `refrec.of_those_mice_categorize_them` | followup | Of those mice, categorize them by genotype | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_mice_categorize_them` | seed | Find me ndma treated mice | `api_ok` true | base | |
| `refrec.of_those_monkeys_which_are_cd8` | followup | Of those monkeys, which are cd8 depleted | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_monkeys_which_are_cd8` | seed | What monkeys exist in the database | _(none)_ | base | |
| `refrec.of_those_samples_which_have_a` | followup | of those samples, which have a high tumor fraction | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_samples_which_have_a` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.of_those_samples_which_have_tu` | followup | of those samples, which have tumor values | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_samples_which_have_tu` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.of_those_which_are_actually_fr` | followup | Of those, which are actually from the SHA lab? Give me UIDs | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_which_are_actually_fr` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.of_those_which_are_actually_fr_2` | followup | Of those, which are actually from the SHA lab? Give me UIDs with their Lab field value | `parser_plan.mode` eq `ask_about_last_results`; `last_reply` mentions `D.SEQ` | base | |
| `refrec.of_those_which_are_actually_fr_2` | seed | Find me all D.SEQ samples for the SHA lab | `api_ok` true; `route_source` eq `baml`; `parser_plan.mode` eq `new_search` | base | |
| `refrec.of_those_which_ones_are_from_t` | followup | Of those, which ones are from the SHA lab specifically? Give me UIDs | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.of_those_which_ones_are_from_t` | seed | How many were returned in that search | _(none)_ | base | |
| `refrec.refine_after_2023` | refine | Narrow those results to samples collected after 2023. | `parser_plan.mode` eq `refine_last_search`; `api_ok` true | base | |
| `refrec.refine_after_2023` | seed | Find me D.SEQ samples for the SHA lab. | `parser_plan.mode` eq `new_search`; `api_ok` true | base | |
| `refrec.refine_liver` | refine | Can you limit those to liver tissue only? | `parser_plan.mode` eq `refine_last_search`; `api_ok` true | base | |
| `refrec.refine_liver` | seed | Find tissue samples from the IMPACT project. | `parser_plan.mode` eq `new_search`; `api_ok` true | base | |
| `refrec.refine_those_results_to_cd8_de` | followup | Refine those results to CD8-depleted animals only | `parser_plan.mode` eq `refine_last_search`; `api_ok` true | base | |
| `refrec.refine_those_results_to_cd8_de` | seed | Find all NHP samples in the database | `api_ok` true; `entity_sampletype_codes` contains `NHP` | base | |
| `refrec.refine_to_cd8` | refine | Which of those monkeys are depleted of CD8? | `parser_plan.mode` eq `refine_last_search`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `trio` trio_match | base | |
| `refrec.refine_to_cd8` | seed | Find me NHP samples in the IMPACT project. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `NHP`; `api_ok` true | base | |
| `refrec.refine_to_female` | refine | Now filter those to only female animals. | `parser_plan.mode` eq `refine_last_search`; `api_ok` true | base | |
| `refrec.refine_to_female` | seed | Find me mouse samples in the Kamm project. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_ok` true | base | |
| `refrec.rerun_that_last_search_with_pr` | followup | Rerun that last search with project id = 2 | `parser_plan.mode` eq `refine_last_search`; `last_reply` matches_re `(MUS-|mice|mouse)` | base | |
| `refrec.rerun_that_last_search_with_pr` | seed | Find me mice treated with NDMA | `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_result_meta.row_count` gte `1` | base | |
| `refrec.try_it_again_but_with_project` | followup | Try it again but with project id = 2 | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.try_it_again_but_with_project` | seed | Create me an investigation called API TEST 2 | _(none)_ | base | |
| `refrec.try_that_again_but_with_projec` | followup | Try that again but with project id = 2 | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.try_that_again_but_with_projec` | seed | Create me investigation "Oh yeah! Lets go" | _(none)_ | base | |
| `refrec.try_that_previous_request_but` | followup | Try that previous request but with project id 2 | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.try_that_previous_request_but` | seed | Create me investigation "TESTING" | _(none)_ | base | |
| `refrec.try_that_search_again_with_wat` | followup | Try that search again with "Water" instead of "Water Study" | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.try_that_search_again_with_wat` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.try_this_search_again_with_tis` | followup | Try this search again with TIS instead of PAT | `parser_plan.mode` eq `refine_last_search` | base | |
| `refrec.try_this_search_again_with_tis` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.what_about_all_fibrin_images_t` | followup | What about all fibrin images that exist | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.what_about_all_fibrin_images_t` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.what_about_those_nhp_samples_h` | followup | What about those NHP samples — how many were there | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.what_about_those_nhp_samples_h` | seed | How many D.SEQ IMPACT samples did the first search return | _(none)_ | base | |
| `refrec.what_sample_types_were_represe` | followup | What sample types were represented in those results | `parser_plan.mode` eq `ask_about_last_results`; `api_result_meta.source_mode` eq `new_search`; `last_reply` mentions `MUS` | base | |
| `refrec.what_sample_types_were_represe` | seed | Find mice treated with NDMA | `api_ok` true; `entity_sampletype_codes` contains `MUS` | base | |
| `refrec.which_of_those_are_males` | followup | Which of those are Males | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.which_of_those_are_males` | seed | Find me NHP samples from study IMPACT | `api_ok` true | base | |
| `refrec.which_of_those_samples_are_fro` | followup | Which of those samples are from 2022 | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.which_of_those_samples_are_fro` | seed | Find me mice treated with NDMA | `api_ok` true | base | |
| `refrec.which_samples_have_a_tumor_fra` | followup | Which samples have a tumor fraction of 100% | `parser_plan.mode` eq `ask_about_last_results` | base | |
| `refrec.which_samples_have_a_tumor_fra` | seed | Find me mice treated with NDMA | `api_ok` true | base | |

### reporting

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `report.build_a_pride_deposit_for_d_ms` | main | Build a PRIDE deposit for D.MSP-241114WHI-110-PUB and D.MSP-241114WHI-108-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `report_produced_output` true | base | |
| `report.build_an_sra_metadata_file_for` | main | Build an SRA metadata file for D.SEQ-230512FOR-29-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.build_be_a_geo_submission_for` | main | Build be a GEO submission for D.SEQ-230512FOR-287-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.build_be_an_sra_submission_for` | main | Build be an SRA submission for DNA-260402BMC-1 | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.build_me_a_full_nih_report_for` | main | Build me a full NIH Report for SRP in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)srp`; `reporter_result.ok` true; `reporter_result.samples.uuids_saved` gte `1`; `report_produced_output` true | base | |
| `report.build_me_a_full_rppr_for_srp_i` | main | Build me a full RPPR for SRP in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)SRP`; `report_produced_output` true | base | |
| `report.build_me_a_geo_submission_for` | main | Build me a geo submission for D.SEQ-240709KAM-1-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.build_me_a_geo_submission_for_2` | main | Build me a GEO Submission for D.SEQ-221031SHA-67-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.build_me_a_geo_submission_for_3` | main | Build me a GEO submission for D.SEQ-240422SHA-23-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.build_me_a_geo_submission_for_4` | main | Build me a GEO Submission for D.SEQ-241219BRY-5-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.build_me_a_sample_summary_for` | main | Build me a sample summary for MetNet in 2023 to 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)MetNet`; `report_produced_output` true | base | |
| `report.build_me_an_nih_report_for_the` | main | Build me an NIH report for the Kamm Project | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.build_me_an_sra_submission_for` | main | Build me an SRA submission for D.SEQ-230512FOR-288-PUB, D.SEQ-230512FOR-289-PUB, and D.SEQ-230512FOR-29-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.build_me_an_sra_submission_for_2` | main | Build me an SRA submission for D.SEQ-230512FOR-288-PUB D.SEQ-230512FOR-289-PUB D.SEQ-230512FOR-29-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.build_me_an_srp_rppr_for_2025` | main | Build me an SRP RPPR for 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)SRP`; `report_produced_output` true | base | |
| `report.create_a_geo_deposit_file_for` | main | Create a GEO deposit file for D.SEQ-241219BRY-5-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.create_a_pride_submission_for` | main | Create a PRIDE submission for D.MSP-230828GRI-4-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `report_produced_output` true | base | |
| `report.create_an_sra_submission_for_d` | main | Create an SRA submission for D.SEQ-230512FOR-288-PUB and D.SEQ-230512FOR-289-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.export_d_msp_230828gri_4_pub_f` | main | Export D.MSP-230828GRI-4-PUB for submission to the PRIDE repository | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `report_produced_output` true | base | |
| `report.find_me_all_samples_uploaded_i` | main | Find me all samples uploaded in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.full_nih_report_for_kamm_proje` | main | Full nih report for kamm project please | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.generate_an_annual_progress_re` | main | Generate an annual progress report for CSBC | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)CSBC`; `report_produced_output` true | base | |
| `report.generate_the_annual_progress_r` | main | Generate the annual progress report for the CGR project | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)CGR`; `report_produced_output` true | base | |
| `report.generate_the_rppr_for_the_metn` | main | Generate the RPPR for the MetNet project | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)MetNet`; `report_produced_output` true | base | |
| `report.geo_submission` | main | Build me a GEO Submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB. | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `reporter_result.merged_report` nonempty; `report_saved_files` nonempty; `report_produced_output` true | base | |
| `report.how_many_samples_are_in_the_gb` | main | How many samples are in the GBM study | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)GBM`; `report_produced_output` true | base | |
| `report.how_many_samples_did_metnet_up` | main | How many samples did MetNet upload in 2024 | `parser_plan.mode` eq `reporter`; `reporter_plan.project` matches_re `(?i)metnet`; `reporter_result.years_table` nonempty; `report_produced_output` true | base | |
| `report.how_many_samples_have_been_upl` | main | How many samples have been uploaded in the last 2 weeks | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_protocols_and` | main | How many samples, protocols, and published datasets does the MetNet project have | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)MetNet`; `report_produced_output` true | base | |
| `report.how_many_samples_were_added_to` | main | How many samples were added to the CGR project this year | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)CGR`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded` | main | How many samples were uploaded in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_10` | main | How many samples were uploaded yesterday | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_2` | main | How many samples were uploaded across all projects last quarter | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_3` | main | How many samples were uploaded for Impact in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_4` | main | How many  samples were uploaded in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_5` | main | How many samples were uploaded for CSBC over the last year | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)CSBC`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_6` | main | How many samples were uploaded for SRP from December 3rd 2023 to October 5th 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)SRP`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_7` | main | How many samples were uploaded from 2023 to 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_8` | main | How many samples were uploaded in the last 12 weeks | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.how_many_samples_were_uploaded_9` | main | How many samples were uploaded on 1/13/26 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.i_need_the_nih_progress_report` | main | I need the NIH progress report for the Kamm lab | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.i_need_to_submit_d_seq_231213f` | main | I need to submit D.SEQ-231213FOR-120-PUB, D.SEQ-231213FOR-121-PUB to the SRA. Can you generate the submission file | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `report_produced_output` true | base | |
| `report.i_need_to_submit_these_samples` | main | I need to submit these samples to GEO: D.SEQ-240422SHA-23-PUB, D.SEQ-240422SHA-24-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.i_need_to_submit_this_proteomi` | main | I need to submit this proteomics dataset to PRIDE: D.MSP-250606SHO-1-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `report_produced_output` true | base | |
| `report.make_a_pride_submission_for_d` | main | Make a PRIDE submission for D.MSP-230828GRI-4-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `report_produced_output` true | base | |
| `report.make_me_a_geo_submission_for_a` | main | Make me a GEO submission for A.SCXP-220824SHA-1-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.make_me_a_geo_submission_for_d` | main | Make me a GEO submission for  D.SEQ-250409KAM-20-PUB, D.SEQ-250409KAM-22-PUB, and D.SEQ-250409KAM-2-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |
| `report.pride_submission` | main | Please create a PRIDE submission for the mass spec sample D.MSP-230828GRI-4-PUB. | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `PRIDE`; `reporter_result.merged_report` nonempty; `report_saved_files` nonempty; `report_produced_output` true | base | |
| `report.progress_kamm` | main | Put together an annual progress report for the Kamm project — I need it for our NIH grant. | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.protocols_cgr` | main | Show me protocols registered for the CGR project. | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.summary_mode` eq `protocols`; `reporter_plan.project` matches_re `(?i)CGR`; `report_produced_output` true | base | |
| `report.published_srp` | main | Show me published samples for the SRP project. | `parser_plan.mode` eq `reporter`; `reporter_plan.summary_mode` eq `published`; `reporter_plan.project` matches_re `(?i)SRP`; `report_produced_output` true | base | |
| `report.put_together_an_annual_progres` | main | Put together an annual progress report for the Kamm project - I need it for our NIH grant renewal | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_result.ok` true; `reporter_result.samples.uuids_saved` gte `1`; `reporter_result.samples.uuids_saved` lte `20000`; `reporter_result.samples.labs_table` nonempty; `report_produced_output` true | base | |
| `report.samples_last_month` | main | How many samples were uploaded last month? | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.summary_mode` eq `samples`; `report_produced_output` true | base | |
| `report.samples_uploaded_impact` | main | How many samples were uploaded for IMPACT from 2023 to 2025? | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.summary_mode` eq `samples`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.show_me_samples_uploaded_for_i` | main | Show me samples uploaded for Impact from 2024 to 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.sra_submission` | main | Build me an SRA submission for D.SEQ-230512FOR-288-PUB, D.SEQ-230512FOR-289-PUB. | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `SRA`; `api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx` true; `api_artifact.merged_report_SRA_SRA_biosample_filled.xlsx` true; `api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx.rows_gte` gte `2`; `report_produced_output` true | base | |
| `report.what_has_been_derived_from_nhp` | main | What has been derived from NHP-220630FLY-5-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `report_produced_output` true | base | |
| `report.what_has_impact_published_in_2` | main | What has Impact published in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.what_kamm_samples_were_uploade` | main | What kamm samples were uploaded in 2024 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.what_samples_have_been_uploade` | main | What samples have been uploaded for Impact in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.what_samples_were_uploaded_for` | main | What samples were uploaded for Impact from June 3rd 2023 to October 25th 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.what_samples_were_uploaded_for_2` | main | What samples were uploaded for impact in 2025 | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.project` matches_re `(?i)IMPACT`; `report_produced_output` true | base | |
| `report.whats_the_nih_reporter_link_fo` | main | Whats the NIH Reporter link for the Kamm Project? What kind of research do they do | `parser_plan.mode` eq `reporter`; `reporter_plan.reporter_mode` eq `summary`; `reporter_plan.reporter_context.lab_codes` contains `KAM`; `report_produced_output` true | base | |
| `report.write_me_a_geo_submission_for` | main | write me a geo submission for D.SEQ-240422SHA-23-PUB, D.SEQ-240422SHA-24-PUB | `parser_plan.mode` eq `reporter`; `reporter_plan.report_type` eq `GEO`; `report_produced_output` true | base | |

### routing_graph

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `routing.gbm_study_count` | main | How many samples are in the GBM study? | `parser_plan.mode` eq `graph_query`; `neo4j_ok` true; `graph_result.parameters.project` matches_re `(?i)gbm`; `last_reply` matches_re `(?s)^(?!.*(\b[45]\d[,.]\d{3}\b|\b[45]\d{4}\b)).*$` | base | |

### routing_lab

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `routing.lab_ooc_kamm_casual` | main | what about organ on chips in the kamm lab? | `parser_plan.mode` eq `new_search`; `api_plan.requestBody.filter_searchText` contains `KAM` | base | |
| `routing.lab_ooc_kamm_count` | main | How many organ on chips exist in the Kamm lab | `parser_plan.mode` eq `new_search`; `api_plan.requestBody.filter_searchText` contains `KAM` | base | |

### search_advanced

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `advanced.bacteria_mtb` | main | Find bacteria samples with strain mTB. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `BAC`; `api_plan.requestBody.filter_searchText` contains `mTB`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.basic_ndma` | main | Find me mice treated with NDMA. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_plan.endpoint` contains `advanced_search`; `api_plan.requestBody.filter_searchText` contains `NDMA`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.cd8_antibodies` | main | Find me all CD8 antibodies in the database. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `AB`; `api_plan.endpoint` contains `advanced_search`; `api_plan.requestBody.filter_searchText` contains `CD8`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.do_we_have_any_western_blot_da` | main | Do we have any western blot data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.female_mice` | main | Find female mouse samples. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_all_mouse_samples_in_the` | main | Find all mouse samples in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_all_nhp_samples_in_the_da` | main | Find all NHP samples in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_all_tissue_samples_in_the` | main | Find all tissue samples in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `TIS`; `api_outcome_observed` true | base | |
| `advanced.find_cell_samples_with_celltyp` | main | Find cell samples with CellType set to T Cell | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `CEL`; `api_outcome_observed` true | base | |
| `advanced.find_mass_spectrometry_data_of` | main | Find mass spectrometry data of type proteomics | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `D.MSP`; `api_outcome_observed` true | base | |
| `advanced.find_me_all_fibrin_images_on_o` | main | Find me all fibrin images on omero | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_all_monkeys_in_the_dat` | main | Find me all monkeys in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_me_all_of_the_fibrin_imag` | main | Find me all of the fibrin images that are on omero | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_all_samples_associated` | main | Find me all samples associated with CD8 Antibodies | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `AB`; `api_outcome_observed` true | base | |
| `advanced.find_me_cd8_antibodies` | main | Find me cd8 antibodies | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `AB`; `api_outcome_observed` true | base | |
| `advanced.find_me_cd8_antibodies_associa` | main | Find me CD8 Antibodies associated with the Patient Visit Assay | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `AB`; `api_outcome_observed` true | base | |
| `advanced.find_me_cd8_depleted_monkeys` | main | Find me cd8 depleted monkeys | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_me_cell_line_samples` | main | Find me cell line samples | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `CEL`; `api_outcome_observed` true | base | |
| `advanced.find_me_d_seq_samples_in_proje` | main | Find me D.SEQ samples in project IMPACT | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `D.SEQ`; `api_outcome_observed` true | base | |
| `advanced.find_me_dna_samples` | main | Find me DNA samples | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `DNA`; `api_outcome_observed` true | base | |
| `advanced.find_me_extravasation_images` | main | Find me extravasation images | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_fibrin_images` | main | Find me fibrin images | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_fibrin_imaging_data` | main | Find me fibrin imaging data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_gpt_assay_data` | main | Find me GPT assay data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_images_associated_with` | main | Find me images associated with fibrin | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_mice` | main | Find me mice | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_me_mice_associated_with_n` | main | Find me mice associated with ndma | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_me_mice_treated_with_mtb` | main | Find me mice treated with mTB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_me_mice_treated_with_tube` | main | Find me mice treated with tuberculosis | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_me_monkeys` | main | Find me monkeys | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_me_monkeys_depleted_of_cd` | main | Find me monkeys depleted of CD8 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_me_ndma_treated_mice` | main | Find me ndma treated mice | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_me_nhp_samples_from_study` | main | Find me NHP samples from study GBM | `last_reply` matches_re `(?is)(no NHP|no samples|zero|0 samples|does not exist|not a (study|project|investigation)|could not find|not found)`; `last_reply` matches_re `(?s)^(?!.*\b[1-9][0-9]*\s+(?:NHP|samples|records)\b).*$` | base | |
| `advanced.find_me_nhp_samples_from_study_2` | main | Find me NHP samples from study IMPACT | `route` eq `nextseek_query`; `graph_not_truncated` true; `last_reply` matches_re `(NHP-|no samples|could not)`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_nhp_samples_from_study_ns_graph` | main | Find me NHP samples from study GBM | `route` eq `nextseek_query`; `last_reply` matches_re `(?s)^(?!.*(\b[45]\d[,.]\d{3}\b|\b[45]\d{4}\b)).*$` | overlay | |
| `advanced.find_me_nhp_samples_in_a_nonex` | main | Find me NHP samples in a nonexistent investigation called XYZQQQ | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.find_me_patients_from_the_gbm` | main | Find me patients from the GBM study | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `PAT`; `api_outcome_observed` true | base | |
| `advanced.find_me_rna_samples` | main | Find me RNA samples | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `RNA`; `api_outcome_observed` true | base | |
| `advanced.find_me_samples_associated_wit` | main | Find me samples associated with cd8 depletion | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_scrna_seq_clustering_r` | main | Find me scRNA-seq clustering results | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_sequencing_files_assoc` | main | Find me sequencing files associated with Short Read Sequencing | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_me_tissues_that_have_tumo` | main | Find me tissues that have tumor value data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `TIS`; `api_outcome_observed` true | base | |
| `advanced.find_me_tumor_values_with_pati` | main | Find me tumor values with patient 4 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `PAT`; `api_outcome_observed` true | base | |
| `advanced.find_mice_treated_with_50mg_nd` | main | Find mice treated with 50mg NDMA for 6 weeks from the water study | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_mice_treated_with_50mg_nd_2` | main | Find mice treated with 50mg NDMA from the water study | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_mice_treated_with_ndma` | main | Find mice treated with NDMA | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `MUS`; `api_outcome_observed` true | base | |
| `advanced.find_samples_containing_the_ke` | main | Find samples containing the keyword NDMA | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.find_samples_of_pbmc_type_from` | main | Find samples of PBMC type from the GBM study that also have sequencing data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `D.SEQ`; `api_outcome_observed` true | base | |
| `advanced.find_tissue_samples_with_organ` | main | Find tissue samples with organ type Liver | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `TIS`; `api_outcome_observed` true | base | |
| `advanced.find_tissues_that_underwent_sh` | main | Find tissues that underwent short read sequencing | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `TIS`; `api_outcome_observed` true | base | |
| `advanced.nextseq_instrument` | main | Find sequencing data generated on a NextSeq instrument. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_plan.requestBody.filter_searchText` contains `NextSeq`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.rna_rin_score` | main | Find RNA samples with a RIN score greater than 7. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `RNA`; `api_plan.requestBody.sampletype` eq `RNA`; `api_plan.requestBody.filter_searchText` contains `RIN`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.show_me_all_facs_data_for_the` | main | Show me all FACS data for the monkeys | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `NHP`; `api_outcome_observed` true | base | |
| `advanced.show_me_all_gbm_tumor_data_for` | main | Show me all gbm tumor data for patient 4 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `PAT`; `api_outcome_observed` true | base | |
| `advanced.show_me_analyzed_sequencing_da` | main | Show me analyzed sequencing data | `route` eq `nextseek_query`; `api_ok` true; `api_result_meta.row_count` gte `1`; `api_outcome_observed` true | base | |
| `advanced.show_me_flow_cytometry_data_wi` | main | Show me flow cytometry data with PFA fixation | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `D.FLOW`; `api_outcome_observed` true | base | |
| `advanced.show_me_tis_samples_that_have` | main | Show me TIS samples that have been processed via Tissue Collection | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `TIS`; `api_outcome_observed` true | base | |
| `advanced.show_me_tumor_data_for_patient` | main | Show me tumor data for patient 4 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `PAT`; `api_outcome_observed` true | base | |
| `advanced.what_about_srp_samples_in_both` | main | What about SRP samples in both 2022 and 2023 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_fibrin_images_exist` | main | What fibrin images exist | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_fibrin_images_exist_in_th` | main | What fibrin images exist in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_gpt_data_exists_in_the_da` | main | What GPT data exists in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_gpt_data_is_in_the_databa` | main | What GPT Data is in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_monkeys_exist` | main | What monkeys exist in the database? | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `NHP`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_organ_on_chip_images_exis` | main | What organ on chip images exist | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |
| `advanced.what_proteomics_data_exists_in` | main | What proteomics data exists in the database | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `entity_sampletype_codes` contains `D.MSP`; `api_outcome_observed` true | base | |
| `advanced.zero_result_zebrafish` | main | Find me all zebrafish samples in the database. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `advanced_search`; `api_ok` true; `api_outcome_observed` true | base | |

### search_parents_by_child

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `pbct.find_all_mice_that_have_both_s` | main | Find all mice that have both sequencing and imaging data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `MUS`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_monkeys_that_have_both` | main | Find me monkeys that have both flow and sequencing data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_monkeys_that_have_both_2` | main | Find me monkeys that have both flow and sequencing data associated with it | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_monkeys_that_have_flow` | main | Find me monkeys that have flow cytometry and sequencing data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_monkeys_that_have_imag` | main | Find me monkeys that have imaging and sequencing data associated with it | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_monkeys_who_have_seque` | main | Find me monkeys who have sequencing and flow cytometry data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_non_human_primate_samp` | main | Find me non human primate samples that have flow and sequencing data associated with it | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_me_samples_that_have_flow` | main | Find me samples that have flow and sequencing data associated with it | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `D.SEQ`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.find_monkeys_that_have_both_fl` | main | Find monkeys that have both flow cytometry and sequencing data | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `NHP`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.flow_and_msp` | main | Find samples with both flow cytometry and mass spec data. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.mice_crispr` | main | Which mice have CRISPR knockout data? | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.mice_msp_rnaseq` | main | Which mice have both mass spectrometry and RNA sequencing data? | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.monkeys_flow_and_seq` | main | Find me monkeys that have flow and sequencing data. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `NHP`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.monkeys_proteomics_flow` | main | Find monkeys with proteomics and flow cytometry data. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `NHP`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.no_match` | main | Find zebrafish with single-cell ATAC-seq data. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.patients_seq_imaging` | main | Show me patients that have both sequencing and imaging data. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.tell_me_all_tissues_that_have` | main | Tell me all tissues that have DNA and imaging samples associated with them | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `parents_by_child_types`; `entity_sampletype_codes` contains `TIS`; `api_ok` true; `api_outcome_observed` true | base | |
| `pbct.three_assays` | main | Find mice with flow cytometry, sequencing, AND mass spec data. | `parser_plan.mode` eq `new_search`; `entity_sampletype_codes` contains `MUS`; `api_plan.endpoint` contains `parents_by_child_types`; `api_ok` true; `api_outcome_observed` true | base | |

### search_retrieve

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `retrieve.batch_two_dseq` | main | Retrieve D.SEQ-221031SHA-65-PUB and D.SEQ-221031SHA-67-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.can_you_return_to_me_all_sampl` | main | Can you return to me all samples associated with CEL-250319WHI-1-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.cel_single` | main | Fetch CEL-250319WHI-1-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.get_the_full_details_for_d_seq` | main | Get the full details for D.SEQ-221031SHA-67-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.large_batch` | main | Look up these samples: D.SEQ-230512FOR-288-PUB, D.SEQ-230512FOR-289-PUB, D.SEQ-221031SHA-65-PUB, D.SEQ-221031SHA-67-PUB, NHP-220630FLY-5-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.metadata_filter` | main | Pull the published version of D.SEQ-230512FOR-288-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.mixed_valid_invalid` | main | Get me NHP-220630FLY-5-PUB and XYZ-999999ZZZ-1-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.pull_up_the_record_for_tis_220` | main | Pull up the record for TIS-220831FLY-26-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_all_samples_associate` | main | Retrieve all samples associated with NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_all_samples_associate_2` | main | Retrieve all samples associated with: 	NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_me_all_associated_sam` | main | Retrieve me all associated samples of NHP-220630FLY-6-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_me_all_associated_sam_2` | main | Retrieve me all associated samples of 	NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_me_all_associated_sam_3` | main | Retrieve me all associated samples with NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.retrieve_me_all_samples_associ` | main | Retrieve me all samples associated with NHP-220630FLY-6-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.run_a_sample_retrieval_on_nhp` | main | Run a sample retrieval on NHP-220630FLY-6-PUB and NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.show_me_everything_we_know_abo` | main | Show me everything we know about CEL-230131KAM-1-PUB2 | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.single_msp` | main | Show me details for D.MSP-230828GRI-4-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.single_nhp` | main | Retrieve all samples associated with: NHP-220630FLY-5-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.then_inspect` | inspect | What's the Scientist field on those samples? | `parser_plan.mode` eq `ask_about_last_results`; `last_reply` mentions `Scientist`; `api_ok` true; `api_outcome_observed` true | base | |
| `retrieve.then_inspect` | seed | Retrieve D.SEQ-221031SHA-65-PUB and D.SEQ-221031SHA-67-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `retrieve`; `api_ok` true | base | |

### search_tree

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `tree.can_you_retrieve_the_full_samp` | main | Can you retrieve the full sample tree for MUS-220122SAS-334-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `MUS-220122SAS-334-PUB`; `api_ok` true | base | |
| `tree.cel_descendants` | main | Show me all samples derived from CEL-250319WHI-1-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `CEL-250319WHI-1-PUB`; `api_ok` true | base | |
| `tree.derivation_of_dmsp` | main | What is D.MSP-230828GRI-4-PUB derived from? | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_ok` true | base | |
| `tree.dseq_leaf` | main | Show me the derivation tree for D.SEQ-221031SHA-67-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `D.SEQ-221031SHA-67-PUB`; `api_ok` true | base | |
| `tree.find_me_all_samples_derived_fr` | main | Find me all samples derived from 	NHP-220630FLY-5-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `NHP-220630FLY-5-PUB`; `api_ok` true | base | |
| `tree.get_the_tissue_sample_tree_and` | main | Get the tissue sample tree and any derived tissue samples for the sample with ID NHP-220630FLY-1-PUB using the correct GET-based sample-tree endpoint, and return the list of derived tissue samples (children and descendants) with their IDs, types, and relationships | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `NHP-220630FLY-1-PUB`; `api_ok` true | base | |
| `tree.missing_uid` | main | Show me children of XXX-999999ZZZ-1-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_result_meta.status_code` eq `404` | base | |
| `tree.msp_lineage` | main | What's the lineage of D.MSP-230828GRI-4-PUB? | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `D.MSP-230828GRI-4-PUB`; `api_ok` true | base | |
| `tree.nhp_lineage` | main | What samples descend from NHP-220630FLY-5-PUB? | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `NHP-220630FLY-5-PUB`; `api_ok` true | base | |
| `tree.show_me_the_full_lineage_of_d` | main | Show me the full lineage of D.SEQ-221031SHA-67-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `D.SEQ-221031SHA-67-PUB`; `api_ok` true | base | |
| `tree.show_me_tissue_samples_derived` | main | Show me tissue samples derived from NHP-220630FLY-1-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `NHP-220630FLY-1-PUB`; `api_ok` true | base | |
| `tree.then_ask_about` | follow_up | Of those, which are sequencing samples? | `parser_plan.mode` eq `ask_about_last_results`; `chat_log.length` gte `2`; `api_ok` true | base | |
| `tree.then_ask_about` | seed | Show me everything derived from NHP-220630FLY-5-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_ok` true | base | |
| `tree.walk_up` | main | Walk me up the derivation chain from D.SEQ-230512FOR-288-PUB. | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `D.SEQ-230512FOR-288-PUB`; `api_ok` true | base | |
| `tree.what_is_the_parent_chain_for_n` | main | What is the parent chain for NHP-220830FLY-42-PUB | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_plan.endpoint` contains `NHP-220830FLY-42-PUB`; `api_ok` true | base | |
| `tree.yes_it_is_try_using_the_sample` | main | Yes it is. Try using the sample-tree endpoint | `parser_plan.mode` eq `new_search`; `api_plan.endpoint` contains `sample-tree`; `api_ok` true | base | |

### system_question

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `sys.capabilities` | main | What can you do? List your capabilities. | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.dflow_vs_aflow` | main | What is the difference between D.FLOW and A.FLOW? | `parser_plan.mode` eq `system_question`; `last_reply` mentions `flow` | base | |
| `sys.dseq_definition` | main | What is a D.SEQ sample? | `parser_plan.mode` eq `system_question`; `last_reply` mentions `sequencing` | base | |
| `sys.how_do_i_find_samples_associat` | main | How do I find samples associated with a specific protocol or assay | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.list_all_available_assay_types` | main | List all available assay types in the system | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.sampletypes_available` | main | What sample types are available in the system? | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.show_me_all_assays_i_have_acce` | main | Show me all assays I have access to | `parser_plan.mode` matches_re `(system_question|new_search)`; `last_reply` matches_re `(assay|Assay)`; `api_result_meta.row_count` gte `300`; `last_reply` mentions `324` | base | |
| `sys.show_me_all_investigations_tha` | main | Show me all investigations that I have access to | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.study_search_howto` | main | Can I search for samples by which study they belong to? | `parser_plan.mode` eq `system_question`; `last_reply` mentions `study` | base | |
| `sys.tell_me_about_the_radr_assay` | main | Tell me about the RaDr Assay | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.tell_me_about_tissue_collectio` | main | Tell me about Tissue collection | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.tissue_metadata_fields` | main | What metadata fields are tracked for a Tissue Sample? | `parser_plan.mode` eq `system_question`; `entity_sampletype_codes` contains `TIS` | base | |
| `sys.what_api_endpoints_can_i_query` | main | What API endpoints can I query | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_assay_types_are_registere` | main | What assay types are registered in NExtSEEK | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_assays_are_available_to_m` | main | What assays are available to me | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_assays_do_i_have_access_t` | main | What assays do I have access to | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_attributes_can_i_filter_o` | main | What attributes can I filter on for NHP samples | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_can_nextseek_do` | main | What can NExtSEEK do | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_can_the_system_do` | main | What can the system do | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_can_you_do` | main | What can you do | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_is_a_d_seq_assay` | main | What is a D.SEQ assay | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_kinds_of_reports_can_i_ge` | main | What kinds of reports can I generate | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_mice_are_treated_with_ndm` | main | What mice are treated with NDMA | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_sample_types_are_most_com` | main | What sample types are most common across all investigations | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_sample_types_can_i_search` | main | What sample types can I search for in NExtSEEK | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.what_types_of_searches_can_you` | main | What types of searches can you do | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |
| `sys.who_is_the_current_user` | main | Who is the current user | `parser_plan.mode` eq `system_question`; `last_reply` nonempty | base | |

### unsupported

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `unsup.compare_gene_expression_betwee` | main | Compare gene expression between NDMA-treated and control mice | `route` eq `container_cc` | base | |
| `unsup.domain_chemistry` | main | Explain what NDMA is and why it's carcinogenic. | `route` matches_re `(unrelated|container_cc)` | base | |
| `unsup.give_me_a_bar_chart_of_all_sam` | main | Give me a bar chart of all sample type counts | `route` eq `container_cc` | base | |
| `unsup.is_treatment_a_significantly_b` | main | Is treatment A significantly better than treatment B based on our sequencing data? | `route` eq `container_cc`; `last_reply` nonempty | base | |
| `unsup.make_me_a_bar_chart_of_sample` | main | Make me a bar chart of sample counts broken down by type across all projects in NExtSEEK | `route` eq `container_cc` | base | |
| `unsup.make_me_a_bar_chart_of_sample_2` | main | Make me a bar chart of sample counts by type | `route` eq `container_cc` | base | |
| `unsup.weather` | main | What is today's weather forecast in Boston? | `route` eq `unrelated` | base | |

### writes_unsupported

| id | turn | question | criteria | src | disposition |
|---|---|---|---|---|---|
| `write.can_you_create_a_new_investiga` | main | Can you create a new investigation for me and call is "Investigation API Test" | `route` eq `container_cc` | base | |
| `write.can_you_create_an_investigatio` | main | Can you create an Investigation named "TEST CD AGENT" | `route` eq `container_cc` | base | |
| `write.can_you_create_an_investigatio_2` | main | Can you create an Investigation named "TEST CD API" | `route` eq `container_cc` | base | |
| `write.can_you_create_me_an_investiga` | main | Can you create me an Investigation called "Test API Charlie" | `route` eq `container_cc` | base | |
| `write.create_a_new_investigation_tes` | main | Create a new investigation "TEST API CD" for me | `route` eq `container_cc` | base | |
| `write.create_investigation_testing_4` | main | Create Investigation 'Testing 404' | `route` eq `container_cc` | base | |
| `write.create_me_an_investigation_cal` | main | Create me an investigation called "API TEST 2" with project id = 2 | `route` eq `container_cc` | base | |
| `write.create_me_an_investigation_cal_2` | main | Create me an investigation called API TEST 2 | `route` eq `container_cc` | base | |
| `write.create_me_investigation_oh_yea` | main | Create me investigation "Oh yeah! Lets go" | `route` eq `container_cc` | base | |
| `write.create_me_investigation_test_c` | main | Create me investigation TEST-CD with project id 2 | `route` eq `container_cc`; `last_reply` nonempty | base | |
| `write.create_me_investigation_test_w` | main | Create me investigation "TEST WOW TEST" | `route` eq `container_cc` | base | |
| `write.create_me_investigation_testin` | main | Create me investigation "Testing Investigation. Still Testing" | `route` eq `container_cc`; `last_reply` nonempty; `last_reply` matches_re `(?s)^(?!.*(attempt budget|stop-after-2|thrash)).*$`; `last_reply` matches_re `(?is)(created|confirm|would you like|shall I|proceed|investigation id)` | base | |
| `write.create_me_investigation_testin_2` | main | Create me investigation "TESTING" | `route` eq `container_cc` | base | |
| `write.download_all_samples_from_the` | main | Download all samples from the database as a spreadsheet | `route` eq `container_cc` | base | |
| `write.export_all_metadata_for_nhp_22` | main | Export all metadata for NHP-220630FLY-1-PUB and its derived samples to Excel | `route` eq `container_cc` | base | |
| `write.export_all_metadata_for_nhp_22_2` | main | Export all metadata for NHP-220630FLY-1-PUB and its derived samples to JSON | `route` eq `container_cc` | base | |
| `write.register_a_new_mouse_sample_wi` | main | Register a new mouse sample with strain C57BL/6 and sex Male | `route` eq `container_cc` | base | |
| `write.update_the_scientist_field_on` | main | Update the scientist field on NHP-220630FLY-1-PUB to Damn Daniel | `route` eq `container_cc` | base | |
| `write.update_the_scientist_field_on_2` | main | Update the scientist field on NHP-220630FLY-1-PUB to Jane Doe | `route` eq `container_cc` | base | |
