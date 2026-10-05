# NExtSEEK glossary

The words NExtSEEK, its docs and Nessie use for its own concepts, one meaning each.

## Samples and assays

**Assay**:
The named kind of lab step, such as RNA-Seq. It is what the graph's Assay nodes and Nessie mean by the word. Several SEEK assays can map to one assay, so differently titled SEEK assays count as the same kind of step.
_Avoid_: internal assay (say "assay"; use "SEEK assay" for SEEK's record)

**SEEK assay**:
SEEK's record of an assay under a study; samples are made members of it. A SEEK assay maps to at most one assay.

**Assay registration**:
Making a sample a member of a SEEK assay. The SEEK assay is found through the assay, in the sample's own project.

**Sample type**:
SEEK's template for a kind of sample: a title, a code and the attributes each sample of that type fills.

**Catalog entry**:
Nessie's hand-written description of one sample type or one assay, held in the catalog.
_Avoid_: sample type (when the description is meant, not SEEK's template)

**Catalog**:
The set of catalog entries for sample types, assays and projects that Nessie reads. The graph also carries a live copy of it.

**Sample**:
One record of a material in SEEK, made from a sample type and belonging to a project. Each sample also has a node in the sample graph.

## The SEEK hierarchy

**Project**:
SEEK's top-level grouping. It holds investigations, and users and samples belong to projects.

**Project scope**:
The set of projects a user's questions are limited to. An unscoped administrator sees every project; an empty scope sees nothing.

**Investigation**:
A SEEK record inside a project that holds studies.

**Study**:
A SEEK record inside an investigation that holds SEEK assays. A study titled "Unpublished" is a holding bucket; samples move out of it to a new study when their paper is published.

## The graph

**Sample graph**:
The Neo4j copy of NExtSEEK's sample records, built from MySQL by graph sync. It is never the source of truth.
_Avoid_: graph (alone, when the kind matters)

Other graphs, so the bare word stays unused:

- **Code graph**: the file-level picture of which files import which, in the docs graph folder.
- **Notes graph**: the graphify graph over the operator's session notes.
- **Project graph**: the drawing of one project's samples and links on the site.

## Nessie

**Nessie**:
The whole assistant: the chat panel, the router and both engines.

**Route**:
The per-turn choice of who answers: the NS engine, the CC engine, or a fixed out-of-scope reply.

**NS engine**:
The engine that answers a database question with a fixed multi-agent pipeline, run inside the web app.

**CC engine**:
The Container-CC engine: a sandboxed Claude Code agent started in a fresh container for each turn routed to it.
_Avoid_: CC (alone; the tool itself is "Claude Code")

**Task family**:
The classifier's label for a kind of question. The router reads it to pick a route; it is not a route.
