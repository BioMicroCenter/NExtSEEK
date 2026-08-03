# NExtSEEK UI Reference

Quick reference for all UI pages, their routes, views, and templates.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
├─────────────────────────────┬───────────────────────────────┤
│  Django Templates           │  React Chat App               │
│  (themes/NextSeek/)         │  (chat_frontend/)             │
│  - Bootstrap 5              │  - Vite + TypeScript          │
│  - jQuery EasyUI            │  - Tailwind + shadcn/ui       │
│  - Server-rendered          │  - localhost:5173             │
└─────────────────────────────┴───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Django Backend                           │
├───────────────┬─────────────────┬───────────────────────────┤
│  seek/        │  nextseek_api/  │  api_app/                 │
│  Main app     │  Chat/AI API    │  Legacy API               │
│  views.py     │  assistant/     │  file submission          │
└───────────────┴─────────────────┴───────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Databases                               │
├─────────────────────────────┬───────────────────────────────┤
│  MySQL (SEEK + NExtSEEK)    │  Neo4j (Graph)                │
└─────────────────────────────┴───────────────────────────────┘
```

---

## Chat Frontend (React)

Separate React app for AI chat assistant interface.

**Location**: `chat_frontend/`

**Stack**: React 19 + Vite + TypeScript + Tailwind CSS + shadcn/ui

**Dev Server**: `http://localhost:5173`

**API**: Talks to `/nextseek_api/` endpoints

### Structure
```
chat_frontend/src/
├── App.tsx                    # Main app entry
├── AppLayout.tsx              # Layout wrapper
├── EmbeddedApp.tsx            # Embeddable version for Django pages
├── main.tsx                   # Standalone entry
├── main.embedded.tsx          # Embedded entry
├── index.css                  # Main styles
├── components/
│   ├── ChatPanel/             # Chat interface components
│   ├── DebugPanel/            # Debug/testing tools
│   ├── Layout/                # Layout components
│   ├── TestRunner/            # Test utilities
│   └── ui/                    # shadcn/ui components (buttons, dialogs, etc.)
├── hooks/                     # React hooks
├── lib/                       # Utilities
└── test/                      # Tests
```

### Commands
```bash
cd chat_frontend
npm install          # Install deps
npm run dev          # Dev server (localhost:5173)
npm run build        # Production build
npm run build:embedded  # Build for embedding in Django
npm test             # Run tests
npm run test:e2e     # Playwright E2E tests
```

### Key Components
| Component | Path | Purpose |
|-----------|------|---------|
| ChatPanel | `src/components/ChatPanel/` | Main chat interface |
| DebugPanel | `src/components/DebugPanel/` | Debug tools |
| UI Components | `src/components/ui/` | Reusable shadcn components |

---

## Django Theme (NextSeek)

## Theme Structure

```
themes/NextSeek/
├── templates/
│   ├── base.html              # Master layout (sidebar, header, footer)
│   ├── header.embed.html      # Top header with search
│   ├── nav.embed.html         # Sidebar navigation
│   ├── page-footer.embed.html # Footer
│   ├── index.html             # Home page wrapper
│   └── content.embed.html     # Home page content
└── static/
    ├── css/nextseek.css       # Main theme CSS
    └── js/nextseek.js         # Sidebar toggle, nav state
```

All pages extend `base.html` and inject content via `{% block main %}`.

---

## Pages by Category

### Home

| Page | URL | View | Template |
|------|-----|------|----------|
| Home/Dashboard | `/` | `direct_to_template` | `themes/NextSeek/templates/index.html` → `content.embed.html` |

---

### Data Entry

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Batch Upload (Assay Sheets) | `/seek/samples/upload/` | `views.batchUpload` | `seek/templates/batchUpload.html` | `pages/batch_upload.embed.html` |
| Data/Protocol File Upload | `/seek/data/upload/` | `views.datafileUpload` | `seek/templates/dataFileUpload.html` | `pages/datafile_upload.embed.html` |
| Download Templates | `/seek/templates/` | `views.templatesList` | `seek/templates/templatesList.html` | - |

---

### Data Query / Search

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Sample Search (main) | `/seek/search/` | `views.searchAdvanced` | `seek/templates/searchAdvanced.html` | `pages/searchAdvanced_*.embed.html` |
| Sample Search (legacy) | `/seek/samples/search/` | `views.sampleSearch` | `seek/templates/sampleSearch.html` | `pages/samples_search.embed.html` |
| New Search | `/seek/newsearch/` | `views.newSearch` | `seek/templates/newSearch.html` | `pages/samples_newsearch.embed.html` |
| Data File Query | `/seek/datafile/query/` | `views.datafileQuery` | `seek/templates/dataFilesPage.html` | `pages/datafile_table.embed.html` |
| Protocol (SOP) Query | `/seek/sop/query/` | `views.sopQuery` | `seek/templates/sopsPage.html` | `pages/sops_table.embed.html` |
| Smart Query (AI) | `/seek/assistant/` | `views.smartSearch` | `seek/templates/smartSearch.html` | - |

