# build_tools

Out-of-band generators for committed-but-generated ("lockfile-style") assets.
Nothing here runs inside `docker build` — images COPY the committed outputs.

## ingest_nextseek_docs

Regenerates the NExtSEEK user-docs snapshot baked into the cc-agent image:

- `docker/cc-runtime/docs/nextseek/*.md` + `README.md` + `.content-hash`
- the auto-generated `<!-- BEGIN/END NEXTSEEK-DOCS -->` block inside
  `docker/cc-runtime/container/CLAUDE.md` (the rest of that file is
  hand-authored; the tool never touches it)

Ported from the dmac-assistant repo (`build_tools/ingest_nextseek_docs/` at
`a429f137`), with two NExtSEEK changes: retargeted default paths
(`constants.py`) and tolerance for GitBook's 2026-07 markdown-export format
(leading llms.txt banner; reworded trailing agent-instructions boilerplate).

Run (needs network to the upstream GitBook; any python with `httpx`):

```sh
python -m build_tools.ingest_nextseek_docs          # no-op if upstream unchanged
python -m build_tools.ingest_nextseek_docs --force  # rewrite regardless
```

Exit codes: 0 = no change, 2 = changes written, 1 = error. Commit whatever it
rewrites — builds must never depend on refetching.

Tests (hermetic, no network/django):

```sh
python -m pytest build_tools/tests/
```
