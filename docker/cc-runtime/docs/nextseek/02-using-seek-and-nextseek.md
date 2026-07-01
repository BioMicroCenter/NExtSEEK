# Using SEEK and NExtSEEK

## Concepts:

### SEEK vs NExtSEEK

NExtSEEK is a modified wrapped, built on top of the SEEK infrastructure. The fundamental differences that differentiate the SEEK and NExtSEEK platforms are outlined in the [NExtSEEK publication.](https://jbt.pubpub.org/pub/nextseek-interopaerable-data-management/release/1) Although there are differences, SEEK is required for NExtSEEK's functionality, as we leverage and use many features from the core SEEK. All data/metadata in NExtSEEK are compatible with SEEK. This compatibility is shown by using [FAIRDOMHub](https://fairdomhub.org/), an instance of the core SEEK infrastructure, as the metadata repository we use to publish our research data. An example of published research data exists [here.](https://fairdomhub.org/studies/1239)

SEEK and NExtSEEK are utilized together, each serving different purposes. Below is a very brief explanation of how the two platforms interact.&#x20;

Core SEEK: [fairdata.mit.edu](https://fairdata.mit.edu/)\
\- Functionality: Register for accounts (same account used for SEEK and NExtSEEK), create Projects/SampleTypes/Assays, administer account-project associations

NExtSEEK: [nextseek.mit.edu](https://nextseek.mit.edu/)\
\- Functionality: Upload/Search/Download Samples, Protocols, and Data Files

We use SEEK as the administrative site (creating assets and administering roles), while NExtSEEK is used for all things data (uploading, downloading, searching).

### ISA Structure

SEEK/NExtSEEK uses the ISA metadata tracking framework as described [here](https://www.isacommons.org/). ISA = Investigation, Study, Assay. In our case: Investigation = Grant/Research Project, Study = Publication, and Assay = Experiment. This is a nested structure -> There are multiple Assays in a Study, and multiple Studies in an Investigation.

This is how the data is modeled in the public domain (on FAIRDOMHub), but in the scope of NExtSEEK, we treat the investigation and study as a singular node. During the research process, it's often unknown which data will be part of a particular publication, therefore, all data of ongoing research efforts (on NExtSEEK), lives underneath a single study. When data is published from NExtSEEK to FAIRDOMHub, it is then associated with a publication, and can then be in ISA format.&#x20;

### Types of Assets

SEEK/NExtSEEK has a few different flavors/types of assets: Samples, Assays, Protocols, and Data Files.&#x20;

#### **Samples:**&#x20;

A sample is any unit of biological, chemical, or data material that is subject to analysis or experimentation. It can range from a tangible entity, such as a patient or tissue specimen, to digital data outputs like raw or analyzed sequencing data files.&#x20;

Samples are stored as tabular metadata **(excel)**, and grouped into different Sample Types; each describing a specific type of data or metadata. Each sample type is unique and will contain a different subset of attributes. Some attributes are shared, such as UUID (unique identifier/primary key), Name (also needs to be unique), Protocol (A field that links to the protocol associated with the sample), Parent (unique identifier of Parent sample), and more.\
Sample Type Nomenclature:  Samples without a prefix = Metadata samples. D.XXX = Data File, A.XXX = Analyzed Data File. \
\
\&#xNAN;*Examples: PAT: Human Patient, TIS: Tissue, DNA: DNA Library, D.SEQ: Sequencing File, A.GEX: Gene Expression Analysis File.*

{% file src="/files/wDK8mvSdNH2sHYlOAf8Y" %}
A list of all Sample Types and their description as of 8/24/24
{% endfile %}

#### **Assays:**&#x20;

An Assay is a type of experiment/procedure done on a Sample, to generate another sample. These can be broad terms, or more specific. Assays always have two samples associated with them: the Parent sample that feeds into the assay, and the Child sample that is generated from the assay. \
\
\&#xNAN;*Examples: PAT -> Tissue Collection -> TIS -> DNA Extraction -> DNA -> Short Read Sequencing -> D.SEQ -> Gene Expression Analysis -> A.GEX*

In the above example, the PAT sample feeds into the Tissue Collection Assay and generates a TIS sample.&#x20;

Assays are Study specific[ (see ISA format)](#isa-structure). To view the full list of assays that are visible to you, head [here.](https://fairdata.mit.edu/assays) To view the list of assays associated with a study, head to the [study page](https://fairdata.mit.edu/studies) for your specific project.

#### **Protocols:**&#x20;

A description of the assay/experiment performed on the sample. Can be in any format (PDF, DOCX, XLSX, TXT, IMG, etc). Ideally, this is primary materials from a lab (primary protocols used in-house), but materials and methods sections usually suffice.\
\&#xNAN;*Examples: Protocols describing* [*Tissue Extraction*](https://fairdomhub.org/sops/664)*, DNA Library Creation,* [*Sequencing*](https://fairdomhub.org/sops/570)*, and* [*Gene Expression Analysis*](https://fairdomhub.org/sops/571)*. Again, these can be Word documents, PDFs, text files, etc.*&#x20;

#### **Data Files:**&#x20;

An actual data file. Not frequently used. We are not looking to house/manage terabytes of research data, nor be responsible for serving/housing that data to the public (in perpetuity). Instead, we push for data to live in their respective repositories, and until then, in their original home (generating lab). We can store data files on SEEK/NExtSEEK, and those data files can be downloaded by users who have access, but the majority of our use cases point to systems that are much better at managing data transfers (repositories, cloud computing environments, Globus, etc).&#x20;

## Pages on NExtSEEK:

### Data Entry

There are three pages associated with Data Entry:&#x20;

* [Assay Sheet Uploading](https://nextseek.mit.edu/seek/samples/upload/): Where a user uploads samples
* [Data File/Protocol Uploading](https://nextseek.mit.edu/seek/data/upload/): Where a user uploads data files/protocols
* [Templates](https://nextseek.mit.edu/seek/templates/): Housing sample sheet templates for users to use (to prep and upload files)

More information on how to use these pages exists on the [Uploading](/mit-data-management-analysis-core/uploading.md) page.

### Data Query

There are four pages associated with Data Query:

* Advanced Search: A text search of the entire database (all samples). Allows complex searching (AND/OR/NOT). partial/exact matches, and sample type specificity.
* Simple Search: Search a single Sample Type, by a single Attribute, by a single Value.\
  \&#xNAN;*Example: All D.SEQ whose Type contains 'RNA-Seq'*
* Data File Query: Search through what data files exist in a filterable table. Files are downloadable as well (single + batch).
* Protocol Query: Search through what Protocols exist in a filterable table. Files are downloadable as well (single + batch).

More information on how to search / download samples exists on the [Searching / Downloading](/mit-data-management-analysis-core/searching-downloading.md) page.

### Sample Pages

Each sample has its own page on NExtSEEK located at: [https://nextseek.mit.edu/seek/sampletree/uid=](https://nextseek.mit.edu/seek/sampletree/uid=MUS-240620SAR-3)XXX (where XXX = the UUID of that sample).

The sample page has two sections: An interactive Sample Tree and a table of Metadata.

The interactive sample tree shows all connected Parent/Child samples. By clicking on a sample, you then load the sample page of that sample.

<figure><img src="/files/De3WXIh2seYlfkz2eBST" alt=""><figcaption><p>Interactive Sample Tree</p></figcaption></figure>

The table of Metadata is straightforward - it is the metadata associated with that sample.

<figure><img src="/files/eby72wd0crg5QEmca1sA" alt=""><figcaption><p>Metadata Table</p></figcaption></figure>

Sample pages can take some time to load (as they are not all stored in the database, and are auto-generated on load)- depending on the number of nodes (child/parent) associated with the sample.

## Pages on SEEK:

### Account Registration / Project Association:

Accounts are registered on the SEEK website (and then used on both the SEEK and NExtSEEK websites). You can register for an account here: <https://fairdata.mit.edu/signup>.

Once you have an account, you will need to be approved and added to a project (by an administrator) to access SEEK/NExtSEEK. This is what federates access to different Projects, and therefore access to the different assets of those projects. You must be a member of a project to access the assets, therefore allowing multiple projects to exist in the same database.

To administer project associations: <https://docs.seek4science.org/help/user-guide/administer-project-members.html#add-and-remove-people-from-a-project>.

You can also request to join a project: <https://docs.seek4science.org/help/user-guide/join-a-project.html>.

### Creating Assets (Sample Types, Assays, Projects)

To create a new asset type, the SEEK website is used. Whether that is creating a new Sample Type, a new Assay, or creating a new Project.&#x20;

Documentation surrounding creating these assets can be found directly on the SEEK Documentation, linked below:

* [Sample Type](https://docs.seek4science.org/help/user-guide/create-sample-type.html)
* Assay: There is no documentation on the SEEK website for this.
* [Project](https://docs.seek4science.org/help/user-guide/create-a-project.html)
* [Investigation/Study](https://docs.seek4science.org/help/user-guide/generating-the-isa-structure.html#creating-an-investigation)
* [Institution](https://docs.seek4science.org/help/user-guide/adding-admin-items.html#creating-institutions)

### Clades

To organize and standardize the various types of samples in our system, we have defined a four-tiered structure known as Clades. Each clade represents a distinct level in the data generation and processing lifecycle, enabling better metadata classification and downstream tracking.

**1. Source Clade**

* **Definition**: Primary metadata-only samples that represent biological sources.
* **Examples**: Patients, Antibodies, Non-Human Primates (NHP), Mice.
* **Purpose**: Serve as the origin point for all derived experimental materials.

**2. Processed Clade**

* **Definition**: Samples or materials derived from Source clade samples.
* **Examples**: Cells, Tissues, DNA, RNA.
* **Purpose**: Represent physical biomaterials used in experimental assays.

**3. Raw Clade**

* **Definition**: Unprocessed output data generated from assays run on Processed samples.
* **Examples**: Sequencing reads, raw imaging files, flow cytometry data.
* **Notation**: Denoted by a **D** (for "Data").

**4. Analyzed Clade**

* **Definition**: Final processed or interpreted data outputs derived from Raw data.
* **Examples**: Gene expression matrices, statistical summaries, differential analysis results.
* **Notation**: Denoted by an **A** (for "Analyzed").

### SEEK Documentation Link

A link to the full SEEK Documentation exists here: <https://docs.seek4science.org/> (head to user guides).