---

### Sample Views

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Sample by ID | `/seek/sample/id=<id>/` | `views.sample` | `seek/templates/samples.html` | `pages/samples.embed.html` |
| Sample Tree by UID | `/seek/sampletree/uid=<uid>/` | `views.sampleTree` | `seek/templates/samples.html` | `pages/samples_tree.embed.html` |
| Sample Timeline (NHP) | `/seek/sample_timeline/` | `TemplateView` | `seek/templates/sample_timeline.html` | - |
| NHP Info | `/seek/nhpinfo/<name>/` | `views.nhp_info` | (JSON/template) | - |

---

### Projects

| Page | URL | View | Template |
|------|-----|------|----------|
| Projects List | `/seek/projects/` | `views.projects` | `seek/templates/projectsList.html` |
| Project Detail | `/seek/projects/<id>/` | `views.project_page` | `seek/templates/projectPage.html` |

---

### Admin

| Page | URL | View | Template |
|------|-----|------|----------|
| Django Admin | `/admin/` | Django admin | (Django built-in) |
| Clades Management | `/seek/admin/clades/` | `views.adminClades` | `seek/templates/clades.html` |
| Sample Attributes | `/seek/samples/attributes/` | `views.sampleAttributes` | `seek/templates/sampleAttributes.html` |
| Admin Sample Retrieval | `/seek/admin/retrieve/` | `views.adminRetrieveSamples` | `seek/templates/admin_retrieval.html` |
| Internal Assays | `/seek/admin/internal_assays/` | `views.internalAssays` | `seek/templates/internal_assays.html` |

---

### Authentication

| Page | URL | View | Template |
|------|-----|------|----------|
| Login | `/login/` | `views.login_seek` | `themes/NextSeek/templates/login.html` |
| Signup | `/signup/` | `views.signup_seek` | (Mezzanine accounts) |
| Logout | `/logout/` | `views.logout_seek` | (redirect) |

---

### Publishing

| Page | URL | View | Template | Embeds |
|------|-----|------|----------|--------|
| Publish Samples | (via search) | - | `seek/templates/publish.html` | `pages/publish_*.embed.html` |
| Publish Assets | (via search) | - | `seek/templates/publishAssets.html` | `pages/publishAssets_*.embed.html` |

---

### Other

| Page | URL | View | Template |
|------|-----|------|----------|
| Batch Search | `/seek/samples/query/` | `views.sampleQuery` | `seek/templates/batchSearch.html` |
| Sample Deletion | (via advanced search) | - | `seek/templates/sampleDeletion.html` |
| Error Page | (on error) | - | `seek/templates/error.html` |
| 404 | (not found) | - | `seek/templates/pages/404.html` |

---

## Key Files

### Views
- **Main views**: `seek/views.py` - All seek-related page handlers
- **Auth views**: `dmac/views.py` - Login, logout, signup

### URL Routing
- **Seek URLs**: `seek/urls.py` - All `/seek/*` routes
- **Root URLs**: `dmac/urls.py` - Top-level routing, auth, admin

### Templates Structure
```
seek/templates/
├── *.html                    # Page wrappers (extend base.html)
└── pages/
    └── *.embed.html          # Page content (included in wrappers)

themes/NextSeek/templates/
├── base.html                 # Master layout
├── *.embed.html              # Theme components
└── includes/
    └── user_panel.html       # Auth state display
```

---

## UI Components

### jQuery EasyUI (retained)
Used for datagrids, tabs, and panels throughout the app.
- **CSS**: `static/jquery-easyui-1.5.2/themes/default/easyui.css`
- **JS**: `static/jquery-easyui-1.5.2/jquery.easyui.min.js`
- **Overrides**: `themes/NextSeek/static/css/nextseek.css` (bottom section)

### Bootstrap 5 (new)
Layout and utility classes.
- Loaded via CDN in `base.html`
- Bootstrap Icons via CDN

---

## Updating a Page

1. Find the page in the tables above
2. Locate the view in `seek/views.py`
3. Find the template and any embeds
4. Edit the template/embed HTML
5. For styling, update `themes/NextSeek/static/css/nextseek.css`
6. Run `collectstatic` after CSS/JS changes

---

## NextSeek API (Chat Backend)

API endpoints used by the chat frontend.

**Location**: `nextseek_api/`

**Base URL**: `/nextseek_api/`

