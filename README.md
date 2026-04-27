# NExtSEEK
 Extending SEEK for active management of scientific metadata
 
## License
Copyright (c) 2021, BioMicro Center & Bioinformatics Core, Massachusetts Institute of Technology: [MIT license](LICENSE)

## About NExtSEEK
NExtSEEK is a modified wrapper around the [SEEK](https://github.com/seek4science/seek) platform that allows active data management by establishing more discrete sample types which are mutable to permit the expansion of the types of metadata, allowing researchers to track additional information. The use of discrete nodes also converts assays from nodes to edges, creating a network model of the study, and more accurately representing the experimental process. With these changes to SEEK, users are able to collect and organize the information that researchers need to improve reusability and reproducibility as well as to make data and metadata available to the scientific community through public repositories.

## Release notes

### Version 1.3.0
Release date: *January 23, 2025*

This release adds some quality of life improvements to dropdown inputs, adds a templates page, and adds a timeline feature for NHP sample types. The timeline feature is based on code made by [Taisha Joseph](https://github.com/tavjo)

## Installation

To install NExtSEEK, [please follow the installation instructions](https://igb.mit.edu/data-management/seek-and-nextseek) available at the Integrated Genomics and Bioinformatics (IGB) Core documentation site.

## Installation - Docker

NExtSEEK also provides Docker as an installation method.
The `docker-compose.yml` file will orchestrate SEEK, NExtSEEK, and other necessary services for you.

First, you need to [install Docker](https://docs.docker.com/engine/install) and [Docker Compose](https://docs.docker.com/compose/install/) onto your system.
Once installed, clone this repository:

```bash
# Clone this repository
git clone https://github.com/BMCBCC/NExtSEEK && cd NExtSEEK

# IMPORTANT. Clone the chat_nextseek repository.
# Not publicly available at this moment.
```

Edit `docker-compose.yml` to include the neo4j password you'd like to use.
You'll also need to download the [SmartAdmin](https://wrapmarket.com/item/smartadmin-enterprise-admin-dashboard-WB0573SK0) theme and edit the `nextseek` container volume paths to point to it.

Edit `docker/db.env` and `docker/nextseek.env` with the appropriate values.

Copy `dmac/local_settings.example.py` to `dmac/local_settings.py` and edit with appropriate values.

Before bringing up the Docker containers, you need to create the necessary volumes:

```bash
# Create SEEK-specific volumes
docker volume create --name seek-filestore
docker volume create --name seek-mysql-db
docker volume create --name seek-solr-data
docker volume create --name seek-cache

# Create NExtSEEK-specific volumes
docker volume create --name nextseek-static-files
docker volume create --name neo4j-data
```

To bring up the containers run:

```bash
docker compose up -d
```

Now, you can visit SEEK at `http://127.0.0.1:3000` and NExtSEEK at `http://127.0.0.1:8000`.

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
