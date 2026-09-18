# Labs from SEEK, investigations as rows, and the generated investigation list (design)

- Date: 2026-09-18
- Branch: `feat/projects-labs-context`, cut at `9dadbfd8`, which is the context generator branch `wf/p6` plus its
  rework. `origin/dev-graph` is not an ancestor. It receives `wf/p6` and this work together, at the end.
- Status: draft. Design only: nothing here is built, and no database, graph or SEEK instance was read to write it.
  The SEEK table shapes below come from the committed seed `startup/seed/seek_production.sql.gz`, and the API
  shapes from the committed OpenAPI description `nextseek_api/tests/fixtures/fairdomhub_openapi_v3_resolved.yaml`.
- Tracking: none yet. File an issue per `docs/ISSUE-CONVENTIONS.md` after approval.
- Builds on: the Nessie master plan of 2026-09-17 (tasks 6.15, 6.15b, 6.15c, 6.15d, 6.16 and 13c) and the six phase 6
  verification verdicts of 2026-09-18 (pi-parsing, capabilities-block, column-fidelity, idempotency, seed-shape,
  sql-safety). Both live outside the repository.
- Every example lab below is invented. No real lab title, person name or production count appears in this file,
  and none may appear in the tests this spec asks for.

## 1. What this changes

Lab codes and lab heads' surnames stop coming from free text. SEEK already records every lab as an Institution
whose title reads `<CODE>-<Name> Lab (<Affiliation>)`, where `CODE` is the three-letter code sample UIDs carry
(`TYPE-YYMMDDCODE-n`) and `Name` is the lab head's surname. The daily context export reads those titles, with the
projects each institution belongs to, into a new context file, and every project row the runtime reads carries its
labs. The entity agent then resolves lab and person names in code against those records, and emits a lab code only
from a record it matched. A person name that matches no lab becomes a `Scientist` value, never a guessed code.

Separately, investigations become rows of `projects_context`, and the investigation list in `capabilities.md`
becomes a generated block built from those rows: production's investigations plus TCGA, with TCGA marked as not
present on every instance.

## 2. Operator decisions this spec takes as fixed (2026-09-18)

| Id | Decision |
|---|---|
| OD1 | Lab codes and lab head names are mined from SEEK institution titles, never curated by hand and never parsed from `projects_context.pi`. A title that does not fit is reported, never guessed. |
| OD2 | SEEK API if it gets this done, otherwise a read-only query of the SEEK database, at most once a morning, never a write, never per turn. |
| OD3 | The result is a context file, injected into the projects context so every project row the runtime reads carries its labs. `config.py::map_project` must carry the labs through. |
| OD4 | The entity agent matches lab and PI names in code and emits `lab_codes` only from a matched record. A person name that matches no lab is the `Scientist` attribute (13c). The prompt side of 13c belongs to the prompt chat. |
| OD5 | The `capabilities.md` investigation list is generated from `projects_context`: production's names plus TCGA, with a note on names that are not on every instance. Investigations become rows per 6.15c, and every consumer of `projects_context` is audited first per 6.15d. |
| OD6 | The database write (6.16) stays gated on the operator's xlsx sign-off. Install and reset keep loading what `origin/dev-graph` loads today. |

## 3. Decisions this spec makes

| Id | Decision | Why |
|---|---|---|
| **L1** | SEEK source: a read-only SQL query of the SEEK database, over the connection the context export already holds. Not the API. | Section 4.2. |
| **L2** | Cadence: the existing context export's gate, unchanged. The labs read happens inside `_fetch_context_files_from_db`, so at most once per UTC day, at the first `ChatConfig` build that finds no `.context_db_refresh` marker dated today. | Section 4.4. |
| **L3** | Titles are parsed by one strict grammar. A title that does not fit is excluded and reported with a reason code, in the labs file, in one log line per refresh, and by a read-only report command. | OD1. Section 5. |
| **L4** | The labs file is `labs_db.json` in `ChatConfig.CONTEXT_DIR`, beside `projects_db.json`. It is runtime-only: gitignored, never baked into the cc-agent image, never committed. | Real lab titles never enter the public repository. Section 6.1. |
| **L5** | Every project row in `projects_db.json` carries `labs: [{code, name, affiliation}]`. Investigation rows carry none. | OD3. Section 6.3. |
| **L6** | `map_project` stays a nested function with the same explicit column reads and adds the `labs` key from the export's own fetch. It does not start passing unknown columns through. | A generic pass-through would ship any future column to the entity agent on every turn, and would blind the column-fidelity test, whose fixture is `map_project`'s own read list. Section 6.4. |
| **E1** | The entity agent resolves names deterministically after the LLM returns, against `ChatConfig.LABS`, by the rules in section 7. | OD4. |
| **E2** | `labs` becomes the matched labs' surnames as SEEK spells them; `lab_codes` only matched codes; a new `lab_matches` says what matched and how; a new `scientists` holds person names that matched no lab. | Section 7.5. |
| **E3** | A lab match never adds a project scope. | An institution can belong to several projects, and a project holds several labs' samples: the UID code is the lab's exact scope. The v2 graph prompt already says a lab scope "is not a project". |
| **E4** | A `scientists` entry is also appended to `keywords`. | The v2 graph prompt already routes a person name in `resolved.keywords` to the `Scientist` attribute, and `advanced_search`'s text search covers the `Scientist` field, so both engines use the name before any prompt changes. The prompt chat may drop this once its prompts read `scientists`. |
| **E5** | `helpers/lab_code.py::lab_code` (the first three letters of any name) is removed. | Its only caller is replaced, and keeping it invites reuse of the rule OD4 retires. |
| **P1** | `pi` stays, as curated display prose. `parse_pi`, `with_pi_names` and the `pi_names` column are retired. | Section 8. |
| **I1** | `projects_context`'s natural key becomes `(name, entity_type)`. | The real CSBC and MetNet investigations share their exact titles with the CSBC and MetNet project rows. Section 9.1. |
| **I2** | Nine investigation rows: the plan's eight plus BioMicroCenter, which the counts may still refuse. | Section 9.3. |
| **I3** | Availability is a curated, generator-only key `present_on` (a list of instance profiles, absent meaning every instance). It renders the note in the block and is never written to the database. | Section 9.5. |
| **I4** | The refusal reads a counts file that names the instance it was measured on and enumerates every Investigation in the graph, and it treats "absent here" differently from "present and empty". | This answers the verdicts' finding that one instance's counts cleared a list shipped to every instance. Section 10.3. |
| **I5** | `drift.py` applies the same absent-versus-empty rule to names the block marks as not on every instance. | Section 10.4. |
| **I6** | A blocking, graph-free gate pins the committed block to the investigation rows. | Section 10.5. |
| **I7** | Investigation titles leave the project rows' `alternative_names`. An investigation row may repeat its own parent project's name or aliases; no other alias may be shared across rows. | Section 9.4. |
| **C1** | Every consumer of `projects_context` gets the filter in section 11 before any database holds an investigation row. | 6.15d. |

## 4. SEEK source and cadence

### 4.1 What the SEEK API offers

The committed OpenAPI description answers the question in two calls per project:

- `GET /projects/{id}` (`readProject`): `data.relationships.institutions.data[].id`.
- `GET /institutions` (`listInstitutions`): `data[].id` and `data[].attributes.title`.
- `GET /institutions/{id}` (`readInstitution`) gives the reverse link, `data.relationships.projects.data[].id`.

SEEK derives a project's institutions from its `work_groups` rows, which is also what the query in 4.3 reads.

### 4.2 Why the API does not get it done here

1. **No identity for an unattended read.** The SEEK API helpers send the requesting user's own credentials
   (`nextseek_api/seek_api_helpers.py::get_current_logged_in_user`, then `call`). The export runs while `ChatConfig`
   is constructed at settings import, where there is no request and no user. The only other SEEK identity in the
   repository is the proxy ViewSets' shared session, which `nextseek_api/CLAUDE.md` says not to copy. An API read would need a new service credential on every box. Whether SEEK serves projects and
   institutions anonymously cannot be checked without calling a live SEEK, which this work may not do, and nothing in
   the repository relies on it.
2. **It adds SEEK's Rails process to every app start.** A SEEK that is restarting or memory-starved would slow or fail
   Django's settings import. MySQL is already a hard dependency of that step.
3. **One SELECT against several calls.** The export already holds a MySQL connection, and `ChatConfig` already reads
   `seek_production.projects` and `seek_production.investigations` over it (`_load_name_to_id_from_db`).
4. **Same data.** Both paths read the same `work_groups` rows.

So the source is the database. The fetch sits behind one function, `labs.fetch_institution_rows(conn)`, whose rows
are `(institution_id, title, project_id)`, so moving to the API later replaces one function and nothing else.

### 4.3 The query

Tables and columns as the committed SEEK seed declares them: `institutions(id, title, ...)` and
`work_groups(id, name, institution_id, project_id, ...)`. The schema name `seek_production` is spelled exactly as
`_load_name_to_id_from_db` spells it.

```sql
SELECT i.id AS institution_id, i.title AS title, wg.project_id AS project_id
FROM seek_production.institutions AS i
LEFT JOIN seek_production.work_groups AS wg ON wg.institution_id = i.id
ORDER BY i.id, wg.project_id
```

- The `LEFT JOIN` keeps an institution linked to no project. It is still a lab, and its code still scopes UIDs.
- The statement is a module constant: no interpolation, one statement, `SELECT` only.
- **Read-only is enforced by the server, not by trust.** The app's MySQL user can write, so the read runs in its own
  transaction: end any open implicit transaction on the connection (`rollback()`), then
  `start_transaction(readonly=True)`, the SELECT, then `rollback()`. In a read-only transaction the server refuses
  any write. A SELECT-only MySQL account would be stronger; creating one needs root and is the operator's option,
  not a requirement.
- A failure (no such schema, no such table, a timeout) is logged once and never raises out of the export.

### 4.4 Cadence

**Chosen: the existing gate, reused.** The labs read is the first statement of `_fetch_context_files_from_db`, so
it runs exactly when the context export runs: at most once per UTC day, at the first `ChatConfig` build that finds
no `.context_db_refresh` marker dated today. `ChatConfig` is built at process start (settings import), so in practice
that is the first rebuild, restart or worker spawn of a new UTC day, never a turn.

This does not contradict "at most once a morning", which limits the rate: it is at most one read per UTC day per
starting process. Two differences are stated rather than hidden:

- The UTC day turns over at 20:00 Eastern in summer and 19:00 in winter, so the read is not pinned to a morning.
- It is an upper bound, not a schedule. With long-lived workers the labs are as fresh as the last process start on a
  new UTC day. Labs change a few times a year, so this is acceptable. A guaranteed morning refresh would be a
  scheduled trigger, which is out of scope.

A second, morning-anchored marker was rejected: two cadences would let `projects_db.json` and `labs_db.json`
disagree for hours.

Four gunicorn workers starting together on a new UTC day may each run the export. That is four SELECTs, and the
file is written atomically (a temporary file, then `os.replace`), so readers never see a torn file.

### 4.5 The first live run is the operator's

