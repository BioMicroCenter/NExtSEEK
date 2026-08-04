"""
Centralized endpoint description constants for all nextseek_api ViewSets.

Each constant follows a structured template optimized for LLM tool-selection:
    SUMMARY > USE WHEN > DO NOT USE WHEN > ACCEPTS > RETURNS > TRIGGER PHRASES > EXAMPLES

Import individual constants into ViewSet files and pass to @extend_schema(description=...).
"""

# =============================================================================
# SampleTreeViewSet (1 endpoint)
# =============================================================================

SAMPLE_TREE_GET_DESC = (
    "**SUMMARY:** Retrieve the full parent-child sample hierarchy tree for a single sample.\n\n"
    "**USE WHEN:** The user asks for the lineage, family tree, or derivation chain of ONE sample "
    "identified by a UID (e.g. NHP-220630FLY-2-PUB) or numeric SEEK ID.\n\n"
    "**DO NOT USE WHEN:** The user wants lineage trees for MULTIPLE samples at once — use `POST entity_tree/lineage` instead.\n\n"
    "**ACCEPTS:** A single sample identifier (UID string or numeric SEEK ID) as a path parameter.\n\n"
    "**RETURNS:** Nodes (`id`, `uuid`, `sample_type`, `color`, `parentIds`) and relationships (`child_id`, `parent_id`, assay info) for visualization.\n\n"
    "**TRIGGER PHRASES:** sample tree, sample lineage, sample hierarchy, derived from, parent samples, child samples\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all samples derived from NHP-220630FLY-2-PUB'\n"
    "- 'What tissue and cell samples came from this patient: PAT-241113DFC-3?'\n"
    "- 'Get the sample tree for sample 1525'\n"
)

# =============================================================================
# AdminSampleViewSet (1 endpoint)
# =============================================================================

ADMIN_SAMPLE_RETRIEVE_DESC = (
    "**SUMMARY:** Export sample metadata for admin users given a list of sample identifiers.\n\n"
    "**USE WHEN:** An admin needs to bulk-export metadata (JSON or Excel) for specific samples they already know by UID or SEEK ID, "
    "including all parent/child derived samples.\n\n"
    "**DO NOT USE WHEN:** The user wants to SEARCH or FILTER samples by type, attribute, or text — use `POST advanced_search` instead.\n\n"
    "**ACCEPTS:** A list of sample UIDs (e.g. 'NHP-220630FLY-1-PUB') and/or SEEK IDs (numeric); optional `output_format` (`json` or `excel`).\n\n"
    "**RETURNS:** JSON grouped by sample type with full metadata, or an Excel workbook. Includes derived (parent/child) samples automatically.\n\n"
    "**TRIGGER PHRASES:** export samples, download sample metadata, admin sample retrieval, bulk sample export, sample data dump\n\n"
    "**EXAMPLES:**\n"
    "- 'Export all metadata for NHP-220630FLY-1-PUB and its derived samples as JSON'\n"
    "- 'Download an Excel spreadsheet with data for samples 12345 and 67890'\n"
    "- 'Get sample data for these three monkeys in JSON format'\n"
    "- 'Retrieve full metadata for tissue samples TIS-230324BOO-39-PUB and TIS-230324BOO-40-PUB'\n"
)

# =============================================================================
# SopProxyViewSet (5 endpoints)
# =============================================================================

SOP_LIST_DESC = (
    "**SUMMARY:** List all standard operating procedures (protocols) accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse or list all available SOPs/protocols without knowing a specific ID.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of SOP records with titles, descriptions, associated projects, and file attachment metadata.\n\n"
    "**TRIGGER PHRASES:** list protocols, show SOPs, all procedures, available protocols, browse SOPs\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all available protocols'\n"
    "- 'What procedures are documented for sample collection?'\n"
    "- 'List all SOPs in the system'\n"
)

SOP_FETCH_DESC = (
    "**SUMMARY:** Fetch details for a specific protocol document by ID or name.\n\n"
    "**USE WHEN:** The user wants to view metadata for ONE specific SOP/protocol identified by SEEK ID or title.\n\n"
    "**ACCEPTS:** SOP identifier (numeric SEEK ID or title string) as path parameter; optional version query param.\n\n"
    "**RETURNS:** Full SOP metadata including title, description, version history, attached files, and linked projects.\n\n"
    "**TRIGGER PHRASES:** get SOP, fetch protocol, SOP details, protocol info, show procedure\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the tissue processing protocol'\n"
    "- 'What is the current version of the blood draw procedure?'\n"
    "- 'Get details for SOP 142'\n"
)

SOP_CREATE_DESC = (
    "**SUMMARY:** Upload a new standard operating procedure document.\n\n"
    "**USE WHEN:** The user wants to register or upload a new SOP/protocol into the system.\n\n"
    "**ACCEPTS:** SOP data including title, content blobs (PDF, Word, etc.), policy settings, and project associations.\n\n"
    "**RETURNS:** The created SOP record with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create SOP, upload protocol, add procedure, new SOP, register protocol\n\n"
    "**EXAMPLES:**\n"
    "- 'Add a new necropsy protocol for the primate study'\n"
    "- 'Upload the updated sample handling procedure'\n"
    "- 'Create a new SOP for the GEX workflow'\n"
)

SOP_UPDATE_DESC = (
    "**SUMMARY:** Revise an existing protocol document by ID or name.\n\n"
    "**USE WHEN:** The user wants to modify an existing SOP's title, description, files, or project associations.\n\n"
    "**ACCEPTS:** SOP identifier as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated SOP record with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update SOP, edit protocol, modify procedure, change SOP, revise protocol\n\n"
    "**EXAMPLES:**\n"
    "- 'Update the description for the imaging protocol'\n"
    "- 'Change the project association for the cell culture procedure'\n"
    "- 'Rename SOP 132 to GEX Protocol v2'\n"
)

SOP_DOWNLOAD_DESC = (
    "**SUMMARY:** Download SOP content blobs — supports single and batch modes.\n\n"
    "**USE WHEN:** The user wants to download the actual file(s) attached to one or more SOPs.\n\n"
    "**ACCEPTS:** Single mode (JSON object): one `SopDownloadRequest` with `uid_or_id`, optional `blob_id` and `output_format`. "
    "Batch mode (JSON array): multiple `SopDownloadRequest` objects.\n\n"
    "**RETURNS:** Single mode: streaming binary file download. "
    "Batch mode: zip archive containing all downloaded files plus a `manifest.json`.\n\n"
    "**TRIGGER PHRASES:** download SOP, get protocol file, fetch procedure document, download protocol PDF, batch download SOPs\n\n"
    "**EXAMPLES:**\n"
    "- 'Download the GEX protocol document'\n"
    "- 'Get the DNA adducts procedure file as a PDF'\n"
    "- 'Fetch multiple protocols at once as a zip archive'\n"
    "- 'Download SOP 142 as CSV'\n"
)

