# NExtSEEK Chat Assistant — Capabilities Reference

## Overview

The NExtSEEK Chat Assistant lets you query the NExtSEEK biological sample repository using plain English. Under the hood it translates your questions into NExtSEEK REST API calls and Neo4j graph database queries, then summarizes the results for you. The repository holds the metadata of biological samples and of the data made from them, the links between them, and organizational structures (projects, investigations, studies) from across multiple research programs. You do not need to know any API syntax, Cypher, or SQL — just describe what you want.

---

## What NExtSEEK Is

NExtSEEK is a metadata catalog, built on SEEK, for the samples a research program collects and everything made from them. It records each sample (an animal, a tissue, an extract, and also each data file and analysis result), its sample type and that type's attributes, the assays and protocols that link a sample to the samples derived from it, and the projects, investigations and studies it belongs to. Together these form a lineage graph from the source organism to the final analysis.

### How samples connect

Every sample type belongs to one of four clades:

- **Source**: an organism or starting material that enters the lab, such as a mouse (MUS), a non-human primate (NHP), a patient (PAT), bacteria (BAC), a virus (VIR), a cell line (CEL), an antibody (AB), a chemical (CHM) or a water sample (WTR).
- **Processed**: material prepared from a source, such as a tissue (TIS), extracted RNA or DNA, a lysate (LYS), a slide (SLD) or an organ-on-chip (OOC).
- **Raw**: a data file an instrument produced from a sample, with a `D.` type code, such as sequencing reads (D.SEQ) or a flow cytometry file (D.FCS).
- **Analyzed**: a result computed from raw data, with an `A.` type code (and `M.` for a model).

An **assay** is the experiment that turns one sample into another. Each assay names the parent sample types it takes (required and optional), the child sample types it produces, and the clades on each side. So a sample's history reads sample, then the assay run on it, then the new sample type that assay produced: a mouse (Source) goes through Tissue Collection to become a tissue (Processed), RNA Extraction turns the tissue into RNA (Processed), a sequencing assay turns the RNA into sequencing reads (Raw, D.SEQ), and an analysis turns the reads into a result (Analyzed, A.). The usual order is Source, then Processed, then Raw, then Analyzed, although an assay can also go straight from a Source to Raw data (Intravital Microscopy images a live mouse, MUS to D.IMG) or from one Source to another. In the graph each step is a `DERIVED_FROM` relationship from the child sample to its parent, carrying the assay and the protocol used.

### Where the data files are

**NExtSEEK does not host the data files.** Raw and processed data (FASTQ, BAM, FCS, images, spreadsheets of results) live on the storage of the lab or the core facility that produced them, or in a public repository. NExtSEEK holds the metadata that points to them. Every data and analysis record names its file in `File_PrimaryData`, says where that file is stored in `Link_PrimaryData`, and usually carries its checksum in `Checksum_PrimaryData`; some also have `File_SecondaryData` and `Link_SecondaryData`, or a `Path_PrimaryData`. A record deposited publicly names the repository in `Repository` and its accession in `RepositoryID`.

What can be downloaded from here is metadata: search results as spreadsheets, the reports and submission workbooks the assistant builds, and a whole chat's files.

A whole chat downloads as one zip from the **All files** button in the chat's right-hand panel: the transcript, every file its turns wrote, and a manifest. That zip is also how to send a conversation to the development team for feedback: download it and attach it to the message. To get the data files themselves, find their locations first, then fetch them from there and check each one against its checksum.

When someone asks to download a data file, or asks where the raw data is, say that NExtSEEK does not host files: it holds the metadata that points to where each file lives. Then offer to look up the file names and locations (`File_PrimaryData`, `Link_PrimaryData`, `Checksum_PrimaryData`) for the samples they care about. If the question already names samples, those locations are the answer.

---

## What You Can Ask

### 1. Sample Search

Search for samples by type, assay, treatment, keyword, or any combination. The system filters against the full NExtSEEK sample database and returns matching records with metadata.