### Key Modules
| Module | Path | Purpose |
|--------|------|---------|
| Views | `nextseek_api/views.py` | API endpoint handlers |
| Models | `nextseek_api/models.py` | Data models |
| Assistant | `nextseek_api/assistant/` | AI chat logic |
| Batch Upload | `nextseek_api/batch_upload/` | File upload handling |
| Schema RAG | `nextseek_api/schema_rag/` | Schema-based retrieval for AI |
| Services | `nextseek_api/services/` | Business logic |

### URL Config
See `nextseek_api/urls.py` for all endpoints.

---

## Notes

- All page templates extend `base.html` (sidebar layout)
- Auth pages (login) extend `base_auth.html` (full-screen, no sidebar)
- Content goes in `{% block main %}{% endblock %}`
- Extra head content: `{% block extra_head %}{% endblock %}`
- Extra JS: `{% block extra_js %}{% endblock %}`
- Chat frontend is separate React app, can run standalone or embedded

---

## EasyUI Component Inventory

> **Future replacement roadmap.** These 50 files use jQuery EasyUI and are candidates for migration to a modern component library. Prioritized by user-facing impact. The EasyUI CSS is already rethemed to match the NExtSEEK Option B palette (see `nextseek.css`). Full replacement is a separate initiative.

### Priority 1 - Core Datagrids (highest user impact)

| # | Template | Path | Components | Description |
|---|----------|------|------------|-------------|
| 1 | samples_table | `seek/templates/pages/samples_table.embed.html` | datagrid, linkbutton | Main sample results table - filtering, selection, pagination |
| 2 | searchAdvanced_stable | `seek/templates/pages/searchAdvanced_stable.embed.html` | datagrid, linkbutton | Advanced search results datagrid |
| 3 | searchAdvanced_new_stable | `seek/templates/pages/searchAdvanced_new_stable.embed.html` | datagrid, linkbutton | New search results datagrid |
| 4 | batchSearch_table | `seek/templates/pages/batchSearch_table.embed.html` | datagrid, linkbutton | Batch search results with filtering, pagination, export |
| 5 | datafile_table | `seek/templates/pages/datafile_table.embed.html` | datagrid, linkbutton | Data files listing with sort and pagination |
| 6 | sops_table | `seek/templates/pages/sops_table.embed.html` | datagrid, linkbutton | SOPs/Protocols datagrid with actions |
| 7 | publishAssets_stable | `seek/templates/pages/publishAssets_stable.embed.html` | datagrid, linkbutton | Published assets datagrid |
| 8 | publish_stable | `seek/templates/pages/publish_stable.embed.html` | datagrid, linkbutton | Published samples datagrid |
| 9 | searchAdvanced_rtable | `seek/templates/pages/searchAdvanced_rtable.embed.html` | datagrid, linkbutton | Sample retrieval results datagrid |
| 10 | samples_atable | `seek/templates/pages/samples_atable.embed.html` | datagrid, linkbutton | Sample attributes datagrid |
| 11 | clades | `seek/templates/clades.html` | datagrid, layout, tabs, linkbutton | Clade management with editable rows |
| 12 | internal_assays | `seek/templates/internal_assays.html` | datagrid, layout, tabs, linkbutton | Internal assays datagrid with actions |
| 13 | datagrid_custom_table | `dmac/templates/pages/datagrid_custom_table.embed.html` | datagrid, dialog, linkbutton | Custom editable datagrid with inline editing |

### Priority 2 - Layout/Tabs Wrappers (page shells)

| # | Template | Path | Components | Description |
|---|----------|------|------------|-------------|
| 14 | searchAdvanced | `seek/templates/searchAdvanced.html` | layout, tabs | Advanced search page shell (5 tabs, 1400px) |
| 15 | batchSearch | `seek/templates/batchSearch.html` | layout, tabs | Batch search page shell |
| 16 | batchUpload | `seek/templates/batchUpload.html` | layout, tabs | Batch upload page shell |
| 17 | sampleUpload | `seek/templates/sampleUpload.html` | layout, tabs | Sample upload page shell |
| 18 | sampleSearch | `seek/templates/sampleSearch.html` | layout, tabs | Sample search page shell |
| 19 | sampleQuery | `seek/templates/sampleQuery.html` | layout, tabs | Sample query builder shell |
| 20 | sampleAttributes | `seek/templates/sampleAttributes.html` | layout, tabs | Sample attributes management shell |
| 21 | sampleDeletion | `seek/templates/sampleDeletion.html` | layout, tabs | Sample deletion page shell |
| 22 | dataFilesPage | `seek/templates/dataFilesPage.html` | layout, tabs | Data files page shell |
| 23 | newSearch | `seek/templates/newSearch.html` | layout, tabs | New search interface shell |
| 24 | smartSearch | `seek/templates/smartSearch.html` | layout, tabs | Smart (AI) search page shell |
| 25 | sopsPage | `seek/templates/sopsPage.html` | layout, tabs | Protocols/SOPs page shell |
| 26 | publish | `seek/templates/publish.html` | layout, tabs | Sample publish page shell |
| 27 | publishAssets | `seek/templates/publishAssets.html` | layout, tabs | Asset publish page shell |
| 28 | samples | `seek/templates/pages/samples.embed.html` | layout, tabs | Sample detail with tree browser and info panel |
| 29 | dataFileUpload | `seek/templates/dataFileUpload.html` | combobox, linkbutton | Data file upload form with dropdowns |
| 30 | admin_retrieval | `seek/templates/admin_retrieval.html` | layout, tabs, textbox | Admin retrieval form with multiline input |