# =============================================================================
# DataFileProxyViewSet (4 endpoints)
# =============================================================================

DATAFILE_LIST_DESC = (
    "**SUMMARY:** List all data files accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse all available data files (spreadsheets, images, sequencing outputs, etc.).\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of data file records with titles, descriptions, `content_type`, and linked projects/assays.\n\n"
    "**TRIGGER PHRASES:** list data files, show files, all uploads, available data, browse files\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all uploaded data files'\n"
    "- 'What sequencing results are available for download?'\n"
    "- 'List all data files in the system'\n"
)

DATAFILE_FETCH_DESC = (
    "**SUMMARY:** Fetch metadata for a specific data file by ID or name.\n\n"
    "**USE WHEN:** The user wants to view details for ONE specific data file identified by SEEK ID or UID.\n\n"
    "**ACCEPTS:** Data file identifier (numeric SEEK ID or UID string) as path parameter; required version query param (default 1).\n\n"
    "**RETURNS:** Full data file metadata including title, description, file type, download URL, version history, and linked samples/assays.\n\n"
    "**TRIGGER PHRASES:** get data file, fetch file, file details, file info, show data file\n\n"
    "**EXAMPLES:**\n"
    "- 'Get the CT scan images for this monkey'\n"
    "- 'Show me the flow cytometry results file for sample NHP-220630FLY-1-PUB'\n"
    "- 'Fetch data file 560'\n"
)

DATAFILE_CREATE_DESC = (
    "**SUMMARY:** Register a new data file by providing a URL or uploading content.\n\n"
    "**USE WHEN:** The user wants to add a new data file to the system and associate it with projects/assays/samples.\n\n"
    "**ACCEPTS:** Data file payload with title, content blobs (URL or upload), and project/assay relationships.\n\n"
    "**RETURNS:** The created data file record with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create data file, upload file, add data, register file, new data file\n\n"
    "**EXAMPLES:**\n"
    "- 'Upload the microscopy images from today\\'s experiment'\n"
    "- 'Register a new sequencing output file for the water study'\n"
    "- 'Create a data file entry for the CSV export'\n"
)

DATAFILE_UPDATE_DESC = (
    "**SUMMARY:** Update an existing data file's metadata by ID or name.\n\n"
    "**USE WHEN:** The user wants to modify a data file's title, description, or project/assay associations.\n\n"
    "**ACCEPTS:** Data file identifier as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated data file record with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update data file, edit file, modify file metadata, change file, patch data file\n\n"
    "**EXAMPLES:**\n"
    "- 'Add a description to the microscopy images'\n"
    "- 'Change the project association for this sequencing file'\n"
    "- 'Update data file 560 title'\n"
)

DATAFILE_DOWNLOAD_DESC = (
    "**SUMMARY:** Download DataFile content blobs — supports single and batch modes.\n\n"
    "**USE WHEN:** The user wants to download actual file(s) attached to one or more data files.\n\n"
    "**ACCEPTS:** Single mode (JSON object): one request with `uid_or_id`, optional `blob_id` and `output_format`. "
    "Batch mode (JSON array): multiple request objects.\n\n"
    "**RETURNS:** Single mode: streaming binary file download. "
    "Batch mode: zip archive containing all downloaded files plus a `manifest.json`.\n\n"
    "**TRIGGER PHRASES:** download data file, get data, fetch file content, download experiment data, batch download files\n\n"
    "**EXAMPLES:**\n"
    "- 'Download the sequencing results for sample NHP-260225MIT-1'\n"
    "- 'Get the CSV data file for the water study'\n"
    "- 'Download multiple data files as a zip'\n"
)

# =============================================================================
# ProjectProxyViewSet (4 endpoints)
# =============================================================================

PROJECT_LIST_DESC = (
    "**SUMMARY:** List all research projects accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse or list all projects they have access to.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of project records with titles, descriptions, and IDs for team members, investigations, studies, assays, and data files.\n\n"
    "**TRIGGER PHRASES:** list projects, show projects, all projects, my projects, browse projects\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all projects I am part of'\n"
    "- 'What research projects are studying water toxicity?'\n"
    "- 'List all available projects'\n"
)

PROJECT_FETCH_DESC = (
    "**SUMMARY:** Fetch details for a specific research project by ID or name.\n\n"
    "**USE WHEN:** The user wants to view full metadata for ONE specific project.\n\n"
    "**ACCEPTS:** Project identifier (numeric SEEK ID or name string) as path parameter.\n\n"
    "**RETURNS:** Full project metadata including title, description, team members, investigations, studies, assays, protocols, and data files.\n\n"
    "**TRIGGER PHRASES:** get project, fetch project, project details, project info, show project\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the details of the primate immunology study'\n"
    "- 'Who are the team members on the water toxicity project?'\n"
    "- 'Get project 2558'\n"
)

PROJECT_CREATE_DESC = (
    "**SUMMARY:** Create a new research project with title, description, and team associations.\n\n"
    "**USE WHEN:** The user wants to set up a new research project in the system.\n\n"
    "**ACCEPTS:** Project data including title, description, and optional team/programme associations.\n\n"
    "**RETURNS:** The created project with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create project, new project, add project, set up project, register project\n\n"
    "**EXAMPLES:**\n"
    "- 'Create a new project for the cancer biomarker study'\n"
    "- 'Set up a project for the 2024 primate cohort'\n"
    "- 'Add a new research project called Water Toxicity Phase 2'\n"
)

PROJECT_UPDATE_DESC = (
    "**SUMMARY:** Update an existing research project by ID or name.\n\n"
    "**USE WHEN:** The user wants to modify a project's title, description, or team member associations.\n\n"
    "**ACCEPTS:** Project identifier as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated project with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update project, edit project, modify project, change project, rename project\n\n"
    "**EXAMPLES:**\n"
    "- 'Rename the water study project to Water Toxicity Phase 2'\n"
    "- 'Add a new team member to the primate research project'\n"
    "- 'Update the description for project 2558'\n"
)

# =============================================================================
# PeopleProxyViewSet (4 endpoints)
# =============================================================================

PEOPLE_LIST_DESC = (
    "**SUMMARY:** List all registered researchers and users in the system.\n\n"
    "**USE WHEN:** The user wants to browse all people/researchers/team members.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of person records with names, email addresses, institutions, and IDs for associated projects and contributions.\n\n"
    "**TRIGGER PHRASES:** list people, show researchers, all users, team members, browse people\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all researchers in the system'\n"
    "- 'Who are the scientists working on primate studies?'\n"
    "- 'List all registered users'\n"
)

