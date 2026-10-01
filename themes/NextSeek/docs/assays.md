# Assays

An **assay** is the experiment that turns samples of one type into samples of another. Short Read Sequencing, for example, takes a `DNA` sample and produces a `D.SEQ` sequencing data file. See [Sample types](sample-types.md) for the codes.

## What an assay has

* **Required parent types**: the sample types you must start from.
* **Optional parent types**: other types it can also take in.
* **Produces**: the sample types that come out.
* **Protocol**: the written procedure, registered as an SOP.
* **Critical attributes**, an **associated repository** and other names it is known by, where they apply.

Assays link the clades in the [data model](data-model.md): Source to Processed, Processed to Raw, Raw to Analyzed. When you upload samples, you register them against an assay so the link from parent to child is recorded. See [Uploading](uploading.md).

## Browse every assay

The live catalog lists every assay, grouped by the clade it takes as input. Assays with no clade yet sit in an Unassigned group that starts collapsed. Use the filter box to find one.

[Open the Assays catalog](/seek/assays/) (you need to be signed in).

Click an assay to see its required and optional parent types, what it produces and its tags.
