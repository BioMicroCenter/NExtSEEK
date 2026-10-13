# seek/timeline/

## What this is

The backend of the NHP (non-human primate) sample timeline page. It reads an animal's visits, treatments, tissues and images from the SEEK database and shapes them into the event data the React timeline draws. The front end is a built bundle in `static/js/sample_timeline/` (`static/README.md`), loaded by `seek/templates/sample_timeline.html`.

## Layout

| Path | What it holds |
|---|---|
| `core/database.py` | its own MySQL connection pool (`get_db_connection`, `execute_query`), not Django's |
| `services/nhp_service.py` | fetches an NHP's records and their descendants (`get_timeline_data`, `save_nhp_info_to_json`, `save_nhp_data` for the Excel download) |
| `services/timeline_service.py` | turns the records into dataframes and event objects (`run_All`, `get_event_data`) |
| `services/nhp_cache_cli.py` | a command-line check, not a test: `python -m seek.timeline.services.nhp_cache_cli <nhp_uid>` from the repo root with Django settings configured; it writes `nhp_cache_test.log` in the current folder |
| `models/schemas.py` | the record shapes |
| `utils/helpers.py` | a small query helper and `get_NHP_name` |

## Entry points

`seek/views/timeline.py` is the only caller: `nhp_info`, `get_nhp_data`, `download_nhp_data` and `fetch_event_data`, routed in `seek/urls.py` as `nhpinfo/`, `nhpdata/` (and `.../download/`) and `eventdata/`, plus the `sample_timeline/` page itself.

## Landmines

The pool takes its database name from the environment variable `DB_NAME` (`DB_NAME1` for the second database), which nothing in this repo sets, so it connects with no schema selected and only fully qualified queries work. Importing the timeline services also calls `logging.basicConfig` at import time. Both are described in `seek/CLAUDE.md` "Landmines".