PEOPLE_FETCH_DESC = (
    "**SUMMARY:** Fetch profile details for a specific researcher by their numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to view full details for ONE specific person/researcher.\n\n"
    "**ACCEPTS:** Person SEEK ID (numeric) as path parameter.\n\n"
    "**RETURNS:** Full person metadata including name, email, institution, and all linked projects, studies, assays, data files, and publications.\n\n"
    "**TRIGGER PHRASES:** get person, fetch researcher, person details, researcher profile, show person\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me Dr. Smith\\'s profile and projects'\n"
    "- 'What studies has this researcher contributed to?'\n"
    "- 'Get person 1652'\n"
)

PEOPLE_FETCH_CURRENT_DESC = (
    "**SUMMARY:** Fetch profile details for the currently authenticated user.\n\n"
    "**USE WHEN:** The user wants to view full details for themselves.\n\n"
    "**RETURNS:** Full person metadata including name, email, institution, and all linked projects, studies, assays, data files, and publications.\n\n"
    "**TRIGGER PHRASES:** get current person, fetch my profile, current person details, current profile, show my account\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me details about my profile'\n"
    "- 'What studies have I contributed to?'\n"
)

PEOPLE_CREATE_DESC = (
    "**SUMMARY:** Register a new researcher or user in the system.\n\n"
    "**USE WHEN:** The user wants to add a new person (researcher, collaborator) to the system.\n\n"
    "**ACCEPTS:** Person data including `first_name`, `last_name`, email, and optional institution.\n\n"
    "**RETURNS:** The created person record with their assigned ID.\n\n"
    "**TRIGGER PHRASES:** create person, add researcher, register user, new person, add collaborator\n\n"
    "**EXAMPLES:**\n"
    "- 'Add a new researcher named Jane Doe to the system'\n"
    "- 'Register a collaborator from the partner institution'\n"
    "- 'Create a person record for John Smith'\n"
)

USER_LIST_DESC = (
    "**SUMMARY:** List SEEK login accounts (User + linked Person) for superuser administration.\n\n"
    "**USE WHEN:** An admin needs to audit or browse registered SEEK logins with project membership.\n\n"
    "**DO NOT USE WHEN:** The caller is not a Django superuser, or the goal is profile-only CRUD without credentials — use `people/` instead.\n\n"
    "**RETURNS:** Admin records with SEEK user id, person id, login, email, activation state, and optional project/institution ids.\n\n"
)

USER_FETCH_DESC = (
    "**SUMMARY:** Fetch one SEEK login account by numeric SEEK user id.\n\n"
    "**USE WHEN:** An admin needs details for a specific login before update or deactivation.\n\n"
    "**ACCEPTS:** SEEK user id (numeric) as path parameter.\n\n"
    "**RETURNS:** A single admin user record (passwords are never returned).\n\n"
)

USER_CREATE_DESC = (
    "**SUMMARY:** Mint a new SEEK login (User + Person + project membership) and mirror a Django auth user.\n\n"
    "**USE WHEN:** A superuser needs a credential-bearing account that can authenticate to SEEK/Nessie — not a login-less Person profile.\n\n"
    "**ACCEPTS:** `login`, `password` (≥10 chars), `password_confirmation`, `email`, `first_name`, `last_name`, required `project_id` and `institution_id`, optional `is_superuser` (default false), optional `activate` (default true).\n\n"
    "**RETURNS:** Created ids and metadata; never echoes the password.\n\n"
)

USER_UPDATE_DESC = (
    "**SUMMARY:** Update or deactivate an existing SEEK login (prefer `active: false` over deletion).\n\n"
    "**USE WHEN:** An admin changes profile fields, password, project membership, Django superuser flag, or deactivates a former staff account.\n\n"
    "**ACCEPTS:** Partial update fields; deactivation sets SEEK inactive and Django `is_active=false`.\n\n"
    "**DO NOT USE WHEN:** Hard deletion is required — this API does not expose DELETE.\n\n"
)

PEOPLE_UPDATE_DESC = (
    "**SUMMARY:** Update an existing researcher's profile by their numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to modify a person's name, email, institution, or associations.\n\n"
    "**ACCEPTS:** Person SEEK ID as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated person record with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update person, edit researcher, modify person, change person, update profile\n\n"
    "**EXAMPLES:**\n"
    "- 'Update Dr. Smith\\'s email address'\n"
    "- 'Change the institution for this researcher'\n"
    "- 'Update person 1652 first name'\n"
)

# =============================================================================
# InvestigationProxyViewSet (4 endpoints)
# =============================================================================

INVESTIGATION_LIST_DESC = (
    "**SUMMARY:** List all investigations (research topics or themes) accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse all investigations/research topics.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of investigation records with titles, descriptions, and IDs for parent projects and child studies.\n\n"
    "**TRIGGER PHRASES:** list investigations, show research topics, all investigations, browse investigations\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all ongoing investigations'\n"
    "- 'What research topics are being studied in the primate project?'\n"
    "- 'List all investigations'\n"
)

INVESTIGATION_FETCH_DESC = (
    "**SUMMARY:** Fetch details for a specific investigation by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to view full metadata for ONE specific investigation/research topic.\n\n"
    "**ACCEPTS:** Investigation SEEK ID (numeric) as path parameter.\n\n"
    "**RETURNS:** Full investigation metadata including title, description, objectives, linked project, and all associated studies.\n\n"
    "**TRIGGER PHRASES:** get investigation, fetch investigation, investigation details, research topic info, show investigation\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the details of the immune response investigation'\n"
    "- 'What studies are part of this research topic?'\n"
    "- 'Get investigation 763'\n"
)

INVESTIGATION_CREATE_DESC = (
    "**SUMMARY:** Create a new investigation (research topic) within a project.\n\n"
    "**USE WHEN:** The user wants to define a new research topic or investigation under a project.\n\n"
    "**ACCEPTS:** Investigation data including title, description, and project relationship.\n\n"
    "**RETURNS:** The created investigation with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create investigation, new investigation, add research topic, set up investigation\n\n"
    "**EXAMPLES:**\n"
    "- 'Create a new investigation for studying vaccine efficacy'\n"
    "- 'Add a research topic about tumor microenvironment to the cancer project'\n"
    "- 'Set up an investigation under project 4475'\n"
)