**Example queries:**
- "Find me mice treated with NDMA."
- "Show me all non-human primate samples with flow cytometry data."
- "Find tissue samples associated with CD8 depletion."
- "List the sequencing data records uploaded in the last six months, with where their files are stored."
- "Find all CD8 antibodies in the database."
- "Show me DNA samples from the fibrin study."
- "Find all organ-on-chip samples with cell viability assay data."

You can also search for parents that have children of multiple types — for example, animals with both sequencing and imaging downstream:

- "Which monkeys have both flow cytometry and sequencing data?"
- "Find all animals that have tissue samples and cell line derivatives."

### 2. Graph Queries

The knowledge graph captures organizational structure and biological relationships that the REST API cannot express. It stores three main node types:

- **Sample** — an individual registered sample record (identified by its type code, e.g. `NHP`, `TIS`, `D.SEQ`).
- **Study** — a named experimental study grouping related samples (e.g., "GBM Cohort 2023"). Samples belong to studies via the `IN_STUDY` relationship.
- **Publication** — the paper a study was published as. Held as the `DOI` and
  `PMID` properties on the Study node (uppercase; empty string when unset), so a
  sample inherits the paper of every study it belongs to. You can ask which paper
  a sample appears in, or which samples a paper used, by title, DOI or PMID. Most
  studies are unpublished; that is expected, not a gap.
- **Investigation** — a project-level grouping of studies (e.g., "Impactb Investigation", "MIT_SRP", "GBM_BTC"). Studies belong to investigations via the `IN_INVESTIGATION` relationship.

Derivation (lineage) between samples is encoded on the `DERIVED_FROM` relationship, which also carries assay and protocol metadata.

Use graph queries when your question involves named studies or investigations, cross-study aggregation, derivation chains, or filtering by which assay or protocol produced a sample.

**Example queries:**
- "What samples are in the GBM study?"
- "What studies exist in the Griffith project?"
- "How many TIS samples are across all studies?"
- "Show me all NHP samples in the MIT_SRP investigation."
- "What projects have mouse samples?"
- "Find all samples that underwent single cell sequencing."
- "How many studies are in the CSBC project?"
- "Show me the derivation lineage for sample NHP-220630FLY-5-PUB."
- "Find tissues associated with flow cytometry protocols."

### 3. Sample Lineage

For a specific known sample UID, you can retrieve the full derivation tree — all ancestors and descendants — and get comprehensive metadata for a sample and everything related to it.

**Example queries:**
- "Show me all samples derived from CEL-250319WHI-1-PUB."
- "Retrieve all samples associated with NHP-220630FLY-5-PUB."
- "What tissue and cell samples came from PAT-241113DFC-3?"

### 4. Upload Statistics and Reporting

A SQL-backed reporting system answers questions about sample upload history and generates repository submission packages.

**Upload statistics examples:**
- "How many samples were uploaded for Impact from 2023 to 2025?"
- "How many samples did the Griffith lab upload last year?"
- "Show me upload counts by sample type for the Break Through Cancer project in Q1 2024."

**Repository submission generation:**
- "Build me a GEO submission for D.SEQ-221031SHA-67-PUB and D.SEQ-221031SHA-65-PUB."
- "Generate a PRIDE report for these proteomics samples: [UIDs]."
- "Create an SRA submission package for our sequencing data."

### 5. Follow-up Questions

After any search, you can ask follow-up questions about the results already returned — without running a new query. The system reasons over what it has already retrieved.

**Example queries (after a preceding search):**
- "Which of those monkeys are depleted of CD8?"
- "How many of those samples are from 2023?"
- "Which of the results are from the Griffith lab?"

### 6. Search Refinements

You can modify and re-run your most recent search by describing what you want to change. The system carries forward the prior search context and adjusts it.

**Example queries:**
- "Try that search again but with DFCI4 instead."
- "Same thing but only from 2024."
- "Run that again but add a sequencing data filter."
- "Narrow those results to liver tissue only."

