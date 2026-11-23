# Admin pages

These pages are for administrators. You need an administrator account to see them: the Admin section of the left menu only shows for administrators.

| Page | Path | What it does |
|---|---|---|
| Admin Panel | `/admin/` | The standard Mezzanine admin site that comes with NExtSEEK |
| Definitions | `/seek/admin/definitions/` | Manage the sample type and assay definitions in one place, in six tabs (below) |
| Project Members | `/seek/admin/project_members/` | Pick a project and open its members page in SEEK |

## Definitions

The Definitions page holds what NExtSEEK knows about each sample type and assay. You write the words; once the sync is
switched on for the site, it keeps the rest from SEEK and from the data, seconds after each save. Until then a note at
the top of the page says so.

| Tab | What it does |
|---|---|
| Sample types | The [sample types](sample-types.md) by clade. Pick one to edit its name, description and tags, and to move each of its fields between the required, standard and possible tiers. Its parents, children and assays are shown read-only, each marked **D** (an assay definition allows it) or **O** (seen in the data) |
| Assays | Each [assay](assays.md) definition: its description, tags, other names and the sample types it takes and makes. Write parents and children as sample type codes; a comma separates groups and `or` separates alternatives (`TIS or CEL, AB`). The connections seen in the data are shown beside it |
| Needs attention | Connections seen in the data that no assay definition allows, SEEK sample types and internal assays with no definition (each with a **Create** button), hand-typed entries nothing backs, and assay names that differ only in case or spacing |
| Attributes | Add, remove and edit the attributes of a sample type. SEEK itself does not allow this. It is useful when a new group collects a field that an existing sample type does not have yet. Once the sync is on, a new attribute lands in the possible tier; until then it shows in the tier card marked new |
| Clades | Set each clade's color and assign sample types to clades. See [Sample types](sample-types.md#the-four-clades) |
| Assay map | Connect the assays registered in SEEK to NExtSEEK's internal assays, the general procedures listed in the [Assays](assays.md) catalog |

The old addresses (`/seek/samples/attributes/`, `/seek/admin/clades/`, `/seek/admin/internal_assays/`) open the
matching tab.