INVESTIGATION_UPDATE_DESC = (
    "**SUMMARY:** Update an existing investigation by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to modify an investigation's title, description, or project association.\n\n"
    "**ACCEPTS:** Investigation SEEK ID as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated investigation with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update investigation, edit investigation, modify investigation, rename investigation\n\n"
    "**EXAMPLES:**\n"
    "- 'Rename the vaccine study investigation'\n"
    "- 'Update the objectives for the immune response research topic'\n"
    "- 'Change the description for investigation 763'\n"
)

# =============================================================================
# StudyProxyViewSet (4 endpoints)
# =============================================================================

STUDY_LIST_DESC = (
    "**SUMMARY:** List all studies (experimental studies within investigations) accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse all studies.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of study records with titles and self links.\n\n"
    "**TRIGGER PHRASES:** list studies, show studies, all studies, browse studies\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all ongoing studies'\n"
    "- 'What studies exist under the vaccine investigation?'\n"
    "- 'List all studies'\n"
)

STUDY_FETCH_DESC = (
    "**SUMMARY:** Fetch details for a specific study by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to view full metadata for ONE specific study.\n\n"
    "**ACCEPTS:** Study SEEK ID (numeric) as path parameter.\n\n"
    "**RETURNS:** Full study metadata including title, description, experimentalists, parent investigation, and associated assays.\n\n"
    "**TRIGGER PHRASES:** get study, fetch study, study details, show study\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the details of the dose response study'\n"
    "- 'What assays are part of this study?'\n"
    "- 'Get study 746'\n"
)

STUDY_CREATE_DESC = (
    "**SUMMARY:** Create a new study within an investigation.\n\n"
    "**USE WHEN:** The user wants to define a new study under an investigation.\n\n"
    "**ACCEPTS:** Study data including title, description, experimentalists, and the required parent investigation relationship.\n\n"
    "**RETURNS:** The created study with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create study, new study, add study, set up study\n\n"
    "**EXAMPLES:**\n"
    "- 'Create a new study for the vaccine dose response'\n"
    "- 'Add a study under investigation 763'\n"
    "- 'Set up a dose comparison study'\n"
)

STUDY_UPDATE_DESC = (
    "**SUMMARY:** Update an existing study by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to modify a study's title, description, experimentalists, or investigation association.\n\n"
    "**ACCEPTS:** Study SEEK ID as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated study with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update study, edit study, modify study, rename study\n\n"
    "**EXAMPLES:**\n"
    "- 'Rename the dose response study'\n"
    "- 'Update the experimentalists for study 746'\n"
    "- 'Change the description for study 746'\n"
)

# =============================================================================
# AssayProxyViewSet (4 endpoints)
# =============================================================================

ASSAY_LIST_DESC = (
    "**SUMMARY:** List all assays (experimental procedures) accessible to the current user.\n\n"
    "**USE WHEN:** The user wants to browse all available assays/experiments.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of assay records with titles, types, technologies, and IDs for linked studies, samples, and data files.\n\n"
    "**TRIGGER PHRASES:** list assays, show experiments, all assays, available assays, browse assays\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all available assays'\n"
    "- 'What experimental procedures have been run on the primate samples?'\n"
    "- 'List all assays in the system'\n"
)

ASSAY_FETCH_DESC = (
    "**SUMMARY:** Fetch details for a specific assay by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to view full metadata for ONE specific assay/experiment.\n\n"
    "**ACCEPTS:** Assay SEEK ID (numeric) as path parameter.\n\n"
    "**RETURNS:** Full assay metadata including title, assay type, technology, linked study, associated samples, protocols, and data files.\n\n"
    "**TRIGGER PHRASES:** get assay, fetch assay, assay details, experiment info, show assay\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the details of the RNA-seq assay'\n"
    "- 'What samples were processed in this flow cytometry experiment?'\n"
    "- 'Get assay 351'\n"
)

ASSAY_CREATE_DESC = (
    "**SUMMARY:** Define a new assay (experimental procedure) within a study.\n\n"
    "**USE WHEN:** The user wants to create a new assay and link it to a study, samples, or protocols.\n\n"
    "**ACCEPTS:** Assay data including title, `assay_class`, `assay_type`, `technology_type`, and study relationship.\n\n"
    "**RETURNS:** The created assay with its assigned ID and metadata.\n\n"
    "**TRIGGER PHRASES:** create assay, new assay, add experiment, set up assay, register assay\n\n"
    "**EXAMPLES:**\n"
    "- 'Create a new transcriptomics assay for the immune response study'\n"
    "- 'Add a histology assay to analyze the tissue samples'\n"
    "- 'Set up a new RNA-seq experiment under study 434'\n"
)

ASSAY_UPDATE_DESC = (
    "**SUMMARY:** Update an existing assay by its numeric SEEK ID.\n\n"
    "**USE WHEN:** The user wants to modify an assay's title, description, type, technology, or linked samples/data.\n\n"
    "**ACCEPTS:** Assay SEEK ID as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated assay with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update assay, edit assay, modify experiment, change assay, patch assay\n\n"
    "**EXAMPLES:**\n"
    "- 'Update the description for the RNA-seq assay'\n"
    "- 'Add additional samples to the flow cytometry experiment'\n"
    "- 'Change the technology type for assay 351'\n"
)

# =============================================================================
# SampleTypeProxyViewSet (4 endpoints)
# =============================================================================

SAMPLETYPE_LIST_DESC = (
    "**SUMMARY:** List all sample type definitions available in the system.\n\n"
    "**USE WHEN:** The user wants to see what types/categories of samples exist (e.g. tissue, cell line, imaging data).\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** Paginated list of sample type schemas including titles, descriptions, and attribute configurations.\n\n"
    "**TRIGGER PHRASES:** list sample types, show sample categories, all sample types, available types, what types exist\n\n"
    "**EXAMPLES:**\n"
    "- 'What types of samples can I register?'\n"
    "- 'Show me all available sample categories'\n"
    "- 'List all sample types in the system'\n"
)

SAMPLETYPE_FETCH_DESC = (
    "**SUMMARY:** Fetch the full schema definition for a specific sample type by ID or name.\n\n"
    "**USE WHEN:** The user wants to see the fields/attributes required for a specific sample type.\n\n"
    "**ACCEPTS:** Sample type identifier (numeric SEEK ID or title string) as path parameter.\n\n"
    "**RETURNS:** Full sample type schema including title, description, and all attribute specifications.\n\n"
    "**TRIGGER PHRASES:** get sample type, fetch sample type, sample type schema, sample type fields, show sample type\n\n"
    "**EXAMPLES:**\n"
    "- 'What fields are required for tissue samples?'\n"
    "- 'Show me the schema for imaging data samples'\n"
    "- 'Get sample type 12'\n"
)

