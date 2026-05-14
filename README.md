# NExtSEEK
 Extending SEEK for active management of scientific metadata
 
## License
Copyright (c) 2021, BioMicro Center & Bioinformatics Core, Massachusetts Institute of Technology: [MIT license](LICENSE)

## About NExtSEEK
NExtSEEK is a modified wrapper around the [SEEK](https://github.com/seek4science/seek) platform that allows active data management by establishing more discrete sample types which are mutable to permit the expansion of the types of metadata, allowing researchers to track additional information. The use of discrete nodes also converts assays from nodes to edges, creating a network model of the study, and more accurately representing the experimental process. With these changes to SEEK, users are able to collect and organize the information that researchers need to improve reusability and reproducibility as well as to make data and metadata available to the scientific community through public repositories.

## Release notes

### Unreleased — API v2 contract
This branch introduces an opt-in v2 of the NExtSEEK REST API alongside the
existing v1. Clients request v2 with `Accept: application/vnd.nextseek.v2+json`;
clients with no Accept header (or `application/json`) continue to receive v1
unchanged. v2 standardizes list envelopes (`{results, count, next, previous}`),
JSON:API-shaped error responses (`{errors: [{status, title, detail, source,
meta}]}`), and per-version OpenAPI 3.1 schemas. A schema-drift test
(`nextseek_api/tests/test_schema_drift.py`) enforces that
`nextseek_api/openapi.v2.snapshot.yaml` matches what `manage.py spectacular`
generates from the current decorators. See
[`nextseek_api/CHANGELOG.md`](nextseek_api/CHANGELOG.md) for full detail.

### Version 1.3.0
Release date: *January 23, 2025*

This release adds some quality of life improvements to dropdown inputs, adds a templates page, and adds a timeline feature for NHP sample types. The timeline feature is based on code made by [Taisha Joseph](https://github.com/tavjo)

## Installation

To install NExtSEEK, [please follow the installation instructions](https://igb.mit.edu/data-management/seek-and-nextseek) available at the Integrated Genomics and Bioinformatics (IGB) Core documentation site.

## References

NExtSEEK: Extending SEEK for active management of scientific metadata, Dikshant Pradhan, Huiming Ding, Jingzhi Zhu, Bevin P. Engelward, and Stuart S.
Levin, MIT BioMicro Center, Department of Biology, Massachusetts Institute of Technology, Cambridge, MA, USA

## Checklist for Preparing to Use NExtSEEK

#### Identify key samples and data to deposit 
The NIH and journals will generally require:
* The rawest form of the generated data – i.e. the immediate output from the instruments used or direct observations and measurements from experiments
* Processed data which is central to the work – e.g. gene expression count matrices and spectra features 
* Unique materials integral to the work – e.g. unique chemical compounds, plasmids, or cell lines
* Code necessary for processing raw data into the interpreted dataset

#### Identify FAIR-compliant field-specific repositories for data and samples
Repositories should conform to the FAIR data standards and, where possible, be well utilized in their respective fields. Examples include GEO, PRIDE, MGI, etc. Lists of available repositories can be found at Nature (https://www.nature.com/sdata/policies/repositories) and FairSharing (fairsharing.org). Where field-specific repositories do not exist, researchers can deposit to general repositories such as FigShare (figshare.com), Dryad (datadryad.org), and Zenodo (zenodo.org).

#### Identify relevant ontologies for their field
When collecting metadata, users should use shared language for interoperability. Users should pull language from the most relevant ontology to their field. Researchers can search for ontologies through the EMBL-EBI (https://www.ebi.ac.uk/ols/index) and BioPortal (https://bioportal.bioontology.org/ontologies). Recommended ontologies to pull from are the NCI Thesaurus, Experimental Factor Ontology, and the BRENDA Tissue Ontology.

#### Identify key metadata required by the repository of choice 
These metadata will vary by endpoint, but the collected metadata should fulfill the following criteria to be FAIR compliant:
* Use a formal and shared language for knowledge representation, drawn from established ontologies where possible
* Include qualified references to other data and metadata where relevant
* Include detailed provenance, such as references to parent samples
* Include protocols and code which are open, free, and universally implementable
* Metadata should be richly described with accurate and relevant attributes

#### Identify additional critical metadata 
To maximize impact and reusability, researchers should try to collect the following information: 
* Experimental groups and cohorts
* Variables and parameters that change between experiments
* Known potential covariates
* Scientists responsible for samples and experiments
* Tools and instruments (including model and software versions)
* Reagents (including manufacturers and lot numbers)
* Treatments
* Target analytes, antibodies, stains, reporters, etc.

## Contact Us
For question in setting up the system or reporting bugs, please visit [BioMicro Center, MIT](https://biology.mit.edu/tile/biomicro-center/).
