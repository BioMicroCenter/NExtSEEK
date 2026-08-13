# Ad-hoc question inventory — real queries run against the dev box

**101 distinct questions** actually asked in chat, that do **not** appear anywhere in
`chat_nextseek/e2e/catalog.json` or `nessie_tests/overlay.json`.

**Historical snapshot — the recipe below no longer runs.** Recovered on 2026-07-30 from
`assistant_query_task.query` on the dev box (451 tasks, 184 distinct) plus
`outputs/*/api_requests.json` locally, deduped case/punctuation-insensitively against the
447-turn corpus, which was then resolved by pointing `corpus.merged()` at
`catalog.json` + `overlay.json`. Since the 2026-08-04 unification `merged()` reads
`nessie_tests/corpus.json` and nothing else — `_read_unified` requires `version == 2`, so
an overlay path now raises `ValueError` instead of silently resolving to zero variants.
To redo the dedupe today, diff against `corpus.merged()` with no argument — but note it
now resolves 314 turns, not 447, so 100 retirements have moved questions INTO the
"not in the corpus" bucket and the count will exceed 101.
The local `outputs/` folder contributed no questions the dev box DB did not
already have, so the DB is a superset.

A further **12** CC infrastructure probes (bash/codeword plumbing tests such as
`Use bash to echo this codeword exactly: BANANA-42`) are listed at the end and excluded from
review — they test the container, not the product.

## Why this matters

The corpus is 447 turns of largely generated variants. Real usage is concentrated somewhere
quite different, and **the single most-run question in the whole history is harmonization**,
which has zero corpus coverage.

| theme | distinct questions |
|---|---|
| search / other | 47 |
| harmonization | 13 |
| reingest / upload sheet | 13 |
| scientist / attribute search | 10 |
| reporting / submission | 5 |
| lineage / traversal | 5 |
| continuation | 2 |
| catalog / system | 2 |
| off-topic | 2 |
| pipeline / nf-core | 2 |

## Questions

`runs` = how many times it was asked. Suggested disposition: `ADD` / `ADD-VARIANT` / `SKIP`.