`python -m chat_nextseek.labs --report`, run inside the app container, connects exactly as `_connect_db(env="prod")`
does (`MYSQL_HOST_PROD`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PROD_PASSWORD`), runs the read of 4.3 in its read-only
transaction, prints the document of 6.2 to stdout, and writes nothing. It exits 2 when it cannot connect. This is
how the operator sees every title's fate before any file is written.

## 5. The title grammar

**Normalisation first, and nothing more:** NFC, strip, collapse runs of whitespace to one space. No dash, case or
spelling repair: a title that needs one is reported so it gets fixed in SEEK.

**The grammar:**

```
^(?P<code>[A-Z]{3})-(?P<name>[^\s()][^()]*?) Lab \((?P<affiliation>[^()]*[^\s()][^()]*)\)$
```

- `code`: exactly three ASCII capital letters, then one ASCII hyphen-minus with no space around it.
- `name`: the lab head's surname as SEEK spells it. Letters (any script), spaces, hyphens, apostrophes and periods
  only; it starts with a letter and holds no digit. Multi-word and hyphenated surnames are allowed.
- ` Lab `: the word `Lab`, capital L, one space either side.
- `affiliation`: non-empty, in one pair of parentheses that ends the title.

**Reason codes** for a title that does not fit, tested in this order, first hit wins:

| Reason | Example (invented) |
|---|---|
| `no_title` | a NULL or empty title |
| `non_ascii_dash` | `OAK–Oakley Lab (MIT)` (an en dash where the hyphen goes) |
| `code_not_three_letters` | `QU-Quail Lab (MIT)`, `QUAL-Quail Lab (MIT)` |
| `no_code_prefix` | `Example Institute of Technology` |
| `no_lab_word` | `PIN-Pinecrest Laboratory (MIT)`, `PIN-Pinecrest lab (MIT)` |
| `no_affiliation` | `PIN-Pinecrest Lab` |
| `trailing_text` | `PIN-Pinecrest Lab (MIT) old` |
| `bad_name_characters` | `PIN-Pinecrest2 Lab (MIT)` |

**Conflicts are reported and kept:**

- `name_shared`: two records with one surname (`FEN-Fenwick Lab (MIT)`, `FEW-Fenwick Lab (Harvard)`). Both are kept;
  the entity agent disambiguates (rule M6).
- `code_shared`: two institutions with one code. Both records are kept, because the code scopes the same UIDs
  whichever name owns it.

**Where a report goes:** the labs file's `unparsed` and `conflicts` lists, one log line per refresh
(`[CONFIG][LABS] ... unparsed institution ids [...]`), and the `--report` output. A bad title never raises and never
blocks the export.

## 6. The labs file and the project rows

### 6.1 Name, location, life cycle

`labs_db.json`, in `ChatConfig.CONTEXT_DIR`. Written by the export, read by `ChatConfig` at construction.

- Gitignored in `NessieAI/chat_nextseek/src/chat_nextseek/context/.gitignore`, so a run of the config against a
  checkout cannot put real lab titles into a commit.
- Not in `CANONICAL_CONTEXT_FILES`, so not baked into the cc-agent image. The CC route gets lab resolution through the
  entity op, which runs in the app and whose whole result the `UserPromptSubmit` hook injects.
- Ignored by name in `NessieAI/tests/cc/test_cc_context_drift_guard.py`'s file scan, beside the existing `.npz`
  exclusion, so a checkout that ran the config does not fail the guard.
- Excluded from the app build context in `.dockerignore`, so a host checkout's stale copy is never baked.
- Not added to `_ensure_context_files`'s targets: its absence must never force a refresh on every start.

### 6.2 The labs file shape (verbatim)

```json
{
  "version": 1,
  "source": "seek_production.institutions LEFT JOIN seek_production.work_groups (one read-only SELECT)",
  "fetched_at": "2026-09-19T06:02:11Z",
  "labs": [
    {
      "code": "ASH",
      "name": "Ashgrove",
      "affiliation": "BWH",
      "title": "ASH-Ashgrove Lab (BWH)",
      "institution_id": 41,
      "project_ids": [4, 12]
    }
  ],
  "unparsed": [
    {"institution_id": 7, "title": "Example Institute of Technology", "project_ids": [2], "reason": "no_code_prefix"}
  ],
  "conflicts": [
    {"kind": "name_shared", "name": "Fenwick", "codes": ["FEN", "FEW"]},
    {"kind": "code_shared", "code": "ASH", "institution_ids": [41, 58]}
  ]
}
```

`labs` sorted by `code`, then `institution_id`; `project_ids` sorted and de-duplicated, `[]` for an institution in no
project.

### 6.3 What a project row carries (verbatim)

Every row of `projects_db.json` whose `entity_type` is `project` (or missing) gains one key:

```json
{
  "name": "MetNet",
  "entity_type": "project",
  "project_id": 4,
  "...": "every existing key, unchanged",
  "labs": [
    {"code": "ASH", "name": "Ashgrove", "affiliation": "BWH"}
  ]
}
```

Sorted by `code`. `[]` when no parsed lab belongs to the project, or when no labs document exists. Investigation rows
carry no `labs` key: their labs are their parent project's.

### 6.4 `map_project`

- Stays a nested `def map_project(row: dict) -> dict:` inside `_fetch_context_files_from_db`, reading every curated
  column through `lower.get("<column>")` exactly as today. `NessieAI/tests/api/test_context_gen.py` parses that
  function's source for its read list, so moving or restructuring it breaks another unit's test.
- Adds `"labs"` from a `labs_by_project` mapping computed in the enclosing scope, for project rows only.
- `labs_by_project` comes from this run's fetch. When the fetch fails it comes from the `labs_db.json` already on
  disk, so a transient failure does not strip labs from the rows. With neither, every project row gets `labs: []`.

### 6.5 `ChatConfig` attributes

| Attribute | Value |
|---|---|
| `LABS` | the `labs` list of `labs_db.json`, each record checked (`code` three capitals, `name` non-empty); `None` when no readable file exists. `None` means unavailable; `[]` means SEEK has no parseable lab. |
| `LABS_STATUS` | `{"source": "fetched" \| "previous_file" \| "unavailable", "fetched_at": ..., "unparsed": <n>}` |
| `FULL_PROJECTS` / `MIN_PROJECTS` | every row, projects and investigations. The entity agent sees both, and each row says its `entity_type`. |
| `FULL_PROJECTS_MAP` | project rows only (`entity_type` `project` or missing), keyed by `name`. Built from every row, as today, a same-named investigation row (CSBC, MetNet) would silently replace the project row in this dict. |
| `FULL_INVESTIGATIONS_MAP` | new: investigation rows, keyed by `name`. |
| `PROJECT_NAME_TO_ID` | `_merge_project_name_to_id` merges project rows only. An investigation row carries its owner's `project_id`, so merging it would turn "TCGA" or "Collagen Study" into a whole-project report scope. |
| `INVESTIGATION_NAME_TO_ID` | unchanged: SEEK investigation titles. |

## 7. The entity agent's matching

### 7.1 Inputs

The question text, the LLM's `labs` and `scientists` lists, `config.LABS`, and, for rule M5, the sample type and
assay catalogs the config already holds (`MIN_SAMPLETYPES`, `MIN_ASSAYS`). The entity agent builds its own index from
`config.LABS`; it does not import the labs module. `config.LABS` that is anything but a list means unavailable (a
`MagicMock` config in a test included).

### 7.2 Normalisation

`fold(s)`: NFKC, then `’ ‘ ʼ` to `'`, then NFKD with combining marks dropped, then casefold, then whitespace collapsed.
Codes compare as written, in capitals. A record name matches as a whole token sequence, bounded by non-letters, so
multi-word and hyphenated surnames match only whole.

### 7.3 Rules

| Id | Rule | Where it looks | Case |
|---|---|---|---|
| **M1** code | Three ASCII letters equal to a record's `code`. In the question, only inside a lab phrase (`ASH lab`, `lab ASH`, `lab code ASH`). In an LLM entry, when the whole entry, less a lab word, is the code and the question carries that code as a word. Never read out of a UID: a UID names a sample, not a lab scope. | question, LLM entries | written in capitals |
| **M2** lab phrase | `<name>` with an optional `'s` or `s'`, then `lab`, `labs`, `laboratory` or `group`; or `lab`, `laboratory` or `group`, then `of`, an optional `Dr`/`Prof`/`Professor`, an optional first name or initial, then `<name>`. Scans the question even when the LLM missed it. | question, LLM entries | any case: the phrase disambiguates |
| **M3** possessive or honorific | `<name>'s` or `<name>s'` followed by a word (`Ashgrove's mice`), or `Dr`/`Prof`/`Professor` plus an optional first name, then `<name>`. | question | `<name>` capitalised as written |
| **M4** name in an LLM entry | The entry's surname position equals a record name: its last name-token sequence, or the part before the comma in `Last, First`. First names, initials and honorifics are ignored, because institution titles carry no first names. The name must also occur in the question as a whole word, so the LLM cannot introduce a lab the user never named. | LLM entries | any case, unless M5 |
| **M5** catalog word | A record name that occurs as a whole word in the sample type or assay catalogs' names, tags or descriptions is a common word in this domain. It matches only through M2, or when capitalised in the question. Decided from data the runtime already holds, so no hand-kept word list. | all name rules | as stated |
| **M6** shared surname | When a matched name belongs to more than one record, keep the records whose `affiliation` occurs in the question as a whole word. If none does, keep all of them, and mark each match `ambiguous: true`. | | |
| **M7** never | A bare surname in running text with no lab phrase, possessive, honorific or LLM entry. A code inside a UID. | | |

A name read after optional first names (M2's `of` form, M3's honorific) is the surname only as the last token of the
run, and M4's question occurrence counts only in that position too: in "Dr. Ashby Jones" the lab is Jones, never
Ashby. The next token on the same line continues the name when it is written like one (a capital, then lower case;
not an acronym or a lone initial), unless it is a lab word, a catalog word or an affiliation word that no record's
name starts with. In lower case nothing continues a name, so "the lab of dana ashgrove samples" still matches.

### 7.4 What happens to an LLM `labs` entry nothing matched

| Id | The entry | Goes to |
|---|---|---|
| U1 | three capitals, no record | `keywords` (a text search still finds UIDs carrying it) |
| U2 | a name or alias of a row in the projects catalog | dropped from `labs`; it is a project, which the LLM also lists in `projects` |
| U3 | names a lab but failed M5's case rule | `keywords`: it is most likely the common word |
| U4 | looks like a person's name: 1 to 4 name tokens, at least one capitalised in the question, none of `Center`, `Centre`, `Institute`, `Core`, `Facility`, `University`, `College`, `Hospital`, `School`, `Department`, `Program`, `Consortium` | `scientists`, spelled as in the question, lab word and possessive stripped; also appended to `keywords` (E4) |
| U5 | anything else | `keywords` |

An entry the LLM put in `scientists` stays there: the question said who handled the samples, and code does not
reclassify it as a lab.

**With no labs document** (`config.LABS` unavailable): `labs` passes through as the LLM wrote it, `lab_codes` and
`lab_matches` stay empty, nothing moves to `scientists`, and one warning is logged. "Not a lab" can only be decided
against a list.

### 7.5 What the entity agent emits (verbatim)

`schemas/entity.py` gains one model and two fields. Both fields are overwritten or merged after the LLM returns,
exactly like `lab_codes` today.

```python
class LabMatch(BaseModel):
    text: str                # what matched, as the question or the LLM wrote it
    code: str                # the lab's three-letter UID code
    name: str                # the lab head's surname as SEEK spells it
    affiliation: str | None = None
    project_ids: list[int] = Field(default_factory=list)
    rule: str                # "code" | "lab_phrase" | "possessive" | "honorific" | "name"
    ambiguous: bool = False  # M6 kept several records and nothing chose between them

class EntityAgentOutput(BaseModel):
    ...                      # every existing field, unchanged
    scientists: list[str] = Field(default_factory=list)
    lab_matches: list[LabMatch] = Field(default_factory=list)
```

For the question "RNA from the Fenwick lab, handled by Dana Example", with the invented records `FEN-Fenwick Lab
(MIT)` and `FEW-Fenwick Lab (Harvard)`, and the LLM's `labs: ["Fenwick", "Dana Example"]`:

```json
{
  "labs": ["Fenwick"],
  "lab_codes": ["FEN", "FEW"],
  "lab_matches": [
    {"text": "Fenwick lab", "code": "FEN", "name": "Fenwick", "affiliation": "MIT", "project_ids": [4], "rule": "lab_phrase", "ambiguous": true},
    {"text": "Fenwick lab", "code": "FEW", "name": "Fenwick", "affiliation": "Harvard", "project_ids": [], "rule": "lab_phrase", "ambiguous": true}
  ],
  "scientists": ["Dana Example"],
  "keywords": ["Dana Example"]
}
```

`labs` and `lab_codes` keep first-match order, de-duplicated; `lab_matches` holds one entry per record and text.

### 7.6 Worked cases (invented records: `ASH-Ashgrove Lab (BWH)`, `MAR-Marrow Lab (BWH)`, the two Fenwick labs; `Marrow` appears in the sample type catalog)

| Question | LLM `labs` | Result |
|---|---|---|
| RNA from the Ashgrove lab | `Ashgrove` | ASH, `lab_phrase` |
| samples from Jane Ashgrove | `Jane Ashgrove` | ASH, `name` |
| Ashgrove, J. samples | `Ashgrove, J.` | ASH, `name` |
| Ashgrove's mice | (none) | ASH, `possessive`, from the question scan |
| ASH lab samples | `ASH` | ASH, `code` |
| bone marrow samples | (none) | nothing (M7) |
| samples from the Marrow lab | `Marrow` | MAR, `lab_phrase` |
| marrow samples, with the LLM wrongly listing `marrow` | `marrow` | nothing matched; `marrow` goes to `keywords` (U3) |
| the Fenwick lab at Harvard | `Fenwick` | FEW only (M6) |
| samples handled by Dana Example | `Dana Example` | `scientists` and `keywords` (U4) |
| the Oakley lab, whose title SEEK spells with an en dash | `Oakley` | `scientists` (U4); the title is in the labs report |
| XYZ lab, a code with no institution | `XYZ` | `keywords` (U1) |

### 7.7 `helpers/query_scope.py`

A `lab_codes` consumer, and the one place the reply is told which constraints the query dropped.

- `_asked_for` adds each `scientists` entry as `scientist <name>`, and skips a keyword that equals a scientist once
  folded, so the name is not counted twice.
- A scientist counts as applied when the full name or its last token occurs in the executed query text. The module
  must under-report a gap, never invent one, and a graph query may match the surname alone.
- A lab constraint's label names the lab from `lab_matches` (`lab ASH (Ashgrove)`).
- `_is_applied` is left alone: making it see graph type labels is a fix already queued elsewhere, so this unit edits
  `_asked_for` only.

### 7.8 No other way in (added at integration)

The entity agent is not the only writer of lab codes. The parser LLM writes `filters.lab_codes` and echoes the entity
result into `resolved`, and the summary reporter used to fall back to the plan's codes when the entity agent's list was
empty. So the orchestrator clamps the parser's plan to the entity agent's `lab_codes` straight after the parser (in
plan mode, every candidate too), and `run_reporter_summary` falls back to the plan's codes only when its caller passed
none: an empty list is the entity agent's answer. `helpers/lab_code.py::clamp_lab_codes` is the one clamp.

## 8. `pi`, `parse_pi` and `pi_names`

- **`pi` stays**, as curated display prose. The project page shows it (`context_catalog.load_project_context`) and the
  entity LLM reads it as context in the catalog. Nothing parses it.
- **`parse_pi`, `with_pi_names` and the `pi_names` column are retired**: removed from `scripts/context_gen.py`
  (`TABLES`, `ADDED_COLUMNS`, `json_columns`, `DDL`, `rows_for`), from the held seed
  `startup/seed/sql/projects_context.curated.sql`, and from their tests.
  1. Institutions are now the source of lab codes and lab heads' surnames (OD1). A second source parsed from free text
     would disagree with it, and the verdicts measured that it did: codes derived from first names landing on another
     project's real lab, and member labs parsed as PIs.
  2. Nothing reads it: `map_project` never carried it.
  3. No database has the column, because 6.16 never ran. Retiring it now costs no migration; after 6.16 it would.
- A curated `pi_names` key is then refused as an unknown column by `check_columns`, with no special case.
- `context/README.md` loses its "must NOT carry `pi_names`" sentence and says instead that `pi` is display prose and lab
  codes come from SEEK.

## 9. Investigation rows

### 9.1 The key collision, and the composite key

6.15c names the real CSBC and MetNet investigations by their exact SEEK titles, `CSBC` and `MetNet`. The project rows
are named `CSBC` and `MetNet`. `projects_context` is keyed on `name` alone: `PRIMARY KEY (name)` on the live table, a
unique key on `name` in the generator's DDL, and every refusal and dedupe in `context_gen.py` keys on it. So the
rows 6.15c asks for cannot coexist with the project rows.

**The key becomes `(name, entity_type)`**, everywhere the generator keys projects:

- `DDL["projects"]`: `entity_type VARCHAR(64) NOT NULL`, and `UNIQUE KEY uq_projects_context_name_type (name,
  entity_type)` in place of the unique key on `name`.
- The update script's schema section, before the rows transaction, each step conditional on the shape found:
  - a table whose primary key is exactly `(name)` (the live shape): `DROP PRIMARY KEY, ADD PRIMARY KEY (name,
    entity_type)`. It only relaxes uniqueness, so it cannot fail on existing rows, and it removes none.
  - a table carrying `uq_projects_context_name`: drop it.
  - the keys section, after the commit, adds `uq_projects_context_name_type` where the table has an `id` primary key.
- The delete of rows the source no longer names, the duplicate collapse, `_checked_keys`'s fold collision refusal and
  the verification checks all key on the pair.
- `entity_type` must be exactly `project` or `investigation`; anything else is refused.

This changes the live table's primary key. The xlsx sign-off for 6.16 must show it, and production's own
`SHOW CREATE TABLE projects_context` must be captured before 6.16 runs (the verdicts note it never was).

### 9.2 Conventions for an investigation row

| Key | Rule |
|---|---|
| `entity_type` | `investigation` |
| `name` | the exact SEEK investigation title that holds the samples, byte for byte, never a paper-tracking copy's |
| `project_id` | the owning project's SEEK id, which equals the parent project row's `project_id`; `null` only when that id differs by instance, which requires `present_on` |
| `parent_project` | the owning project row's `name`; with no such row, the owning SEEK project's title |
| `alternative_names` | what users type for it (section 9.4) |
| `present_on` | generator-only (section 9.5) |
| `research_focus` | required, one line, at most 200 characters, no count (it becomes the bullet's description) |
| `description`, `tags` | curated prose; keep them short, since every row reaches the entity agent on every turn |
| `pi`, `key_data_types`, links | `null` / `[]` |

### 9.3 The rows

Descriptions and aliases are content for the xlsx review; the keys below are the design.

| `name` | `project_id` | `parent_project` | `present_on` | Proposed `alternative_names` |
|---|---|---|---|---|
| `BioMicroCenter` | 5 | `MIT-Koch` | every | `BioMicro Center` |
| `CSBC` | 10 | `CSBC` | every | (none) |
| `Collagen Study` | 11 | `Shoulders` | every | `Shoulders` |
| `Endometriosis` | 7 | `Griffith` | every | `Griffith`, `CGR-Endo` |
| `GBM_BTC` | 9 | `Break Through Cancer` | every | `BTC-GBM` |
| `Impactb Investigation` | 2 | `Impact` | every | `Impact`, `IMPACT`, `IMPAcTb` |
| `MIT_SRP` | 3 | `SRP` | every | `SRP`, `MIT SRP`, `Superfund` |
| `MetNet` | 4 | `MetNet` | every | (none) |
| `TCGA` | `null` | `TCGA` | `local`, `dev` | `The Cancer Genome Atlas` |

- **BioMicroCenter** is not in the plan's list. It is one of production's investigations, the NS parser prompt already
  names it, and on a full enumeration the unlisted-investigation refusal would reject a block that omits it. If the
  counts show it holds no samples, the zero-sample refusal drops it, which is the refusal doing its job.
- **RMS-NGC** (an investigation of project RMS-NGC) and the Training/Test project's test investigation are not added.
  If either holds samples, the unlisted refusal names it at count time, and the operator adds a row or passes
  `--ignore-investigation`.
- **TCGA** belongs to a project titled TCGA that exists only on the local and dev instances, with a different id on
  each, so its `project_id` is `null` and its `parent_project` is that title. There is no TCGA project row.
- The MUS row's `CC` tag stays (6.15b). This work touches no tags.

### 9.4 Aliases

- The five investigation titles now carried by their own rows leave the project rows' `alternative_names`:
  `Impactb Investigation` from Impact, `GBM_BTC` from Break Through Cancer, `MIT_SRP` from SRP, `Endometriosis` from
  Griffith, `Collagen Study` from Shoulders. An exact investigation title then resolves to one row. For the report
  path this means `Endometriosis` resolves through `INVESTIGATION_NAME_TO_ID` to the investigation, not to the whole
  project.
- The generator refuses an alias that, once folded, equals another row's name or alias, with one exception: an
  investigation row may repeat its own parent project's name or aliases. That is what bridges "Impact" to
  `Impactb Investigation`.

### 9.5 `present_on`, the availability field

- A curated key of `context/projects.json` that the generator reads and never writes to the database. It is the one
  documented exception to "keys are the database column names", the mirror image of the retired `pi_names`:
  `check_columns` accepts it for projects, and `rows_for` strips it before any SQL is rendered.
- Absent or `null`: every instance. Otherwise a non-empty list drawn from the instance profiles `local`, `dev` and
  `prod` (the `--ci-profile` vocabulary), without duplicates and not all three. Allowed only on investigation rows.
- It is generator-only because its runtime readers are served by the generated block: the system agent and the CC
  agent read `capabilities.md`, and the graph agent's live catalog already lists only the investigations the local
  graph holds. Adding a column would be a second schema change inside the gated write, with no reader that needs it.
- **Wording**, appended to the bullet: `(not on every instance: loaded on local and dev only)`, profiles in the fixed
  order local, dev, prod. When any bullet carries it, the block's closing paragraph adds: `A name marked "not on every
  instance" is loaded only on the instances it lists. Where a query scoped to it finds no samples, it is not loaded
  on this instance: say so rather than reporting zero.`

## 10. The generated investigation block

### 10.1 Marker placement

In `NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md`, section `## Known Projects and Investigations`:

- `<!-- BEGIN CONTEXT-GEN:investigations -->` on its own line, directly after the blank line under the heading and
  before the intro sentence ("The graph database organizes samples...").
- `<!-- END CONTEXT-GEN:investigations -->` on its own line, directly after the outro line ("Use these names exactly
  ..."), before the blank line that precedes `---`.

The markers wrap the intro, the bullets and the outro, which the generator writes. Nothing that ends drift's section
(`---` or a heading) sits between the heading and BEGIN or inside the pair; `check_capabilities_markers` already
refuses every other placement. Committed alone, the markers change nothing drift reads. Every other placement was
measured by the capabilities-block verdict to duplicate, strand or delete text.

### 10.2 What the block renders

Rows sorted by `name`. The separator is a colon, not a dash, to match the house prose rule:

```
<!-- BEGIN CONTEXT-GEN:investigations -->

The graph database organizes samples into studies grouped under named investigations. The investigations that hold samples are:

- **Impactb Investigation**: <research_focus> [also: Impact, IMPACT, IMPAcTb]
- **TCGA**: <research_focus> [also: The Cancer Genome Atlas] (not on every instance: loaded on local and dev only)

Use these names exactly when asking graph questions scoped to one investigation. The names in brackets are what people call them; the bold name is what the graph answers to. A name marked "not on every instance" is loaded only on the instances it lists. Where a query scoped to it finds no samples, it is not loaded on this instance: say so rather than reporting zero.

<!-- END CONTEXT-GEN:investigations -->
```

`render_capabilities_block` splits in two: `render_capabilities_text(rows)`, pure and graph-free, with every row
check (names, aliases, `present_on`, `research_focus`, baked counts, duplicate names, surrounding whitespace, the
marker phrase appearing in curated text), and `check_investigation_counts(rows, counts_docs)`, the refusals that
need a measurement. `--emit capabilities` runs both. Counts only refuse: they never change the text.

### 10.3 The refusal, and where the counts come from

**The counts file (verbatim shape):**

```json
{
  "measured_on": "local",
  "measured_at": "2026-09-19T06:10:00Z",
  "investigations": {
    "TCGA": {"nodes": 1, "samples": 1000},
    "Impact": {"nodes": 1, "samples": 0}
  }
}
```

- `investigations` enumerates every `Investigation.title` in the graph, grouped by title, which is how drift already
  counts. The real CSBC and its paper copy share a title and count as one entry. A listed name the graph lacks is
  absent from the file.
- It is produced by a new read-only mode, `manage.py graph_sync --investigation-counts --instance <local|dev|prod>
  --json`, backed by `drift.measure_investigations(driver, db)` in a READ transaction. `--instance` is required, with
  no default. Running it against a live graph is the operator's step.
- `--emit capabilities` takes `--counts` once or more, one file per instance, and every file must pass. The flat
  `{title: count}` shape and drift's stat are no longer accepted: neither says where it was measured, and neither can
  tell an absent investigation from an empty one.
- `--ignore-investigation TITLE` (repeatable) excludes a title from the unlisted check.

**The rules, per counts file, for each investigation row:**

| Row | Measured on an instance in `present_on` (or `present_on` is every instance) | Measured on an instance not in `present_on` |
|---|---|---|
| refused when | `samples` is 0 or the title is absent (`ZeroSampleInvestigation`) | the title is present with `samples` 0 (an empty node); or it holds samples, because then `present_on` is wrong |
| accepted when | `samples` > 0 | the title is absent |

Plus: `measured_on` must be one of the three profiles; two files may not share one; every title with `samples` > 0
that no investigation row names, and no `--ignore-investigation` covers, is refused (`UnlistedInvestigation`).

This is how "names not present on every instance" coexist with a refusal decided on one instance's graph. A block
measured on the local instance, which holds production's investigations plus TCGA, clears TCGA because local is in
its `present_on`, and clears the everywhere names because local holds them. What a single local measurement cannot
prove is that production still holds them. Section 15 keeps that risk open, and a second counts file measured on dev
narrows it.

### 10.4 `drift.py`

- `assistant_investigation_entries(text) -> list[tuple[str, bool]]`: the names under the section, each with whether it
  is on every instance (false when its bullet carries `(not on every instance:`).
  `assistant_investigation_names` stays, returning the names, for its existing callers.
- `ASSISTANT_INVESTIGATIONS` also returns `count(DISTINCT i) AS nodes`.
- `_check_assistant_investigations`: an everywhere name fails when `samples` is 0, as today. A name not on every
  instance fails only when `nodes > 0` and `samples == 0`. That is the confident-zero case. An absent one passes and is
  listed under `stats["assistant_investigations"]["absent_here"]`. A fresh local install without the TCGA merge passes;
  an instance that holds an empty TCGA fails.
- The generator's marker phrase and drift's must be the same string. The test that already imports both asserts it,
  as `DRIFT_SECTION_HEADING` is tied today.

### 10.5 The commit-time gate

`ci/gate/test_context_capabilities_markers.py` (blocking, graph-free, standard library only) gains:

- once the markers exist, the text between them equals `render_capabilities_text(rows)` for the curated rows, byte
  for byte. A hand edit between the markers, or a new row without a regeneration, fails the gate.
- the names the block lists, parsed by the generator's mirror of drift's regex, equal the investigation rows' names.

`nextseek_api/tests/test_graph_sync_drift.py` (blocking, Django lane) asserts that drift's real parser reads exactly
the investigation rows' names from the committed file, with TCGA marked as not everywhere.

### 10.6 How the committed block gets written

The build unit may not read a live graph, so it cannot produce counts. It writes the block once with
`render_capabilities_text` (a one-off call; no CLI flag skips the counts), and the gate pins it to the rows. **Before
the branch merges**, the operator measures (`graph_sync --investigation-counts --instance local --json`) and runs
`python scripts/context_gen.py --emit capabilities --counts <file>`. The file must come out byte-identical. A refusal
means a row to fix, not a flag to add. After the rebuild, drift is the runtime backstop on local and dev.

### 10.7 The investigation names outside the block

The prose of `capabilities.md` still calls dead names investigations. Example queries are edited by hand, since only
the list is generated:

- the `Investigation` entry's examples (`"Griffith", "Impact", "GBM"`) become real titles (`"Impactb Investigation"`,
  `"MIT_SRP"`, `"GBM_BTC"`).
- "the SRP investigation" becomes "the MIT_SRP investigation", and "Explain the GBM investigation." becomes
  "Explain the GBM_BTC investigation." "the GBM project" becomes "the Break Through Cancer project".
- The tip that lists `CSBC, GBM, Griffith, Impact, MetNet, SRP, Shoulders` points at the generated list instead, with
  the example "What samples are in the GBM_BTC investigation?".
- "the GBM study" stays: GBM is a real study inside GBM_BTC.

These edits touch example queries only, never an H3 heading of "What You Can Ask" or a bold lead of "What the System
Cannot Do", so the NS projection and `route_capabilities.json` stay byte-identical; `gen_op_surfaces --check` proves it.

The CC plugin's `MANIFEST.md` row for `capabilities.md` stops listing names (its list is five dead names) and points at
the generated list; its `projects_db.json` row says rows carry `entity_type` and project rows carry `labs`.

Hand-kept lists this work may not touch go to the prompt chat (section 13): the NS parser prompt and its v2 copy, the
two NS `min_graph_schema.json` copies, and the CC plugin's `min_graph_schema.json`. `route_capabilities.json`'s
"Impact investigation" example comes from the corpus, which phase 14a owns. The graph agent's live vocabulary lists
every `Investigation.title` with no sample filter, paper copies included; that is `graph_catalog.py`, which phase 9
owns (P7a, P7b).

## 11. Every consumer of `projects_context` (6.15d)

Nothing in the table below changes behaviour until a database holds an investigation row, and none will until 6.16,
which follows the merge of all three units and the xlsx sign-off.

| Consumer | Reads | Filter it needs | Unit |
|---|---|---|---|
| `config.py::_fetch_context_files_from_db` | `SELECT * FROM dmac.projects_context` into `projects_db.json` | none: every row, `entity_type` carried; `labs` injected on project rows only | labs |
| `config.py` `FULL_PROJECTS_MAP` | `projects_db.json` rows | project rows only | labs |
| `config.py` `FULL_INVESTIGATIONS_MAP` (new) | same | investigation rows only | labs |
| `config.py::_merge_project_name_to_id` into `PROJECT_NAME_TO_ID` | names and aliases, to `project_id` | project rows only | labs |
| `config.MIN_PROJECTS`, the entity agent's PROJECTS CATALOG (`agents/entity.py`) | every row, every turn | none, deliberately: it must see investigations to resolve "Impact" to `Impactb Investigation`; each row says its type | none |
| `agents/system.py` ENTITY_DETAILS | `FULL_PROJECTS_MAP[name]` | project row under its `name`, and investigation row from `FULL_INVESTIGATIONS_MAP` under `"<name> (investigation)"`; a non-dict map attribute (a `MagicMock` in tests) reads as empty | investigations |
| `helpers/dates.py::_normalize_project_id`, `reports/runners.py::_resolve_report_scope` | `PROJECT_NAME_TO_ID`, then `INVESTIGATION_NAME_TO_ID` | inherited from the merge filter; no change | none |
| `nextseek_api/services/context_catalog.py::_project_context_row` (the project page, `seek/views/projects.py`) | `WHERE project_id = %s LIMIT 1` | `AND (entity_type = 'project' OR entity_type IS NULL) ORDER BY name`: an investigation row shares its owner's `project_id` and could otherwise render as the project's header | investigations |
| `scripts/context_gen.py` | writes the table | the composite key (section 9.1) | investigations |
| `nextseek_api/graph_sync/drift.py` | the block generated from the rows | section 10.4 | investigations |
| cc-agent image, `/app/plugins/nextseek/context/projects_db.json` | the committed one-row fallback | none now: this work does not regenerate the committed file; `MANIFEST.md` states that rows carry `entity_type` | investigations (`MANIFEST.md`) |
| `graph_context.py` | the live graph's titles, not this table | none | none |
| `startup/steps/schema_fixups.py` with `startup/seed/sql/projects_context.sql` | install's seed | none: install keeps loading what `origin/dev-graph` loads (OD6) | none |
| `startup/seed/sql/projects_context.curated.sql` | the held seed, loaded by nothing | regenerated with the composite key and the nine rows | investigations |
| `CONTEXT_FILES/tools/context_to_xlsx.py`, `validate_context.py` (outside the repository) | `context/projects.json` | the workbook keys rows on `name` alone and would pair a project with its same-named investigation; the validator requires `entity_type == "project"` and a unique, SEEK-known `project_id` on every row. Both need the pair key and the section 9.2 rules before the sign-off | the operator |

## 12. The three units

File-disjoint: no file appears under two units. Where a unit needs another unit's file, the need is stated here and
the owner builds it. Each unit reads `getattr` with a safe default for the attributes the others add, so each is green
on its own branch.

### 12.1 `labs`

**Owns:** `NessieAI/chat_nextseek/src/chat_nextseek/labs.py` (new),
`NessieAI/chat_nextseek/src/chat_nextseek/config.py`, `NessieAI/chat_nextseek/src/chat_nextseek/context/.gitignore`,
`.dockerignore`, `NessieAI/tests/cc/test_cc_context_drift_guard.py`, `NessieAI/tests/chat_nextseek/test_labs.py`
(new), `NessieAI/tests/chat_nextseek/test_config_labs_export.py` (new),
`NessieAI/tests/chat_nextseek/test_config_two_maps.py`, `NessieAI/chat_nextseek/CLAUDE.md`,
`NessieAI/chat_nextseek/README.md`.

**Builds:** sections 4, 5 and 6: the fixed SQL and its read-only transaction, `fetch_institution_rows`, the grammar and
its reason codes, the conflicts, the labs document and its atomic write, `labs_by_project`, the `--report` entry
point, the `labs` key in `map_project`, `LABS` and `LABS_STATUS`, the project-only `FULL_PROJECTS_MAP` and merge, and
`FULL_INVESTIGATIONS_MAP`. The two docs record that the export now reads SEEK institutions, and that `labs_db.json` is
runtime-only.

**Acceptance:** fixtures are invented titles in the grammar's shape. Tests cover every reason code; both conflicts; a
fake connection proving the read runs inside `start_transaction(readonly=True)` and that the SQL is the one constant;
fetch failure falling back to the previous file, then to `[]`; `map_project` still reading every column through
`lower.get` (so `test_every_column_is_one_the_runtime_actually_reads` passes unchanged on this branch); investigation
rows in the export getting no `labs`; the merge ignoring investigation rows; the drift guard ignoring
`labs_db.json`. Then both lanes, with no new failure.

### 12.2 `entity`

**Owns:** `NessieAI/chat_nextseek/src/chat_nextseek/agents/entity.py`,
`NessieAI/chat_nextseek/src/chat_nextseek/helpers/lab_code.py` (rewritten as the matcher; `lab_code()` removed),
`NessieAI/chat_nextseek/src/chat_nextseek/schemas/entity.py`,
`NessieAI/chat_nextseek/src/chat_nextseek/helpers/query_scope.py`, `NessieAI/tests/chat_nextseek/test_lab_code.py`,
`NessieAI/tests/chat_nextseek/test_entity_labs.py`, `NessieAI/tests/chat_nextseek/test_query_scope.py`.

**Builds:** section 7. **Needs from `labs`:** `ChatConfig.LABS` as specified in 6.5; it reads `getattr(config, "LABS",
None)` and treats anything but a list as unavailable, so its tests set `LABS` directly and never import `labs.py`.

**Acceptance:** one test per rule M1 to M7 and U1 to U5, every case in 7.6, the unavailable pass-through, the two new
fields surviving `model_dump` (the CC hook injects the dump), and `query_scope` reporting a dropped scientist while not
double-counting the keyword. The existing tests that build `EntityAgentOutput` (`test_chatter_prompt`,
`test_graph_refine`, `test_graph_turn_retry_loop`, `test_orchestrator_pipeline_dispatch`, `test_graph_agent_context`)
must stay green untouched. Then both lanes.

### 12.3 `investigations`

**Owns:** `scripts/context_gen.py`, `scripts/README.md`, `context/projects.json`, `context/README.md`,
`startup/seed/sql/projects_context.curated.sql`, `NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md`,
`NessieAI/docker/cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md`, `nextseek_api/graph_sync/drift.py`,
`nextseek_api/management/commands/graph_sync.py`, `nextseek_api/services/context_catalog.py`,
`NessieAI/chat_nextseek/src/chat_nextseek/agents/system.py`, `NessieAI/tests/api/test_context_gen.py`,
`NessieAI/tests/api/test_context_gen_mysql.py`, `ci/gate/test_context_capabilities_markers.py`,
`nextseek_api/tests/test_graph_sync_drift.py`, `nextseek_api/tests/test_graph_sync_command.py`,
`nextseek_api/tests/test_context_catalog.py`, `seek/tests/test_context_seed_tables.py`,
`NessieAI/tests/chat_nextseek/test_system_agent_investigations.py` (new).

**Builds, in this order** (6.15d before 6.15c): the consumer filters (`context_catalog.py`, `system.py`); the generator
(composite key, `present_on`, the `pi_names` retirement, the alias rule, the investigation-row checks, the text and
counts split, the new counts shape, the colon separator); `drift.py` and the `--investigation-counts` mode; the nine
rows and the alias removals in `context/projects.json`; `context/README.md`'s conventions; the regenerated held seed;
the markers, then the generated block, then the prose edits in `capabilities.md`; `MANIFEST.md`.

**Needs from `labs`:** `FULL_INVESTIGATIONS_MAP` (read with `getattr` and a `{}` default), and the project-only
`FULL_PROJECTS_MAP` and merge (section 6.5).

**Acceptance:** the three tests that pin today's dead names are inverted in the same commit that places the markers.
The gate's equality check (10.5) lands with the generated block, one commit later, so the marker commit passes on its
own.
Tests cover the composite key on both table shapes in the MySQL lane (the live shape, with no `id` and `PRIMARY KEY
(name)`; and the generator's shape), a second run changing nothing, and a same-named project and investigation
coexisting; every refusal of 10.3; drift's absent-versus-empty rule; the gate of 10.5; the project page ignoring an
investigation row; and `gen_op_surfaces --check` and `python3 ci/docs_map.py` clean. Then both lanes.

### 12.4 The one contract that crosses units at the source level

`test_every_column_is_one_the_runtime_actually_reads` (investigations' file) parses `map_project` (labs' file). It stays
consistent on each branch and after the merge because:

- `labs` adds `labs` through the enclosing scope, not through `lower.get`, and reads no new column.
- `investigations` retires `pi_names` from the declared columns and makes `present_on` generator-only.

So `unread["projects"]` becomes the empty set on the `investigations` branch and stays correct after the merge.

## 13. What the prompt chat needs to change

None of these files is edited by this work.

1. **`prompts/entity_agent.txt`** (and any variant copy), for 13c.1: `labs` may hold the name as the user wrote it
   (surname, full name, "X lab" or a three-letter code), because code resolves it against SEEK's labs. Project rows now
   carry `labs: [{code, name, affiliation}]`. A person named as who made, collected or handled samples, or any person
   the question does not frame as a lab or PI, goes in the new `scientists` field. Leave `lab_codes` and `lab_matches`
   empty. Rows with `entity_type: "investigation"` are investigations: emit their exact `name` in `projects` when the
   user names one or its alias. A project and an investigation may share a name (CSBC, MetNet).
2. **`prompts/parser_core_routing.txt` and `prompts/variants/v2/parser_core_routing.txt`**, for 13c.2: the "Known
   investigation titles" line gains TCGA with the not-on-every-instance note, and keeps BioMicroCenter only if the
   counts keep it. `ENTITY_RESULT.scientists` becomes a `Scientist` predicate. `lab_codes` now come only from labs
   SEEK knows: drop the surname-to-code example (`e.g. <surname> -> <code>`, in the lab-scope rule and in the
   filters section) and say `filters.lab_codes` is a copy of `ENTITY_RESULT.lab_codes`, never derived from a name. Code drops
   any other code since 7.8, so the example now only misleads. A lab does not imply a project scope. An option is to stop hand-listing and have config inject the
   generated block through a placeholder; that is a code change the prompt chat would request.
3. **`context/min_graph_schema.json`** (NS) and **`prompts/variants/v2/min_graph_schema.json`**: the same investigation
   list change.
4. **`prompts/variants/v2/graph_agent.txt`**, for 13c.3: read `resolved.scientists` for the `Scientist` predicate. It
   reads `resolved.keywords` today, which keeps working because of E4. `resolved.lab_matches` names the lab for the
   explanation. A scoped query that finds nothing on a name marked not on every instance means the investigation is not
   loaded there.
5. **`prompts/system_agent.txt`**: ENTITY_DETAILS may carry `"<name> (investigation)"` entries, project rows carry
   `labs`, and the capabilities block carries the availability note.
6. **The CC plugin's `min_graph_schema.json`**: its investigation titles line (phase 12 owns its drift).
7. **6.14**, the `/people/` example intent in `min_api_endpoints_enriched.json`, is unchanged and still theirs.
8. **The 13c workflow (`phase13c-person-names`)** will stop at its Check, which requires `pi_names`. It should check for
   `ChatConfig.LABS`, `EntityAgentOutput.scientists` and `lab_matches` instead. Its entity-resolution unit's code side
   is this spec's `entity` unit: only the prompt side remains. Its brief's "PI means a project scope" becomes "a lab
   head means the lab's UID code; a project scope only when a project is named".

## 14. Order, and what stays gated

1. The three units build in parallel on branches cut from this spec's commit, and merge back into
   `feat/projects-labs-context`. Both test lanes run on the merged tree.
2. The operator runs `python -m chat_nextseek.labs --report` on the local stack (the first live SEEK read), then
   `graph_sync --investigation-counts --instance local --json`, then `--emit capabilities --counts`, which must leave
   `capabilities.md` unchanged.
3. The operator's xlsx review covers the nine rows, the five alias removals, the primary key change and the deletions
   the generator already emits. Its two tools need the pair key first (section 11).
4. 6.16 writes the local database, still gated. Then the app and cc-agent rebuilds (`capabilities.md` and
   `MANIFEST.md` reach the CC agent only through the second), then drift on local.
5. The whole branch, with `wf/p6`, merges into `origin/dev-graph` at the end.

Install and reset are untouched: they keep loading `startup/seed/sql/projects_context.sql`, and the curated seed stays
held.

## 15. Risks this spec does not close

1. **A single-instance measurement.** The block ships to every instance, and drift does not run on production. A
   local measurement cannot prove production still holds an everywhere name. A second counts file measured on dev
   narrows the gap; measuring production is the operator's call.
2. **The committed block is count-checked only by the operator's run** (10.6). The gate pins text to rows, not rows to
   the graph.
3. **The primary key change on the live table** is part of the gated write, and production's `SHOW CREATE TABLE` was
   never captured.
4. **A lab head recorded as someone's `Scientist`.** "Samples handled by <a lab head's full name>" resolves to the lab
   when the LLM puts the name in `labs`, and so does a non-head who shares a lab head's surname. Code resolves; the
   prompt decides the field.
5. **An unparsed title hides a lab** until SEEK's title is fixed: its surname then becomes a `Scientist` value. The
   report names it every refresh.
6. **Freshness is bounded by process starts** (4.4), not by the calendar.
7. **Real lab data in a checkout.** A config run against a checkout rewrites the tracked `projects_db.json` with labs
   injected, as it already does with project content. `labs_db.json` is gitignored; `projects_db.json` cannot be. The
   existing landmine in `NessieAI/chat_nextseek/CLAUDE.md` applies.
8. **`seek_production` is spelled as a literal**, as the existing loaders spell it. An instance whose SEEK schema is
   named otherwise gets no labs, and says so in the log.
9. **Token cost.** Nine investigation rows and a `labs` list per project reach the entity agent on every turn after
   6.16. Keep investigation rows' prose short.
10. **Merge overlap in `helpers/query_scope.py`.** Another chat is fixing `_is_applied`; this work edits `_asked_for`
    only.
11. **`INVESTIGATION_NAME_TO_ID` keeps the first id per title with no `ORDER BY`**, so a real investigation and its
    paper copy resolve by row order. This predates this work; the report path resolves projects first, which hides it
    for every current name.
12. **The corpus and the answer key** may hold lab codes derived by the retired first-three-letters rule; phase 14a
    re-derives them.
13. **Only the capabilities gate and the drift tests block CI.** The labs and entity tests run in the informational
    lane, like every other `chat_nextseek` test, unless the operator adds them to `ci/blocking_lanes.py`.
