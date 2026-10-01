# static/

## What this is

The repo-root static tree. `dmac/settings.py` lists it as the second `STATICFILES_DIRS` entry, behind `themes/NextSeek/static`, so when a path exists in both, the theme copy wins (`themes/README.md`). `collectstatic` copies the merged result into the `/static` volume that nginx serves.

## What it holds

| Path | What it is |
|---|---|
| `js/chat_assistant/` | the committed, built Nessie chat bundle (`assets/`). It has no build step in the Docker image: a UI change is a rebuild of `NessieAI/chat_frontend` plus a commit of the new hashed files here (`NessieAI/chat_frontend/README.md`). |
| `js/sample_timeline/` | the built NHP sample timeline React bundle, loaded by `seek/templates/sample_timeline.html`. Its source is not in this repo; a rebuilt bundle means updating the hashed file names in that template. The backend is `seek/timeline/README.md`. |
| `js/custom/`, `js/dag/`, `js/ns_sample_download.js` | small first-party scripts used by the SEEK pages |
| `js/bootstrap*.js`, `js/html5shiv.js`, `js/respond.min.js`, `css/`, `fonts/` | the Mezzanine project scaffold's Bootstrap and shims |
| `img/`, `robots.txt`, `media/reserved/` | site images, the robots file, and the reserved upload templates (`media/reserved/reserved_readMe.txt`) |
| `admin/`, `grappelli/`, `filebrowser/`, `mezzanine/` | vendored copies of the admin, grappelli, filebrowser and Mezzanine assets. They currently override the ones in the installed packages (the repo copy is found first). Kept until an admin check on a dev box shows the packages' own copies work; then they can be deleted. |

## Rules

- Do not edit the vendored or built files by hand.
- A file here is shadowed by the same path under `themes/NextSeek/static/`.