| runs | first seen | theme | question | disposition |
|---|---|---|---|---|
| 11 | 2026-07-09 | harmonization | I noticed that some of these genotype terms from look similar, could you attempt to normalize them? | |
| 9 | 2026-07-09 | scientist / attribute search | find TIS samples where Scientist is Owen Leddy | |
| 7 | 2026-07-10 | reingest / upload sheet | Can you create an update sheet for re-ingestion? | |
| 7 | 2026-07-24 | search / other | Find NHP sequencing data. | |
| 6 | 2026-07-10 | continuation | Please continue where you left off and complete my previous request. | |
| 6 | 2026-07-27 | search / other | Find sequencing data for non-human primates. | |
| 3 | 2026-07-09 | harmonization | Please list all mice associated with NDMA and create a histogram stratified by genotype. | |
| 3 | 2026-06-26 | search / other | Find me mice treated with NDMA, including their genotype. | |
| 3 | 2026-07-05 | catalog / system | What steps would summarize NDMA samples? | |
| 3 | 2026-07-09 | scientist / attribute search | Find every TIS (Tissue Sample) sample whose Scientist is Owen Leddy. Show each sample's UID and all of its attributes. | |
| 3 | 2026-07-29 | reingest / upload sheet | Build me a NExtSEEK reingest upload sheet from the pipeline outputs in /net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_rnaseq_260723_205359_0/ | |
| 2 | 2026-06-26 | harmonization | Create a histogram of mice treated with NDMA stratified by genotype. | |
| 2 | 2026-06-26 | off-topic | Write me a poem about cats | |
| 2 | 2026-07-05 | reporting / submission | Give me a published-data summary report for the Published Data project. | |
| 2 | 2026-07-05 | reporting / submission | Generate a GEO submission for NExtSEEK sample A.ADCD-250312ALT-1-PUB | |
| 2 | 2026-07-09 | catalog / system | list projects | |
| 2 | 2026-07-10 | scientist / attribute search | Find every TIS sample whose Scientist is Owen Leddy in the 'Published Data' project, and show each one's UID and all of its attributes. | |
| 2 | 2026-07-10 | reingest / upload sheet | Please continue and finish building and validating the workbook; report the validated workbook path and verdict. | |
| 2 | 2026-07-23 | search / other | Find me NHP samples with sequencing data | |
| 2 | 2026-07-29 | pipeline / nf-core | The scrnaseq run at /net/bmc-pub10/data1/bmc/pipeline_cd/runs/nfcore_gideon-4wk_260711_024438_0 has finished � register its outputs as new A.SCXP analysis samples. | |
| 1 | 2026-06-22 | search / other | Hey | |
| 1 | 2026-07-15 | search / other | howdy- what can you do? | |
| 1 | 2026-06-25 | search / other | How do I get started with using NExtSEEK? | |
| 1 | 2026-06-25 | search / other | how many samples are in the SRP study | |
| 1 | 2026-06-26 | search / other | Write and run a Python script that pulls the published samples from NExtSEEK and saves to a file the UIDs of any that are missing an organism value, then tell me the output file path. | |
| 1 | 2026-06-26 | search / other | Create a file at /data/scratch/proof.txt whose entire contents are exactly: DEPLOYED-OK-1782448755 . Then reply with that exact token and nothing else. | |
| 1 | 2026-06-26 | harmonization | Create a histogram of mice treated with NDMA stratified by study. | |
| 1 | 2026-06-26 | search / other | Find all mice treated with NDMA, grouped by study, with the count of mice in each study. | |
| 1 | 2026-06-26 | search / other | Find all mice treated with NDMA. For each one, tell me which study it belongs to, and give me the full count of NDMA-treated mice in every study (list all studies, not just the top few). | |
| 1 | 2026-06-26 | search / other | For those mice treated with NDMA, how many are there of each genotype? Give me the full count for every genotype. | |
| 1 | 2026-06-26 | pipeline / nf-core | Build an nf-core rnaseq run for D.SEQ-240910LAU-135-PUB, D.SEQ-240910LAU-136-PUB, D.SEQ-240910LAU-137-PUB, D.SEQ-240910LAU-94-PUB, grouped by treatment and dose. | |
| 1 | 2026-06-26 | off-topic | Write me a poem about NDMA. | |
| 1 | 2026-06-26 | search / other | Hi Nessie, what are all the strains of mice that we have? | |
| 1 | 2026-06-26 | search / other | List all mouse strains in the database. | |
| 1 | 2026-06-29 | search / other | Briefly say hello and nothing else. | |
| 1 | 2026-06-29 | search / other | Find me samples | |
| 1 | 2026-06-29 | search / other | Use bash to echo FRESHTEST-OK and confirm | |
| 1 | 2026-06-30 | reporting / submission | Use bash to create /data/scratch/step2_probe.txt containing exactly STEP2-ARTIFACT-ROOT, then sleep 20, then report the file path and exact contents. | |
| 1 | 2026-07-04 | search / other | Create a plot of mice treated with NDMA stratified by genotype. | |
| 1 | 2026-07-05 | search / other | search for mouse samples treated with NDMA and tell me what you find | |
| 1 | 2026-07-05 | lineage / traversal | show me the lineage graph of mouse samples | |
| 1 | 2026-07-05 | search / other | How many samples are in the Published Data project? | |
| 1 | 2026-07-06 | search / other | Find D.SEQ samples for the SHA lab, then narrow those results to samples collected after 2023. | |
| 1 | 2026-07-06 | search / other | Find NHP samples from the IV-BCG macaque study that also have flow cytometry data. | |
| 1 | 2026-07-09 | search / other | Please list all mice associated with NDMA. | |
| 1 | 2026-07-09 | scientist / attribute search | Find every TIS Tissue Sample whose Scientist is Owen Leddy and show each UID with its attributes | |
| 1 | 2026-07-09 | harmonization | Good but make the histogram bars yellow instead of blue. | |
| 1 | 2026-07-09 | scientist / attribute search | Find me all samples from the scientist Joanne Flynn. | |
| 1 | 2026-07-09 | search / other | Find me all the famous from Joanne Flynn | |
| 1 | 2026-07-10 | scientist / attribute search | Find every TIS sample whose Scientist is Owen Leddy in the project 'Published Data' | |
| 1 | 2026-07-10 | reingest / upload sheet | Can you prepare an update sheet to update these samples? | |
| 1 | 2026-07-10 | search / other | How many mice are associated with NDMA? | |
| 1 | 2026-07-10 | reingest / upload sheet | Use the nextseek-batch-upload skill to build (do NOT upload) a NExtSEEK batch-upload update sheet for these three Published Data mouse samples: MUS-200901ENG-23-PUB, MUS-200901ENG-24-PUB, MU | |
| 1 | 2026-07-10 | reingest / upload sheet | Please continue where you left off and finish building and validating the update sheet; report the workbook path and validation verdict. | |
| 1 | 2026-07-10 | reingest / upload sheet | The project is "Published Data" � resolve against that single project, one sheet spanning all cohorts. Yes, I confirm the 167-row target set with the 6 genotype mappings; exclude the 21 unch | |
| 1 | 2026-07-10 | reingest / upload sheet | Please continue where you left off and finish the build+validate for the 3 UIDs (MUS-200901ENG-23-PUB, MUS-200901ENG-24-PUB, MUS-200901ENG-25-PUB, Scientist=Jane Doe, project Published Data) | |
| 1 | 2026-07-11 | reingest / upload sheet | Yes � build and validate the update sheet for the Published Data project, using the changed rows you identified. | |
| 1 | 2026-07-11 | harmonization | Please list all mice treated with NDMA and create a histogram stratified by genotype. | |
| 1 | 2026-07-11 | search / other | Try again | |
| 1 | 2026-07-11 | harmonization | Please create an update sheet for re-ingestion of these normalized genotypes. | |
| 1 | 2026-07-11 | search / other | Confirmed project is "Published Data". | |
| 1 | 2026-07-11 | reingest / upload sheet | Confirmed project is "Published Data". Please produce batch upload workbook. | |
| 1 | 2026-07-12 | harmonization | erk -- ok. the real issue (and it's minor but critical) is you went backwards. the LONG version is the correct naming... 'RGA' is very incorrect. Harmonization should discard that for the lo | |
| 1 | 2026-07-12 | search / other | RG = RaDR+/+; GPT+/+ I believe. | |
| 1 | 2026-07-12 | reingest / upload sheet | RG = RaDR+/+; GPT+/+ I believe. Also I think RaDR R/R; gpt g/g can be represented as RaDR+/+; GPT-/-. Please produce batch upload workbook. | |
| 1 | 2026-07-12 | reingest / upload sheet | RG = RaDR+/+; GPT+/+. Please produce batch upload workbook. | |
| 1 | 2026-07-15 | search / other | what sequencing samples are available from the Metnet project | |
| 1 | 2026-07-15 | search / other | hello nessie | |
| 1 | 2026-07-15 | search / other | what sequencing samples are associated with the engelward lab | |
| 1 | 2026-07-15 | harmonization | Find me mice treated with ndma and make a histogram of their genotypes | |
| 1 | 2026-07-23 | search / other | ping | |
| 1 | 2026-07-23 | search / other | Find me all mice treated with NDMA | |
| 1 | 2026-07-23 | harmonization | Now create a histogram of the results and stratify the mice by genotype | |
| 1 | 2026-07-23 | harmonization | Find all mice treated with NDMA and make a histogram stratified by genotype | |
| 1 | 2026-07-23 | search / other | Find me non-human primate samples with RNA-seq data | |
| 1 | 2026-07-24 | reporting / submission | What is the NIH Reporter link for CSBC | |
| 1 | 2026-07-24 | reporting / submission | What is the CSBC Reporter project link? | |
| 1 | 2026-07-24 | search / other | Is treatment A significantly better than treatment B based on our sequencing results | |
| 1 | 2026-07-28 | lineage / traversal | Export all metadata for sample NHP-220630FLY-1-PUB and all of its derived (descendant) samples to Excel. | |
| 1 | 2026-07-28 | lineage / traversal | First find every sample derived from (descendant of) NHP-220630FLY-1-PUB by lineage, then retrieve the full metadata for NHP-220630FLY-1-PUB together with all of those descendant samples and | |
| 1 | 2026-07-28 | search / other | Export all samples in the database to a spreadsheet. | |
| 1 | 2026-07-29 | harmonization | Find all mice treated with NDMA and make a histogram on their genotype | |
| 1 | 2026-07-29 | harmonization | I notice some of these genotype terms look similar, could you attempt to normalize them? | |
| 1 | 2026-07-29 | reingest / upload sheet | Looks great, can you make an upload sheet for reingestion? | |
| 1 | 2026-07-29 | search / other | Cluster these samples by their metadata and tell me what the groups have in common. | |
| 1 | 2026-07-29 | search / other | Which samples belong to the CD8 depletion study? I don't think it's a project. | |
| 1 | 2026-07-29 | search / other | What sample types were in those results? | |
| 1 | 2026-07-29 | continuation | continue | |
| 1 | 2026-07-22 | scientist / attribute search | List all Non Human Primate samples with their sex and species | |
| 1 | 2026-07-22 | scientist / attribute search | For those non human primate sequencing data results, give the unique counts of sex and species. | |
| 1 | 2026-07-22 | lineage / traversal | For sequencing data (D.SEQ) samples derived from Non Human Primate (NHP) samples, traverse DERIVED_FROM from each D.SEQ child up to its NHP parent and return the counts of distinct values of | |
| 1 | 2026-07-22 | scientist / attribute search | Find all Non Human Primate (NHP) samples and return their sex and species attributes. | |
| 1 | 2026-07-22 | search / other | What species are those SED NHP's referenced in the last search | |
| 1 | 2026-07-22 | search / other | write a csv summarizing those samples, uid, sample type, project, etc | |
| 1 | 2026-07-22 | scientist / attribute search | Find me monkeys with the 4 week study attribute | |
| 1 | 2026-07-22 | search / other | Try that search again but the study as "4 week" | |
| 1 | 2026-07-22 | search / other | Find me sequencing data associated with those two NHPs above | |
| 1 | 2026-07-22 | search / other | Find me all sequencing data associated with 	NHP-220524FLY-1-PUB and 	NHP-220524FLY-2-PUB | |
| 1 | 2026-07-22 | search / other | Show me all Sequencing Data samples associated with samples NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB | |
| 1 | 2026-07-22 | lineage / traversal | Trace the full sample lineage in both directions for NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB, and return every connected Sequencing Data (D.SEQ) sample UID. | |
| 1 | 2026-07-22 | search / other | List all Mouse samples treated with NDMA, including their age. | |

## Excluded: CC infrastructure probes

| runs | question |
|---|---|
| 6 | Use bash to echo this codeword exactly and confirm it ran: BANANA-42 |
| 6 | Use bash to echo the same codeword from my first message, but change the number 42 to 84. |
| 3 | Use bash to echo this exact codeword and confirm it ran: WALLABY-OMEGA-58 |
| 3 | Use bash to echo the exact distinctive codeword that I asked you to echo in an earlier, separate chat session. First recall that codeword from your cr |
| 3 | Use bash only. Read /data/input/step3_probe.txt and confirm the exact line is STEP3-UPLOAD-PROBE-77. Write exactly STEP3-REPORT-A-7719 followed by a n |
| 3 | Use bash only. Write exactly STEP3-REPORT-B-8826 followed by a newline to /data/scratch/report.md. Write exactly STEP3-RAW-B followed by a newline to  |
| 2 | Use bash only. Read /data/input/step3_probe.txt and confirm the exact line is: STEP3-UPLOAD-PROBE-77. Write STEP3-DELIVER-99 to /data/scratch/report.m |
| 2 | Use bash to echo Output: STEP3-TURN2-OK |
| 1 | Remember this codeword for later: BANANA-42. Just reply OK. |
| 1 | What was the codeword I told you to remember? Reply with ONLY the codeword. |
| 1 | Use bash to echo this exact codeword and confirm it ran: QUOKKA-SIGMA-91 |
| 1 | In an earlier, separate chat session I had you echo a distinctive codeword. Using your cross-session memory of that previous session, tell me exactly  |
