# Post-deploy smoke suite

Runs **outside** the container, against a deployed stack, over HTTP, the way a
user does. Same command locally, on fairdata-dev, and in CI.

## Run it

```bash
# one time, per host
uv run --no-project --with playwright playwright install chromium

# everything except the write lane
uv run --no-project --with pytest --with requests --with playwright \
  pytest ci/smoke/ --base-url http://127.0.0.1:8000
```

Useful flags:

| flag | what it does |
|---|---|
| `--wait-ready` | Run the readiness gate first. Use after a rebuild; skip it for local iteration or you wait out the 300s floor every time. |
| `--ready-floor N` | Seconds before the first probe (default 300). |
| `--strict-console` | Fail a flow on any console error not in `CONSOLE_ALLOWLIST`. Off by default so early runs report what is actually there. |
| `--headed` | Watch the browser. |
| `-m write` | Run only the write lane. It is deselected otherwise. |

## Credentials

Two accounts, and the split is a safety rule rather than hygiene. Several `seek`
admin routes act on `request.GET` with no method check, and at least one deletes
rows in response to a bare GET, so the sweep must never hold superuser rights.

```
~/.config/nextseek/ci.env      mode 600, never committed, never in GitHub

CI_SMOKE_USER=...     NOT a superuser. Health sweep + the four flows.
CI_SMOKE_PASS=...
CI_WRITE_USER=...     Superuser. The write lane only.
CI_WRITE_PASS=...
```

Environment variables override the file. `NEXTSEEK_CI_ENV` points at a different
file.

**Both accounts must log in through `/login/` once, by hand, on each box before
anything works.** `BasicAuthentication` validates against Django's `auth_user`
table, and only the login view creates that row. Until then every request is a
401 and the reason is not obvious.

## Two authentication modes, not interchangeable

| surface | how |
|---|---|
| `/nextseek_api/*` | HTTP Basic |
| `/seek/*` | a real session cookie from a POST to `/login/` |

`seek` views read `request.session['username']`, which Basic auth never
populates, so a Basic-authenticated request to a `/seek/` page returns a 302 to
`/login/`. A sweep that follows redirects reports that as 200 and calls the page
healthy. Hence `allow_redirects=False` everywhere, and two separate fixtures
(`api` and `web`) that must never share a session: DRF stops at the first
authenticator that succeeds, and a stray `sessionid` outranks the Basic header.

## Known conditions

Two tests are `xfail`. Both are real, both are documented in place, and both flip
to XPASS when fixed rather than silently passing:

- `test_seek_identity_matches_the_authenticated_caller` — two different
  authenticated accounts are reported as the same SEEK person. Cause and fix
  are in the private findings note, not in this repo.
- `test_entity_tree_nodes` — sample types with no attribute definitions make the
  endpoint return an application-level 502.

## What a 200 does not prove

`check_gateway` distinguishes an nginx 502 (HTML, the stack is down, always a
failure) from an application 502 (a JSON envelope, a data condition). Beyond
that, a great many endpoints return 200 on failure: `schema_rag/retrieve/` always
does, roughly thirty `seek` paths return permission denials and wrong-method
errors as 200, `batch-upload/validate/` returns 200 for an invalid sheet, and a
SEEK outage becomes `total: 0` in several places. Every assertion here checks a
body or a rendered element, not just a status.

## Cost

Nothing here spends money. The Nessie flow proves the page is wired by asserting
`?q=` hydration, and where it exercises the send path it aborts the request in
the browser before it leaves. `batch-upload/validate/` involves no model call and
no INSERT.

One caveat on a shared box: validate always runs UID generation, which takes a
MySQL advisory lock, so it can contend briefly with somebody's live upload.
