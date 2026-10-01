# Graph search

NExtSEEK keeps a second copy of your sample records as a graph. A graph stores things and the links between them, which makes questions about lineage fast. This page says what is in it and what it lets you do. You do not run it yourself: the Sample Search page and Nessie use it for you.

## What the graph holds

For every sample:

* Its **sample type** and its attribute values.
* The **projects** it belongs to.
* Its **parents and children**, as links from each sample to the one it was made from.
* The **assay and protocol** on each of those links.
* The SEEK study and investigation it sits in.

Together the links form the lineage tree you see on a sample page and in the "How this data flowed" section of a [download](searching-downloading.md#download-samples). To see how sample types connect, read [Data model](data-model.md).

## What it lets you do

| You want to | How the graph helps |
|---|---|
| Find samples by type and attribute | Search is fast even across a very large number of samples |
| Follow lineage up and down | **Associated with** keeps samples that have an ancestor or descendant of another type |
| Search across projects | One search covers every project you belong to |
| Search free text | A word or phrase is matched against all of a sample's values at once |
| Combine terms | AND, OR, NOT and parentheses in Advanced Sample Search |
| Download with relatives | Parents and children come along in the workbook |

## Where you meet it

* **Sample Search**, both the single-attribute tab and the advanced tab. See [Searching and downloading](searching-downloading.md).
* **The project Sample flow**, which draws how a project's sample types connect. See [Projects and templates](projects-and-templates.md).
* **Nessie**, which looks things up in the graph when you ask about samples. See [Nessie](nessie.md).

## Example questions

| Question | Where |
|---|---|
| Which NHP samples have Species containing Macaca? | Sample Search, type NHP, attribute Species |
| Which sequencing samples came from tissue samples? | Sample Search, type D.SEQ, Associated with TIS, ancestors only |
| Which samples mention both "granuloma" and "lung"? | Advanced Sample Search, two terms joined with AND |
| What was made from this sample? | The sample's page: its tree shows the children |
| How many samples of each type does my project have? | Ask Nessie |

## Limits worth knowing

* **Only your projects.** You see samples in projects you are a member of. An administrator sees all. Lineage stops at the edge of your projects: a parent in a project you cannot see is not followed.
* **A short wait after changes.** Samples added through the upload pages reach the graph within seconds. Changes made in SEEK itself are picked up in a nightly pass, so they can take until the next day to show in search. If a new sample is missing, wait and search again.
* **Blank values are not stored.** An attribute that is empty on a sample is treated as absent.
* **Search is a copy.** The sample page and SEEK are the record. If search and a sample page disagree, trust the sample page and tell the data team.

For how new samples get into the graph, see [Uploading](uploading.md#what-the-job-does).

