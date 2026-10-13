# scripts/laya/label_page: blind labelling of the held-out draft

`build_page.py` writes one offline HTML page from the draft: the full chat and the question, never the family,
entity or current route; labels stay in the browser and export as JSON. `ingest.py` folds that export back into
the draft. Both refuse an output path inside any git repo. Tests: `NessieAI/tests/router/test_laya_label_page.py`.