SAMPLETYPE_CREATE_DESC = (
    "**SUMMARY:** Register a new sample type with custom attributes and validation rules.\n\n"
    "**USE WHEN:** The user wants to define a new category of samples with its own fields.\n\n"
    "**ACCEPTS:** Sample type data including title, description, and attribute definitions.\n\n"
    "**RETURNS:** The created sample type with its assigned ID and full schema definition.\n\n"
    "**TRIGGER PHRASES:** create sample type, new sample type, add sample category, define sample type\n\n"
    "**EXAMPLES:**\n"
    "- 'Create a new sample type for flow cytometry data'\n"
    "- 'Add a custom sample category for behavioral observations'\n"
    "- 'Define a new sample type called D.FLOW'\n"
)

SAMPLETYPE_UPDATE_DESC = (
    "**SUMMARY:** Modify an existing sample type's attributes or description.\n\n"
    "**USE WHEN:** The user wants to change a sample type's fields, description, or validation rules.\n\n"
    "**ACCEPTS:** Sample type identifier as path parameter; partial update payload with fields to change.\n\n"
    "**RETURNS:** The updated sample type with all current settings.\n\n"
    "**TRIGGER PHRASES:** update sample type, edit sample type, modify sample type, change sample type\n\n"
    "**EXAMPLES:**\n"
    "- 'Add a new required field to the tissue sample type'\n"
    "- 'Update the description for sequencing data samples'\n"
    "- 'Change the attributes for sample type 12'\n"
)

# =============================================================================
# SampleTypeChildrenViewSet (1 endpoint)
# =============================================================================

SAMPLETYPE_CHILDREN_DESC = (
    "**SUMMARY:** Get the distinct sample types of all samples derived from a given sample.\n\n"
    "**USE WHEN:** The user wants to know what TYPES of child samples exist below a specific sample "
    "(e.g. 'does this monkey have imaging data?'), not the actual sample records.\n\n"
    "**DO NOT USE WHEN:** The user wants to find parent samples that have children of specific types — "
    "use `POST sample_types/get_parents/parents_by_child_types` instead.\n\n"
    "**ACCEPTS:** A single sample identifier (UID or numeric SEEK ID) as path parameter.\n\n"
    "**RETURNS:** List of unique child sample types with their IDs, titles, and descriptions.\n\n"
    "**TRIGGER PHRASES:** child sample types, what types derived, sample type children, derived types, downstream types\n\n"
    "**EXAMPLES:**\n"
    "- 'What types of samples were collected from NHP-220630FLY-1-PUB?'\n"
    "- 'Does this monkey have any imaging data?'\n"
    "- 'What kinds of data exist for sample 1506?'\n"
)

# =============================================================================
# SamplesByChildTypesViewSet (1 endpoint)
# =============================================================================

PARENTS_BY_CHILD_TYPES_DESC = (
    "**SUMMARY:** Find parent samples that have derived samples of ALL specified types (AND logic).\n\n"
    "**USE WHEN:** The user wants to find which parent samples (e.g. animals) have descendants matching "
    "every requested sample type. Optionally filter by parent sample type.\n\n"
    "**DO NOT USE WHEN:** The user wants to know what types of children a SPECIFIC sample has — "
    "use `GET sampletypes/{uid}/child_types` instead.\n\n"
    "**ACCEPTS:** List of `child_sample_types` (titles or IDs); optional `parent_sample_type_filters`.\n\n"
    "**RETURNS:** List of matching parent samples with IDs, UIDs, sample types, and descriptions.\n\n"
    "**TRIGGER PHRASES:** samples with children of type, parents by child types, which animals have both, find samples with\n\n"
    "**EXAMPLES:**\n"
    "- 'Which monkeys have both imaging data and sequencing results?'\n"
    "- 'Find all animals that have tissue samples and cell line derivatives'\n"
    "- 'Which NHPs have both D.IMG and D.SEQ data?'\n"
)

# =============================================================================
# SampleProxyViewSet (4 endpoints)
# =============================================================================

SAMPLE_FETCH_DESC = (
    "**SUMMARY:** Fetch a single sample's metadata by SEEK ID or NExtSEEK UID.\n\n"
    "**USE WHEN:** The user wants to view details for ONE specific sample.\n\n"
    "**ACCEPTS:** Sample identifier (numeric SEEK ID or UID string) as path parameter.\n\n"
    "**RETURNS:** Sample metadata including title, UUID, timestamps, and IDs for related `sample_type`, `creators`, `projects`, `assays`, and `data_files`.\n\n"
    "**TRIGGER PHRASES:** get sample, fetch sample, sample details, sample info, show sample, look up sample\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the details of sample NHP-220630FLY-2-PUB'\n"
    "- 'What assays are linked to TIS-230324BOO-39-PUB?'\n"
    "- 'Get sample 321'\n"
)

SAMPLE_CREATE_DESC = (
    "**SUMMARY:** Create a new sample in SEEK with sample type and attributes.\n\n"
    "**USE WHEN:** The user wants to register/add a single new sample to the system.\n\n"
    "**ACCEPTS:** Sample creation payload with type, attributes, and `sample_type` relationship.\n\n"
    "**RETURNS:** The created sample with its assigned ID, UUID, and metadata.\n\n"
    "**TRIGGER PHRASES:** create sample, new sample, add sample, register sample\n\n"
    "**EXAMPLES:**\n"
    "- 'Register a new tissue sample for the water study'\n"
    "- 'Add a new blood draw sample for a monkey taken during this visit (PAV-220630FLY-3)'\n"
    "- 'Create a new sample of type TIS'\n"
)

SAMPLE_UPDATE_DESC = (
    "**SUMMARY:** Update an existing sample's attributes by SEEK ID or NExtSEEK UID.\n\n"
    "**USE WHEN:** The user wants to modify metadata for an existing sample.\n\n"
    "**ACCEPTS:** Sample identifier as path parameter; partial update payload with `attribute_map` changes.\n\n"
    "**RETURNS:** The updated sample with all current metadata.\n\n"
    "**TRIGGER PHRASES:** update sample, edit sample, modify sample, change sample, patch sample\n\n"
    "**EXAMPLES:**\n"
    "- 'Update NHP-220630FLY-1-PUB scientist to John Doe'\n"
    "- 'Change the study name for sample TIS-230324BOO-39-PUB'\n"
    "- 'Update sample 321 title'\n"
)

SAMPLE_DELETE_DESC = (
    "**SUMMARY:** Permanently delete a sample by SEEK ID or NExtSEEK UID.\n\n"
    "**USE WHEN:** The user wants to permanently remove a sample from the system.\n\n"
    "**ACCEPTS:** Sample identifier (numeric SEEK ID or UID string) as path parameter.\n\n"
    "**RETURNS:** Confirmation on success.\n\n"
    "**TRIGGER PHRASES:** delete sample, remove sample, destroy sample, drop sample\n\n"
    "**EXAMPLES:**\n"
    "- 'Remove sample NHP-220630FLY-1-PUB from the system'\n"
    "- 'Delete all records for sample 321'\n"
    "- 'Permanently remove this sample'\n"
)