### 7. Entity and Catalog Information

You can ask for detailed information about specific catalog items — what a sample type represents, what an assay involves, or what a particular investigation covers.

**Example queries:**
- "What is a TIS sample type?"
- "Tell me about the SRS assay."
- "Explain the GBM_BTC investigation."
- "What does the D.FLOW sample type contain?"
- "What assays are available in the system?"
- "What sample types can I search for?"

### 8. System Capabilities Questions

You can ask meta questions about what the system can do, what endpoints are available, or how to phrase a query.

**Example queries:**
- "What can you help me find?"
- "What graph relationships are available?"

---

## Sample Types

The system recognizes the following sample types, among others. Each has a short code used internally for filtering and graph queries.

| Code | Name |
|------|------|
| `MUS` | Mouse |
| `NHP` | Non-Human Primate |
| `PAT` | Patient |
| `PAV` | Patient Visit |
| `TIS` | Tissue Sample |
| `CEL` | Cell |
| `CEX` | Cell Extract Sample |
| `DNA` | DNA Sample |
| `RNA` | RNA Sample |
| `SLD` | Slide / Tissue Section |
| `AB` | Antibody |
| `ABP` | Antibody Panel Sample |
| `CHM` | Chemical Sample |
| `OOC` | Organ on Chip |
| `BAC` | Bacteria Sample |
| `VIR` | Virus Sample |
| `D.SEQ` | Sequencing Data (FASTQ) |
| `D.FLOW` | Flow Cytometry Data (FCS) |
| `D.IMG` | Imaging Data |
| `D.MSP` | Mass Spectrometry Data |
| `D.NMR` | Nuclear Magnetic Resonance Data |
| `A.FLOW` | Flow Cytometry Analysis |
| `A.ALN` | Sequence Alignment Analysis |
| `A.GEX` | Gene Expression Analysis |
| `A.SCXP` | Single Cell Expression Matrix Analysis |

This is a representative selection. You can ask **"What sample types are available?"** to retrieve the full list from the system.

---

## Assays

Assays represent experimental processes — the procedures that transform or generate samples. The system recognizes the following assays, among many others.

| Name | Description (brief) |
|------|----------------------|
| Flow Cytometry | Measures fluorescence and light-scattering from individual cells |
| Single Cell Sequencing | Generates sequencing data from individually barcoded cells |
| Bulk DNA Sequencing | Produces nucleotide read data from pooled DNA samples |
| RNA Extraction | Isolates RNA from tissue or cells for downstream sequencing |
| Tissue Collection | Controlled acquisition of biological material from an organism |
| Cell Sorting | Separates cell populations by fluorescence or marker expression |
| Cell Extraction | Isolates cells from tissues for culture or analysis |
| Antibody Treatment | Exposes biological samples to antibodies to study their effects |
| Bacterial Challenge | Exposes samples or organisms to a defined bacterial strain |
| Mass Spectrometry | Identifies and quantifies molecules from ion mass-to-charge spectra |
| Antibody Titer | Measures antibody concentration by serial dilution |
| Antibody-Dependent Cellular Phagocytosis | Quantifies antibody-mediated immune cell uptake |
| Antibody-Dependent NK Cell Activation | Measures NK cell responses triggered by antibody-coated targets |
| Cell Viability | Measures proportion of living cells after treatment |
| Gene Expression Analysis | Converts raw sequencing reads into quantified transcript abundance |
| Spatial Transcriptomics | Maps gene expression across tissue architecture |

You can ask **"What assays are available?"** to retrieve the full assay catalog from the system.

---

## Known Projects and Investigations

<!-- BEGIN CONTEXT-GEN:investigations -->

The graph database organizes samples into studies grouped under named investigations. The investigations that hold samples are:

