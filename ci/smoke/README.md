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
| `--wait-ready` | Run the readiness gate first. Use after a rebuild; skip it for local iteration or you wait out the 300s floor every time. The gate probes an authenticated endpoint, so with no `CI_SMOKE_USER`/`CI_SMOKE_PASS` it exits 2 rather than skipping: a caller that asked for a gate must not read "0 tests, exit 0" as a pass. Without the flag, missing credentials still degrade to per-test skips. |
| `--ready-floor N` | Seconds before the first probe (default 300). |
| `--strict-console` | Fail a flow on any console error not in `CONSOLE_ALLOWLIST`. Off by default so early runs report what is actually there. |
| `--headed` | Watch the browser. |
| `-m write` | Run only the write lane. It is deselected otherwise. |

`./startup.sh ci` is the operator entry point. It runs exactly this command
against the instance's own port, and derives the profile from `ci_profile` in
`startup/.instance.json`, so nobody has to remember which box they are on.

Six files need no stack, no credentials and no browser, because they test the
registry, the guard and the fixtures' own logic rather than a deployment:
`test_registry_unit.py`, `test_registry_contents.py`, `test_guard_unit.py`,
`test_profile_unit.py`, `test_assertions_unit.py`, `test_readiness_unit.py`.

```bash
CI_BOX_PROFILE=local uv run --no-project --with pytest --with requests \
  pytest ci/smoke/test_registry_unit.py ci/smoke/test_registry_contents.py \
         ci/smoke/test_guard_unit.py ci/smoke/test_profile_unit.py \
         ci/smoke/test_assertions_unit.py ci/smoke/test_readiness_unit.py
```

## Tiers

`test_reachability.py` is T0: one test per registry route, parametrised at
collection from `ci/routes.py`. It asserts a status, a live gateway and no
silent bounce to the login page, and it grows by itself as the registry does.

Everything else is hand-written because it is what a table row cannot express --
the API root's exact viewset list, the OpenAPI document generating at all, an
enrichment step that fails silently behind a 200, and the four browser flows.
Per-route body assertions are T1's job and are not in this increment.

## Profiles

Every route in `ci/routes.py` names the profiles it may be called under, and the
client refuses anything else *before* the request leaves the process. **Adding a
route is one line in `ci/routes.py`**; T0 picks it up at the next collection and
nothing in this directory needs editing.

**The box declares its own profile** in `CI_BOX_PROFILE` (`local`, `dev` or
`prod`), and `./startup.sh ci` sets it for you. **An absent value means `prod`**:
a machine nobody has configured gets the most restrictive profile, never the
least.

| flag | what it does |
|---|---|
| `--profile NAME` | Narrow below what the box declares. Asking to widen exits 2 rather than running. |
| `--force-profile NAME` | Widen. Refused unless `CI_FORCE_PROFILE_CONFIRM=yes` is set too, and prints a banner when it runs. Never put this in a workflow file. |

Passing both exits 2 rather than deciding which one wins.

## Credentials

Two accounts, and the split is a safety rule rather than hygiene. The sweep is,
by construction, a program that issues GETs at every URL it knows about, so it
never holds rights it does not need: the health sweep and the four flows
authenticate as the non-superuser, and the sweep never requests any path under
`/seek/admin/`, at any privilege level. Which routes make that rule necessary,
and why, is recorded in the private findings note, which this public repository
does not carry.

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

Breakage that is real is pinned rather than hidden. Every pin reports `xfailed`
while the defect is there and **XPASS** the day it is fixed -- XPASS is the
signal to fix the declaration and delete the pin, and it is why none of these
can quietly turn into a pass.

Most of them now live in the registry: eleven routes carry an `xfail` reason in
`ci/routes.py` and are reported one by one by T0. A registry entry declares the
status a *working* route returns, never the status the broken one returns today,
which is what makes the flip work in both directions.

Two more are hand-written here, because what they assert is not a status code:

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