# =============================================================================
# SampleAdvancedSearchViewSet (1 endpoint)
# =============================================================================

ADVANCED_SEARCH_DESC = (
    "**SUMMARY:** Search samples by type, attribute filters, and text matching with pagination.\n\n"
    "**USE WHEN:** The user wants to SEARCH, FILTER, or QUERY samples by criteria such as sample type, "
    "attribute values, text keywords, or combinations thereof. Also use when the user wants to "
    "retrieve all samples of a given type.\n\n"
    "**DO NOT USE WHEN:** The user already knows specific sample UIDs/IDs and wants to bulk-export "
    "their metadata — use `POST admin/samples/retrieve` instead.\n\n"
    "**ACCEPTS:** Sample type(s) (name or ID), attribute(s), search text(s), `match_type` (`PARTIAL`/`EXACT`), "
    "and logic operators (`AND`/`OR`) for combining multiple attributes or search terms.\n\n"
    "**RETURNS:** Paginated list of matching samples with full metadata (`json_metadata`, `sample_type`, etc.).\n\n"
    "**TRIGGER PHRASES:** search samples, find samples, filter samples, query samples, samples where, "
    "samples matching, group samples by, compare samples\n\n"
    "**EXAMPLES:**\n"
    "- 'Group all monkeys by sex and necropsy date'\n"
    "- 'Find all mice from water study that were treated with NDMA'\n"
    "- 'Show me all samples associated with T cell depletion'\n"
    "- 'How many monkeys have both CT scan data and sequencing data?'\n"
)

# =============================================================================
# SchemaRAGViewSet (2 endpoints)
# =============================================================================

SCHEMA_RAG_INGEST_DESC = (
    "**SUMMARY:** Ingest an OpenAPI schema from a URL for semantic retrieval.\n\n"
    "**USE WHEN:** The system needs to load and index an OpenAPI specification for subsequent "
    "endpoint retrieval queries. Creates a session with configurable TTL.\n\n"
    "**ACCEPTS:** `schema_url` (URL to an OpenAPI YAML/JSON spec); optional `ttl_minutes` (default 15).\n\n"
    "**RETURNS:** `session_id`, `schema_url`, TTL, `expires_at`, and `num_endpoints` ingested.\n\n"
    "**TRIGGER PHRASES:** ingest schema, load API spec, index OpenAPI, schema RAG ingest\n\n"
    "**EXAMPLES:**\n"
    "- 'Ingest the SEEK OpenAPI schema for endpoint retrieval'\n"
    "- 'Load the NExtSEEK API spec from the dev server'\n"
)

SCHEMA_RAG_RETRIEVE_DESC = (
    "**SUMMARY:** Retrieve relevant API endpoints from an ingested schema using semantic search.\n\n"
    "**USE WHEN:** The system needs to find which API endpoints are relevant to a natural language query, "
    "using a previously ingested schema session.\n\n"
    "**ACCEPTS:** `session_id` or `schema_url`, query text, `mode` (`minimal`/`full`), `top_k`, optional filters (`METHOD`, `TAG`), "
    "optional `resolved_terms` for boosting, `min_score` threshold.\n\n"
    "**RETURNS:** Ranked list of matching endpoints in `minimal` (`operationId`, `method`, `path`) or `full` mode "
    "(includes `request_schema`, `parameters`, `examples`). Optional debug info.\n\n"
    "**TRIGGER PHRASES:** retrieve endpoints, find API endpoint, schema RAG retrieve, which endpoint, route query\n\n"
    "**EXAMPLES:**\n"
    "- 'Find the endpoint for creating a new sample'\n"
    "- 'Which API endpoint handles searching samples by attribute?'\n"
    "- 'Retrieve endpoints related to project management'\n"
)

# =============================================================================
# EntityTreeViewSet (4 endpoints)
# =============================================================================

ENTITY_TREE_NODES_DESC = (
    "**SUMMARY:** Retrieve all sample type node attributes from the entity graph.\n\n"
    "**USE WHEN:** The user wants to see the full catalog of sample type nodes with their descriptions, "
    "clades, and metadata field definitions.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** List of sample type nodes with node title, ID, description, clade, and semicolon-delimited `metadata_fields`.\n\n"
    "**TRIGGER PHRASES:** entity tree nodes, sample type nodes, node attributes, graph nodes, all sample types with fields\n\n"
    "**EXAMPLES:**\n"
    "- 'What sample types exist in the system?'\n"
    "- 'Show me all node types and their descriptions'\n"
    "- 'List entity tree nodes with their metadata fields'\n"
)

ENTITY_TREE_EDGES_DESC = (
    "**SUMMARY:** Retrieve all edges (DERIVED_FROM relationships) between sample types.\n\n"
    "**USE WHEN:** The user wants to see how sample types are connected — what derives from what.\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** List of edges with source sample type, target sample type, and annotation (`internal_assay_title`).\n\n"
    "**TRIGGER PHRASES:** entity tree edges, sample type relationships, derivation edges, graph edges, type connections\n\n"
    "**EXAMPLES:**\n"
    "- 'How are sample types related to each other?'\n"
    "- 'Show me the derivation relationships between sample types'\n"
    "- 'What edges exist in the entity graph?'\n"
)

ENTITY_TREE_EDGE_ATTRS_DESC = (
    "**SUMMARY:** Retrieve all edges with extended metadata including assay IDs, study titles, and descriptions.\n\n"
    "**USE WHEN:** The user wants edges enriched with provenance metadata (study context, assay details).\n\n"
    "**ACCEPTS:** No required parameters; supports pagination query params.\n\n"
    "**RETURNS:** List of edges with source, target, annotation, `internal_assay_id`, `study_titles`, and description.\n\n"
    "**TRIGGER PHRASES:** edge attributes, enriched edges, edge metadata, edges with studies, edge details\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the edge attributes with study context'\n"
    "- 'What studies are associated with each derivation relationship?'\n"
    "- 'Get enriched edge metadata from the entity graph'\n"
)