- **BioMicroCenter**: Sequencing and analysis data generated at the MIT BioMicro Center genomics core, such as alignments, variant calls and mutational spectra [also: BioMicro Center]
- **CSBC**: Systems biology of glioblastoma, the tumor, neural and immune interactions in its microenvironment, profiled by proteomics, imaging and sequencing
- **Collagen Study**: Collagen folding and proteostasis in iPSC-derived cartilage models of collagen disease variants [also: Shoulders]
- **Endometriosis**: Endometriosis and the human endometrium, studied in patient-derived endometrial organoids and co-cultures and by single-cell and proteomic profiling [also: Griffith, CGR-Endo]
- **GBM_BTC**: Recurrent glioblastoma studied through serial brain biopsies, with cerebrospinal fluid and blood as surrogate markers of drug activity [also: BTC-GBM]
- **Impactb Investigation**: Protective and non-protective immune responses to Mycobacterium tuberculosis in mice, non-human primates and humans [also: Impact, IMPACT, IMPAcTb]
- **MIT_SRP**: Health effects of N-nitrosamines and other hazardous chemicals in drinking water, including DNA damage, mutation and cancer risk [also: SRP, MIT SRP, Superfund]
- **MetNet**: Mechanical determinants of metastatic colonization, dormancy and outgrowth in engineered microvascular and organ-on-chip models
- **TCGA**: Public pan-cancer reference data from The Cancer Genome Atlas, with patients, tumor and normal tissue samples and their sequencing data [also: The Cancer Genome Atlas] (not on every instance: loaded on local and dev only)

Use these names exactly when asking graph questions scoped to one investigation. The names in brackets are what people call them; the bold name is what the graph answers to. A name marked "not on every instance" is loaded only on the instances it lists. Where a query scoped to it finds no samples, it is not loaded on this instance: say so rather than reporting zero.

<!-- END CONTEXT-GEN:investigations -->

---

## What the System Cannot Do

The system retrieves, filters, and summarizes data from NExtSEEK. It does not perform analysis or answer general science questions. Specifically, it cannot:

- **Generate visualizations or charts.** It has no plotting or rendering capability. Even if the underlying data exists, asking for a heatmap, scatter plot, or bar chart is not supported.
- **Perform statistical analysis.** Differential expression, clustering, fold-change calculations, dimensionality reduction, survival analysis, and other computational methods are outside scope. The system can tell you where the data files are stored; it does not host them and cannot run pipelines on them.
- **Explain external scientific concepts.** It cannot explain what a gene encodes, why a molecule is carcinogenic, how a signaling pathway works, or what a disease mechanism is. These are knowledge questions unrelated to the repository.
- **Answer questions about data outside NExtSEEK.** It only knows what is registered in the NExtSEEK repository. It cannot query external databases (GEO, SRA, UniProt, etc.) except to generate a submission package from NExtSEEK records.
- **Compare groups analytically.** Asking "which group had higher expression levels" or "is there a significant difference between condition A and B" requires computation the system cannot do.

---

## Tips for Better Queries

**Be specific about sample type.** The clearest queries name the kind of thing you want: "Find NHP samples with sequencing data" is more precise than "Find samples with sequencing data." If you are unsure of the sample type name, ask "What sample types are available?"

**Use investigation names for project-scoped questions.** When you want results scoped to a particular project or study, use an exact name from the list under Known Projects and Investigations and phrase the query explicitly: "What samples are in the GBM_BTC investigation?"

**Use "refine" or "try that again" to adjust your last search.** If you want to change a filter on an existing query, say so explicitly: "Try that again but only for 2024" or "Same search but add a flow cytometry filter." This preserves your prior context and avoids starting from scratch.

**Ask follow-ups before running a new search.** If your last query returned results and you want to narrow them down by a criterion, ask about those results directly: "Which of those are from male mice?" This is faster than a new search and reasons over what was already retrieved.

**Provide UIDs when you want lineage or full metadata.** Queries about a specific sample's derivation tree or comprehensive metadata work best when you supply the exact sample UID (e.g., `NHP-220630FLY-5-PUB`). You can get UIDs from any prior search result.