### Priority 3 - Forms & Inputs (comboboxes, textboxes)

| # | Template | Path | Components | Description |
|---|----------|------|------------|-------------|
| 31 | batch_upload | `seek/templates/pages/batch_upload.embed.html` | combobox, linkbutton | Batch upload file selector with category dropdown |
| 32 | batchSearch_query | `seek/templates/pages/batchSearch_query.embed.html` | combobox, linkbutton | Batch query form with dropdown selectors |
| 33 | batchSearch_search | `seek/templates/pages/batchSearch_search.embed.html` | combobox, linkbutton, textbox | Batch search form with filters |
| 34 | samples_search | `seek/templates/pages/samples_search.embed.html` | combobox, linkbutton | Sample search form with type/attribute selectors |
| 35 | samples_newsearch | `seek/templates/pages/samples_newsearch.embed.html` | combobox, linkbutton | New sample search form |
| 36 | samples_attributes | `seek/templates/pages/samples_attributes.embed.html` | combobox | Sample attributes selector |
| 37 | samples_upload | `seek/templates/pages/samples_upload.embed.html` | combobox, linkbutton | Sample upload with dropdown selectors |
| 38 | searchAdvanced_search | `seek/templates/pages/searchAdvanced_search.embed.html` | combobox, linkbutton, textbox | Advanced search query builder |
| 39 | searchAdvanced_newsearch | `seek/templates/pages/searchAdvanced_newsearch.embed.html` | combobox, linkbutton | New advanced search form |
| 40 | searchAdvanced_retrieval | `seek/templates/pages/searchAdvanced_retrieval.embed.html` | linkbutton, textbox | Sample retrieval form |
| 41 | searchAdvanced_deletion | `seek/templates/pages/searchAdvanced_deletion.embed.html` | linkbutton, textbox | Sample deletion form |
| 42 | publishAssets_search | `seek/templates/pages/publishAssets_search.embed.html` | combobox, linkbutton | Asset publication search form |
| 43 | publish_search | `seek/templates/pages/publish_search.embed.html` | combobox, linkbutton | Publication search form |
| 44 | datafile_upload | `seek/templates/pages/datafile_upload.embed.html` | linkbutton, textbox | Data file upload form |
| 45 | dialog_custom_upload | `dmac/templates/pages/dialog_custom_upload.embed.html` | dialog, linkbutton | Modal upload dialog |

### Priority 4 - Display / Action Only (linkbutton only)

| # | Template | Path | Components | Description |
|---|----------|------|------------|-------------|
| 46 | samples_new_stable | `seek/templates/pages/samples_new_stable.embed.html` | linkbutton | New stable sample display |
| 47 | samples_stable | `seek/templates/pages/samples_stable.embed.html` | linkbutton | Stable sample display with actions |
| 48 | searchAdvanced_tree | `seek/templates/pages/searchAdvanced_tree.embed.html` | linkbutton, tree | Hierarchical tree navigation for results |
| 49 | searchAdvanced_newretrieval | `seek/templates/pages/searchAdvanced_newretrieval.embed.html` | linkbutton | New sample retrieval display |
| 50 | searchAdvanced_newdeletion | `seek/templates/pages/searchAdvanced_newdeletion.embed.html` | linkbutton | New sample deletion display |

### Summary

| Component | Count | Notes |
|-----------|-------|-------|
| `easyui-datagrid` | 13 | Core data tables - highest migration complexity |
| `easyui-layout` + `easyui-tabs` | 17 | Page shells - can be replaced with Bootstrap tabs |
| `easyui-combobox` | 14 | Dropdown selectors - replaceable with Select2 or native `<select>` |
| `easyui-linkbutton` | 23 | Styled buttons - simple to replace with Bootstrap `.btn` |
| `easyui-textbox` | 7 | Enhanced inputs - replaceable with `.form-control` |
| `easyui-dialog` | 2 | Modals - replaceable with Bootstrap modal |
| `easyui-tree` | 1 | Tree navigation - needs dedicated component |