ENTITY_TREE_LINEAGE_DESC = (
    "**SUMMARY:** Retrieve full lineage trees for one or more samples with optional edge enrichment.\n\n"
    "**USE WHEN:** The user wants complete upstream and downstream lineage for MULTIPLE samples at once, "
    "or needs edge attribute enrichment (`study_titles`, `description`) on the lineage.\n\n"
    "**DO NOT USE WHEN:** The user wants a simple tree for ONE sample without edge enrichment — "
    "use `GET sample-tree/{uid}/tree` instead.\n\n"
    "**ACCEPTS:** List of `sample_ids` (UIDs or SEEK IDs); optional `include_attributes` flag; optional `timeout`.\n\n"
    "**RETURNS:** Array of lineage trees, each with nodes (`id`, `uuid`, `sample_type`, `color`, `parentIds`) and "
    "relationships (`child_id`, `parent_id`, assay info, optional `study_titles`/`description`).\n\n"
    "**TRIGGER PHRASES:** sample lineage batch, multiple sample trees, lineage for several samples, bulk lineage\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me all samples derived from NHP-220630FLY-1-PUB'\n"
    "- 'Get the full lineage for tissue samples TIS-230324BOO-39-PUB and TIS-230324BOO-40-PUB'\n"
    "- 'Retrieve lineage trees for these three samples with edge attributes'\n"
)

# =============================================================================
# BatchUploadViewSet (5 endpoints)
# =============================================================================

BATCH_UPLOAD_START_DESC = (
    "**SUMMARY:** Start a batch upload job for bulk sample creation from Excel files or direct row data.\n\n"
    "**USE WHEN:** An authenticated user wants to bulk-create many samples at once.\n\n"
    "**DO NOT USE WHEN:** The user wants to create a single sample -- use `POST samples/` instead.\n\n"
    "**ACCEPTS:** Two input modes (if both provided, rows takes priority):\n"
    "1. **Direct rows** (application/json): `rows` array of InputRowModel objects -- bypasses Excel parsing\n"
    "2. **File upload** (multipart/form-data): one or more `.xlsx` files under the `file` key\n\n"
    "Also requires: `project_id` (required top-level; per-row project_id can override); "
    "optional `person_id` (defaults to authenticated user); "
    "optional `update_existing` (default false); optional `neo4j_only` (default false -- skip SQL INSERT, "
    "sync only to Neo4j; assumes samples already exist in DB with pre-assigned UIDs); "
    "optional `config_overrides` (JSON object).\n\n"
    "**RETURNS:** `job_id` (Celery task ID) and initial status `queued`.\n\n"
    "**FILE SIZE LIMIT:** Total upload size must not exceed 200 MB (configurable via "
    "`BATCH_UPLOAD_MAX_TOTAL_BYTES` env var). Returns 413 if exceeded. Only applies to file upload mode.\n\n"
    "**ERROR CODES:**\n"
    "- 400: Missing input, structural errors (no files/rows, missing project_id)\n"
    "- 401: Authentication required\n"
    "- 413: Total upload size exceeds limit (file upload mode only)\n"
    "- 422: Pydantic validation error on rows or request body\n\n"
    "**TRIGGER PHRASES:** batch upload, bulk create samples, upload spreadsheet, bulk sample import, "
    "batch sample creation, upload Excel, multi-file upload, programmatic sample creation, "
    "neo4j only, neo4j sync, re-sync neo4j, graph sync\n\n"
    "**EXAMPLES:**\n"
    "- 'Upload these Excel files to create samples in bulk for project 4475'\n"
    "- 'Programmatically create 500 samples via the API'\n"
    "- `curl -u user:pass -F 'file=@samples.xlsx' -F 'project_id=4475' https://host/nextseek_api/batch-upload/start/`\n"
    "- `curl -u user:pass -H 'Content-Type: application/json' "
    "-d '{\"project_id\": 4475, \"rows\": [{\"SampleType\": \"M.Mice\", \"json_metadata\": {\"Name\": \"mouse1\"}}]}' "
    "https://host/nextseek_api/batch-upload/start/`\n\n"
    "---\n\n"
    "## Input Mode 1: Direct Rows (application/json)\n\n"
    "Send a JSON body with a `rows` array. Each element is an InputRowModel:\n"
    "```json\n"
    "{\"project_id\": 4475, \"rows\": [\n"
    "  {\"SampleType\": \"M.Mice\", \"json_metadata\": {\"Name\": \"mouse1\", \"Strain\": \"C57BL/6\"}},\n"
    "  {\"SampleType\": \"M.Mice\", \"json_metadata\": {\"Name\": \"mouse2\", \"Strain\": \"BALB/c\"}}\n"
    "]}\n"
    "```\n"
    "Skips Excel parsing entirely. Validated via Pydantic in the view (returns 422 on failure).\n"
    "**Note:** Ontology validation is not performed in rows mode.\n\n"
    "**Required per row:** `SampleType` (str), `json_metadata` (str or dict)\n"
    "**Optional:** `UID`, `assay_ids`, `project_id` (overrides top-level), "
    "`study_title`, `study_id`, `sop_id`, `assay_titles`\n\n"
    "## Input Mode 2: File Upload (multipart/form-data)\n\n"
    "Upload one or more Excel (.xlsx) files via the `file` field. Format is auto-detected per file.\n\n"
    "### Flat Format (single sheet)\n\n"
    "- **uid** -- Sample UID. Leave empty for auto-generation.\n"
    "- **sampletype** -- Sample type name (alias: `sample_type`)\n"
    "- **json_metadata** -- JSON metadata string (alias: `jsonmetadata`)\n"
    "- **assay_ids** -- Comma-separated assay IDs (alias: `assayids`)\n"
    "- Optional: `project_id`, `study_title`, `study_id`, `sop_id`, `assay_titles`\n\n"
    "### Traditional 4-Sheet Format (sample-type-specific)\n\n"
    "Auto-detected when all 4 sheet names are present: `Instructions`, `Samples`, `Assay`, `Ontology`.\n"
    "**Each file must contain exactly one sample type.** Multiple types in INSTRUCTIONS = rejection.\n\n"
    "**Instructions sheet** -- Maps column headers to database fields:\n"
    "| Field | Database Field | Field Type | Ontology |\n"
    "|---|---|---|---|\n"
    "| Name | M.Mice::Name | Text | |\n"
    "| Strain | M.Mice::Strain | Controlled Ontology | Strain |\n\n"
    "Duplicate Field headers are rejected. `Database Field` format: `SampleType::AttributeName`.\n\n"
    "**Samples sheet** -- One row per sample. All non-empty columns become json_metadata. "
    "UID column required (even if empty for auto-generation).\n\n"
    "**Assay sheet** -- Links sample type to assays (SampleType, AssayType, Assay, Direction).\n\n"
    "**Ontology sheet** -- Controlled vocabularies. Validation is strict; violations reject the file.\n\n"
    "## Notes\n\n"
    "- Column headers are case-insensitive and whitespace is trimmed (file upload mode).\n"
    "- Unknown extra columns are ignored, with a warning in the summary CSV.\n"
    "- Validation conflicts (e.g., UID mismatch) reject only that row; the job continues.\n"
    "- Auto-generated UIDs use format `{PREFIX}-{YYMMDD}{LABABBV}-{INDEX}`.\n"
)

