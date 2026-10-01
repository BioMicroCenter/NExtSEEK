# The data model

NExtSEEK records an experiment as samples joined by assays. This page walks through that idea one picture at a time.

## A sample is one thing with typed metadata

A sample is a record of one thing: a mouse, a piece of tissue, a DNA library, a sequencing file, an analysis result. It has a unique ID (its UID) and a set of metadata fields. Which fields it has depends on its sample type.

Samples are not only physical material. A sequencing file or a clustering result is also a sample, so data is recorded in the same way as the material it came from.

## Every sample type belongs to a clade

Every sample has one sample type, such as `MUS` for a mouse, `TIS` for tissue or `D.SEQ` for a sequencing file. The type decides the fields. Sample types are grouped into four clades that follow the life of the data.

<figure>
  <img src="../static/docs/img/data-model/clades.png" alt="Four coloured bands, from top to bottom: Source (green), Processed (orange), Raw Data (light blue), Analyzed Data (dark blue), each with a one-line definition and examples. Below them, an arrow chain Source to Processed to Raw to Analyzed, with a note that Source and Processed describe metadata samples while Raw and Analyzed point to actual data files." loading="lazy">
  <figcaption>The four clades. Source and Processed samples are metadata only. Raw and Analyzed samples point to data files.</figcaption>
</figure>

| Clade | What it holds | Examples | Code pattern |
|---|---|---|---|
| Source | The organisms and materials a study starts from | Mouse, non-human primate, patient, bacterial culture, antibody | Plain code, such as `MUS` |
| Processed | What the lab derives from a source | Tissue, DNA, RNA, cell extract | Plain code, such as `TIS` |
| Raw | Measurements as the instrument produced them | Sequencing reads, flow cytometry, imaging | Starts with `D.`, such as `D.SEQ` |
| Analyzed | Results computed from raw data | Expression matrices, clustering, assay analyses | Starts with `A.`, such as `A.GEX` |

The full list of types, with their fields, is in [Sample types](sample-types.md).

## Samples connect through assays

An assay is an experiment or procedure done on a sample that produces another sample. It is the edge between the two. The protocol is the document that describes how it was done.

<figure>
  <img src="../static/docs/img/data-model/sample-assay-sample.png" alt="A sample box on the left, a large arrow labelled experiment in the middle with an assay and its protocol document below it, and a second sample box on the right. Each sample box has a sample type spreadsheet below it." loading="lazy">
  <figcaption>A sample, an assay (with its protocol) and a new sample. The assay is the arrow.</figcaption>
</figure>

For example, a tissue sample goes through DNA extraction and becomes a DNA sample. The DNA sample goes through sequencing and becomes a sequencing file. Each step is one assay, and each assay names its parent sample and the sample it made. See [Assays](assays.md).

## A full experiment

Chain these steps together and you get the whole experiment, with the right metadata recorded at every step.

<figure>
  <img src="../static/docs/img/data-model/sample-type-flow.png" alt="Panel A: a flow of sample types. A non-human primate sample goes through a visit to a patient visit sample. A bacteria sample joins it through an infection. The visit goes through extraction to two tissue samples. One tissue sample goes through library prep to a DNA sample and through sequencing to a sequencing file. The other tissue sample, together with an antibody sample, goes through flow cytometry to a flow cytometry file. Panel B: the metadata tables of example samples at each step, each with a UID, a name, type-specific fields and a Parent field naming the sample it came from." loading="lazy">
  <figcaption>A: the sample types and assays of one experiment, from animal to sequencing and flow cytometry. B: the metadata recorded for a sample at each step. Figure: MIT BioMicro Center.</figcaption>
</figure>

Every table in panel B has a Parent field. That field is how NExtSEEK knows which sample an assay started from.

## Every sample has a lineage

Because each sample names its parent, you can walk from any sample up to its source, and down to everything made from it. This is the tree you see on a sample's page.

<figure>
  <img src="../static/docs/img/data-model/one-animal-tree.png" alt="A tree of samples that starts at one non-human primate sample on the left. Many patient visit samples branch from it, each leading to tissue samples. Some tissue samples continue to a DNA sample, a sequencing file and an analyzed result. Dots are coloured by clade: green for source, orange for processed, light blue for raw, dark blue for analyzed." loading="lazy">
  <figcaption>The sample tree of one animal. Each dot is a sample, each line an assay, and the colours are the clades.</figcaption>
</figure>

A tree like this can grow large for a long study. You start at any sample and click along the tree to move to its parents or children.

## A project's sample types form a graph

The same idea works one level up. Instead of single samples, look at sample types. A project uses a set of types, and assays connect them. Drawn together, they show how work flows through that project.

<figure>
  <img src="../static/docs/img/data-model/project-sample-type-graph.png" alt="A graph for one project titled sample flow. Each node is a sample type, shaped and coloured by clade: green ovals for source, orange rounded boxes for processed, light blue diamonds for raw, dark blue hexagons for analyzed. Lines between nodes carry assay names. A legend and a note that you can click a node or edge for detail run along the top." loading="lazy">
  <figcaption>The sample types of one project, joined by the assays used between them.</figcaption>
</figure>

Project pages show this graph as the **Sample flow**. Click a node or an edge to see its details, or use Fullscreen for a larger view. See [Projects and templates](projects-and-templates.md).

## Where to go next

- [Sample types](sample-types.md): every type, its clade and its fields.
- [Assays](assays.md): the experiments that link samples.
- [Projects and templates](projects-and-templates.md): the project page and the upload templates.
- [Graph search](graph-search.md): search across the connections between samples.

