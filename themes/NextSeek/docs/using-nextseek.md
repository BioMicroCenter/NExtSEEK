# Using NExtSEEK

This page covers getting an account, how NExtSEEK relates to SEEK and FAIRDOMHub, and where things are in the sidebar.

## Accounts and projects

You register for an account on the SEEK site, and the same account works on NExtSEEK. After you register, an administrator has to approve you and add you to a project. Project membership decides what you can see: you only see the samples, files and protocols of your own projects, which is how several projects share one database.

Use the Contact link in the footer if your registration is waiting or you need to join a project. The SEEK user guide explains how to [join a project](https://docs.seek4science.org/help/user-guide/join-a-project.html) and how an administrator [adds people to a project](https://docs.seek4science.org/help/user-guide/administer-project-members.html).

!!! note
    Most NExtSEEK pages need you to sign in. These docs do not.

## NExtSEEK, SEEK and FAIRDOMHub

| | SEEK | NExtSEEK | FAIRDOMHub |
|---|---|---|---|
| What it is | The platform underneath | A layer over SEEK for ongoing work | A public SEEK site for published work |
| Use it to | Create projects, sample types and assays; manage accounts and roles | Upload, search and download samples, protocols and data files | Read and cite published studies |
| Data | Whatever you register | Your projects' data before publication | Published studies only |

SEEK is required for NExtSEEK to work, and everything in NExtSEEK stays compatible with SEEK. That is why a study can be moved to FAIRDOMHub when it is published. The [NExtSEEK paper](overview.md#cite-nextseek) describes the differences in detail.

## The investigation and study structure

SEEK organizes work as investigation, study and assay. In NExtSEEK, the ongoing work of a project lives under one study, because during the work nobody knows yet which data will go into which paper. When data is published to FAIRDOMHub, it is attached to a publication and takes the full investigation, study and assay shape.

## Finding your way around

The sidebar on the left holds the main links. Names below are the labels you see.

| Section | Link | What it opens |
|---|---|---|
| Data | Home | The home page |
| Data | Sample Search | Search all samples. See [Searching and downloading](searching-downloading.md) |
| Data | Data Entry | Assay Sheet Upload (samples) and Data & Protocol Upload (files and protocols). See [Uploading](uploading.md) |
| Data | Data Query | Data File Query and Protocol Query, both filterable tables |
| Data | Projects | The list of project pages. See [Projects and templates](projects-and-templates.md) |
| Data | Useful Info | Documentation, Templates, Sample Types and Assays |
| Quick Access | Nessie button | The assistant. See [Nessie, the assistant](nessie.md) |
| Quick Access | Search by UID | Type a sample UID to open that sample's page |
| Quick Access | New sample | Goes to Assay Sheet Upload |
| Resources | Getting Started | These docs |
| Resources | Published Studies | The public studies on FAIRDOMHub |
| Resources | Contact Support | Opens an email to the data team |

Administrators also see an Admin section. If you are not signed in, expect to be asked to sign in when you open most of these pages.

## The sample page

Each sample has its own page, which you reach by typing its UID in the "Search by UID" box or by clicking a sample in a search result. The page has two parts:

- **Sample tree.** An interactive graph of the sample's parents and children. Click a sample in it to open that sample's page.
- **Sample info.** A table of the sample's metadata, with links to its parent samples and its protocol.

The tree is built when you open the page, so a sample with many connected samples can take a while to load. To read what the tree means, see [The data model](data-model.md).

## Concepts you will meet

- A **sample type** is a template that says which fields a kind of sample carries. See [Sample types](sample-types.md).
- A **clade** is one of four groups of sample types, from source to analyzed data. See [The data model](data-model.md#every-sample-type-belongs-to-a-clade).
- An **assay** links a parent sample to a child sample. See [Assays](assays.md).
- A **protocol** describes how an assay was done, and a **data file** is the file itself. NExtSEEK is not built to hold large data. Keep large files in their repository or at your lab, and record where they are.

For the full SEEK documentation, see the [SEEK user guide](https://docs.seek4science.org/help/user-guide/).