BATCH_UPLOAD_STATUS_DESC = (
    "**SUMMARY:** Get the status and progress of a batch upload job.\n\n"
    "**USE WHEN:** The user wants to check on the progress of a previously started batch upload.\n\n"
    "**ACCEPTS:** `job_id` as path parameter.\n\n"
    "**RETURNS:** Job state (`PENDING`, `STARTED`, `PROGRESS`, `SUCCESS`, `FAILURE`, `REVOKED`), progress metadata, and final result if complete.\n\n"
    "**TRIGGER PHRASES:** batch upload status, job progress, check upload, upload status, job state\n\n"
    "**EXAMPLES:**\n"
    "- 'What is the status of batch upload job abc123?'\n"
    "- 'Check the progress of my bulk upload'\n"
    "- 'Is the batch job done yet?'\n"
)

BATCH_UPLOAD_CANCEL_DESC = (
    "**SUMMARY:** Cancel (revoke) a running batch upload job.\n\n"
    "**USE WHEN:** The user wants to stop a batch upload job that is currently running.\n\n"
    "**ACCEPTS:** `job_id` as path parameter.\n\n"
    "**RETURNS:** `204 No Content` on success.\n\n"
    "**TRIGGER PHRASES:** cancel batch upload, stop upload, revoke job, abort batch, kill upload job\n\n"
    "**EXAMPLES:**\n"
    "- 'Cancel the batch upload job abc123'\n"
    "- 'Stop the running bulk upload'\n"
    "- 'Abort the batch job'\n"
)

BATCH_UPLOAD_SUMMARY_DESC = (
    "**SUMMARY:** Download the summary CSV for a completed batch upload job.\n\n"
    "**USE WHEN:** The user wants to download the results/summary of a finished batch upload.\n\n"
    "**ACCEPTS:** `job_id` as path parameter.\n\n"
    "**RETURNS:** CSV file download with per-row upload results.\n\n"
    "**TRIGGER PHRASES:** batch upload summary, download results, upload report, batch results CSV\n\n"
    "**EXAMPLES:**\n"
    "- 'Download the summary for batch upload job abc123'\n"
    "- 'Get the results CSV from the completed bulk upload'\n"
    "- 'Show me the batch upload report'\n"
)

BATCH_UPLOAD_LIST_DESC = (
    "**SUMMARY:** List the authenticated user's batch upload jobs with pagination and live Celery state.\n\n"
    "**USE WHEN:** The user wants to see their own batch upload history, check which jobs are running, "
    "or find a specific job ID without remembering it.\n\n"
    "**DO NOT USE WHEN:** The user already knows a specific `job_id` and wants detailed progress — "
    "use `GET batch-upload/status/{job_id}/` instead.\n\n"
    "**ACCEPTS:** Optional query params: `page` (default `1`), `page_size` (default `20`, max `100`).\n\n"
    "**RETURNS:** Paginated list of the user's jobs, each with `job_id`, `project_id`, `created_at` (Unix timestamp), "
    "and current Celery `state` (`PENDING`, `STARTED`, `PROGRESS`, `SUCCESS`, `FAILURE`, `REVOKED`). "
    "Also includes `total` (total job count), `page`, and `page_size`. Jobs are sorted newest-first. "
    "Only returns jobs belonging to the requesting user.\n\n"
    "**TRIGGER PHRASES:** list batch uploads, my uploads, recent uploads, batch jobs, upload history, my jobs\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me my recent batch upload jobs'\n"
    "- 'List all my batch uploads'\n"
    "- 'What batch jobs have I run?'\n"
    "- 'Show page 2 of my upload history'\n"
)

BATCH_UPLOAD_VALIDATE_DESC = (
    "**SUMMARY:** Synchronously validate a batch upload without creating anything. "
    "Runs the upload pipeline through the TRANSFORM stage and stops before INSERT.\n\n"
    "**USE WHEN:** A user wants to check an Excel file (or programmatic rows) for errors "
    "*before* committing a real upload via `POST batch-upload/start/`.\n\n"
    "**DO NOT USE WHEN:** The user actually wants to create samples -- use "
    "`POST batch-upload/start/` instead. Validation has no side effects: no job is "
    "created, no summary CSV is written, and no database rows are inserted or updated.\n\n"
    "**ACCEPTS:** Two input modes (same as `start/`; if both provided, rows takes priority):\n"
    "1. **Direct rows** (application/json): `rows` array of InputRowModel objects\n"
    "2. **File upload** (multipart/form-data): one or more `.xlsx` files under the `file` key\n\n"
    "Also requires `project_id` (integer; required, but no project mutation occurs). "
    "Optional `checks` (query or form param) -- a comma-separated subset of:\n"
    "- `structure` (default) -- CONVERT + the json_metadata attribute-name check. Fast.\n"
    "- `name_check` -- additionally check whether each sample Name already exists in the "
    "database (adds DB load proportional to row count).\n"
    "- `dag` -- additionally build the parent/child DAG and report orphans and cycles.\n\n"
    "**RETURNS:** `200 OK` with a `ValidationResult`: `valid` (bool), `summary` (one-line "
    "human string), `totals`, `errors[]`, `warnings`, `checks_run`, `checks_skipped`. "
    "No `job_id` and no `summary_path` (validation is synchronous -- the body is the result).\n\n"
    "**FILE SIZE LIMIT:** Total upload size must not exceed 200 MB (configurable via "
    "`BATCH_UPLOAD_MAX_TOTAL_BYTES`). Returns 413 if exceeded. File upload mode only.\n\n"
    "**ERROR CODES:**\n"
    "- 400: Missing input (no files/rows), missing project_id, or invalid `checks` value\n"
    "- 401: Authentication required\n"
    "- 413: Total upload size exceeds limit (file upload mode only)\n"
    "- 422: Pydantic validation error on rows or request body\n\n"
    "**TRIGGER PHRASES:** validate batch upload, pre-check spreadsheet, dry run upload, "
    "check Excel before upload, validate samples, batch upload validation\n\n"
    "**EXAMPLES:**\n"
    "- 'Validate this spreadsheet before I upload it for project 4475'\n"
    "- `curl -u user:pass -F 'file=@samples.xlsx' -F 'project_id=4475' "
    "'https://host/nextseek_api/batch-upload/validate/?checks=structure,name_check'`\n"
)
