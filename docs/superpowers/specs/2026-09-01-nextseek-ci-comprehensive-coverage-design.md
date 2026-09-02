# NExtSEEK CI: comprehensive route coverage

Date: 2026-09-01
Status: approved design, not yet implemented

Relationship to the other two CI specs in this directory:

| spec | what it is |
|---|---|
| `2026-09-01-nextseek-ci-cd-design.md` | the original approved design: five decisions, four flows |
| `2026-09-01-nextseek-ci-cd-full-spec.md` | what was built and proven against the local stack |
| **this document** | the expansion from ~20% route coverage to declared coverage of all of it |

This document is public. It names paths and mechanisms but carries no reproduction
detail for unresolved platform issues, per `.gitignore:217`. See section 10, which
makes that a constraint on the code as well as on the prose.

---

## 1. Problem

The suite built on 2026-09-01 is green and useful, and it found a real defect on its
first run. It is also narrow, and narrower than the approved design asked for.

Measured against the live URL resolver:

```
                            touched   total    
nextseek_api routes            23       89     26%
seek routes                     9       56     16%
pages with a real interaction   4       23     17%
```

The original design's Job 3 said to "enumerate the real list from `seek/urls.py` rather
than guessing." Nine pages were hand-picked instead. That is the gap.

Two problems, not one:

1. **Coverage is low.** Most of the application is never requested by CI.
2. **Coverage decays silently.** A route added next month gets no test and nothing
   notices. Fixing (1) once, by hand, does not fix (2), and (2) is what returns the
   suite to 20% within a few months.

A third constraint arrived with the deployment workflow: CI is to run against
**production** after a deploy. That makes broad sweeping actively dangerous, because on
this codebase a GET is not safe by default.

## 2. The workflow this serves

```
change -> push to dev  -> ./startup.sh rebuild  -> readiness -> CI T0-T4  (writes allowed)
       -> push to prod -> ./startup.sh rebuild  -> readiness -> CI T0-T3  (writes impossible)
```

CI is part of the rebuild, not a separate step the operator remembers. `--no-ci` skips it;
`./startup.sh ci` runs it on its own against an already-running stack. See section 5a.

## 3. Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Structure | Registry for breadth, hand-written tests for depth |
| 2 | Environments | Three profiles: `local` (disposable stack), `dev`, `prod` |
| 3 | Read-only enforcement | In the HTTP client and the browser context, default-deny |
| 4 | Pre-existing breakage | Pinned and `xfail`ed, never excluded, so coverage is 23/23 |
| 5 | Anti-decay | A completeness gate in job 1 that blocks, plus two softer layers |
| 6 | Public-repo safety | Exclusion reasons in the registry are category codes, not descriptions |

## 4. The registry

`ci/routes.py`. Python rather than YAML, so the tests and
`scripts/validate_viewset_conventions.py` can both import it and it type-checks.

```python
@dataclass(frozen=True)
class Route:
    pattern:  str                 # the Django URL pattern, matched against the resolver
    path:     str | None          # concrete request path, may contain {placeholders}
    methods:  tuple[str, ...]     # ("GET",) — what CI will send, not what the route allows
    profiles: frozenset[str]      # subset of {"local","dev","prod"}; empty means never called
    auth:     str                 # "anon" | "smoke" | "web" | "write"
    expect:   int | tuple[int,...]
    shape:    str | None          # a key that must exist in the JSON body
    xfail:    str | None          # reason, when the route is broken today
    exclude:  str | None          # a CATEGORY CODE, when profiles is empty. See section 10.
    note:     str | None

    def __post_init__(self):
        # Entries are written as profiles="local,dev" for readability; normalise to a
        # frozenset here so exactly one representation exists at run time.
        if isinstance(self.profiles, str):
            object.__setattr__(self, "profiles",
                               frozenset(p.strip() for p in self.profiles.split(",") if p.strip()))
```

Every call site writes `profiles` as a comma-separated string and every consumer reads a
frozenset. Without the normalisation the two would silently disagree, and `"prod" in
"local,dev,prod"` is a substring test that returns True for garbage.

### Why `pattern` and `path` are separate fields

`pattern` comes verbatim from Django's resolver and is what the completeness gate diffs
against. `path` is what gets requested. Parameterised routes carry placeholders:

```python
Route(pattern=r"^seek/sample/id=(?P<id>\d+)/$",
      path="/seek/sample/id={sample_id}/", ...)
```

Placeholders resolve from a session-scoped `discovered` fixture that finds real values at
run time (a sample id and UID, a project id, a sample type, a SOP uid). Nothing is
hard-coded: ids are deployment-specific, and the seed and production disagree about all of
them.

Keeping the two fields separate means path templating can be reworked without ever
weakening the gate.

### `auth` values

| value | client |
|---|---|
| `anon` | no credentials |
| `smoke` | Basic, non-superuser (`ci_smoke`) |
| `web` | session cookie from a real `/login/` POST, non-superuser |
| `write` | Basic, superuser (`ci_write`) |

`web` exists because Basic auth does not work for `/seek/` pages: those views read
`request.session['username']`, which Basic never populates, so a Basic-authenticated
request to a `/seek/` page returns a 302 to `/login/`. Measured, not assumed.

## 5. Profiles, enforced in the client

```
local   disposable stack from the committed seed   everything, destructive included
dev     fairdata-dev                               writes into a CI-only project
prod    production                                 reads only
```

Selected with `--profile`, defaulting to `local`. Enforcement is a `Session` subclass, not
a convention a test author has to remember:

```python
class GuardedSession(requests.Session):
    def request(self, method, url, **kw):
        route = REGISTRY.match(url)
        if route is None:
            raise ProfileViolation(f"unregistered URL: {url}")
        if self.profile not in route.profiles:
            raise ProfileViolation(f"{url} is not enabled for profile {self.profile}")
        if self.profile == "prod" and method.upper() != "GET":
            raise ProfileViolation(f"{method} refused under the prod profile")
        return super().request(method, url, **kw)
```

Playwright gets the same treatment through `context.route("**/*", ...)`, aborting any
non-GET request under `prod`.

**Default-deny is the whole point.** A URL matching no registry entry is refused. Routes
that mutate on GET are therefore excluded because nobody opted them in, not because
somebody remembered to list them, and a dangerous route added in future is excluded until
someone deliberately opts it in. That inversion is what makes running against production
defensible.

## 5a. Invocation: the `startup/` shim

CI is not a thing the operator remembers to run. It runs as part of the rebuild, with a
manual entry point for when it is needed on its own.

```
./startup.sh rebuild                 rebuild, wait for readiness, then run CI   <- default
./startup.sh rebuild --no-ci         rebuild only
./startup.sh ci                      run CI now against the running stack
./startup.sh ci --wait-ready         apply the readiness floor first
```

### Where it lives, and what it must not import

`startup/ci/` builds an argv and subprocesses. It must **not** import anything under
`ci/smoke/`, because `startup/` is deliberately kept to typer, rich, neo4j, orjson and
PyMySQL so that `./startup.sh` stays bootstrappable on a host with no C toolchain. Its
`pyproject.toml` carries a long comment defending exactly that, and pulling playwright and
requests into it would undo it.

```
startup/ci/  ->  uv run --no-project --with pytest --with requests --with playwright \
                   pytest ci/smoke/ --profile <p> --base-url <derived> [--wait-ready]
```

Three environments, none of which contaminate the others:

| environment | holds |
|---|---|
| `startup/` | typer, rich, neo4j, orjson, PyMySQL |
| `ci/smoke/` | pytest, requests, playwright |
| the app | its 67 dependencies |

The base URL is derived from `startup/.instance.json` (`ports.nextseek`), so the operator
never types a URL and can never point a dev run at production by getting it wrong.

### The box declares its own profile

A profile passed on the command line is a profile that can be mistyped. `--profile dev` on
the production box would disable the read-only guard on production, which is the exact
outcome the guard exists to prevent, one keystroke away.

So the box declares it, in `startup/.instance.json`:

```json
{ "name": "dev", "ci_profile": "dev", "ports": { "nextseek": 8000 } }
```

**Absent means `prod`.** A machine nobody has configured gets the most restrictive profile,
not the least. Failing closed is the whole point of putting it here.

### Narrowing is quiet; widening is loud

The operator must be able to run the full suite anywhere, including production. That is a
legitimate need and the design supports it. What it does not support is doing so by
accident.

| flag | direction | behaviour |
|---|---|---|
| `--profile prod` on a `dev` box | narrowing | allowed, silent |
| `--profile dev` on a `prod` box | widening | **refused**, with a message naming `--force-profile` |
| `--force-profile dev` on a `prod` box | widening | allowed, after an interactive confirmation, banner-logged |

Two separate flags rather than one, deliberately. A single `--profile` that sometimes
widens is a flag whose danger depends on where it runs, and it would be copied into a
workflow file by someone who only ever tested it on dev. `--force-profile` cannot be typed
by accident and reads as dangerous at the call site.

`--force-profile` never persists. It applies to one invocation and is not written back to
`.instance.json`.

### When CI fails after a rebuild

The rebuild has already happened and CI does not undo it. On failure the command reports
the failing tiers, exits non-zero, and points at `DEPLOYMENT.md` for the rollback
procedure. It never rolls back on its own: an automatic rollback triggered by a test
failure is a larger and more dangerous action than the one it is reacting to.

## 6. Tiers

| tier | what | source | prod | dev | local |
|---|---|---|---|---|---|
| T0 reachability | status, not a dead gateway, not bounced to `/login/` | registry | yes | yes | yes |
| T1 contract | body shape, envelope keys, identity and scoping | registry | yes | yes | yes |
| T2 page actions | one real interaction per page, plus console capture | hand-written | yes | yes | yes |
| T3 flows | multi-step journeys | hand-written | yes | yes | yes |
| T4 write lane | dry-run, then real, with teardown cleanup | hand-written | no | yes | yes |
| T5 destructive | sync routes, deletion, batch-upload start | hand-written | no | no | yes |

T0 and T1 are parametrised from the registry, so they grow automatically as it grows.
T2-T5 are hand-written because a five-step browser interaction cannot be expressed as a
table row.

## 7. The production run

Read-only is necessary but not sufficient. Actions logs and artifacts on a public
repository are world-readable, and this instance holds human-subject data.

Under `--profile prod`:

- Every request is GET, enforced as in section 5.
- **Failure output carries shape, never values.** A custom assertion helper reports
  status, content-type, and the body's sorted keys and collection lengths. It never prints
  a response body.
- Discovered identifiers are redacted in test ids and failure messages.
- The job uploads no artifacts.
- Counts assert `> 0`, never equality: production data moves.

## 8. The completeness gate

```python
def test_every_route_is_registered():
    live     = {str(p.pattern) for p in walk(get_resolver())}
    declared = {r.pattern for r in REGISTRY}
    missing  = live - declared        # a route exists that nobody declared
    stale    = declared - live        # a declaration outlived its route
```

### It lives in job 1, and it blocks

The smoke suite runs outside the container with no Django installed, so it cannot walk the
resolver. Job 1 already runs `uv sync` and imports Django against in-memory SQLite with no
stack, which is proven. The gate belongs there, where it is free.

This is a deliberate exception to "the pytest job is informational". That rule exists
because the legacy suite is not green and a blocking gate would be ignored within a week.
This gate is new, deterministic, and passes unless somebody adds an undeclared route, so
it carries none of that risk. Job 1 therefore exits non-zero for this one test and
continues to ignore every other failure.

### The failure message is the feature

Adding a route must never mean writing a workflow. It means adding one line, and the gate
prints that line:

```
FAILED test_every_route_is_registered

  2 routes are not declared in ci/routes.py:

    ^nextseek_api/^^samples/bulk_annotate/$   POST
    ^seek/^admin/reindex/$                    GET

  Add each one, or declare it excluded with a category code:

    Route(pattern=r"^nextseek_api/^^samples/bulk_annotate/$",
          path="/nextseek_api/samples/bulk_annotate/",
          methods=("POST",), profiles="local,dev", auth="write", expect=200)
```

### Three layers, decreasing softness

| when | mechanism | kind |
|---|---|---|
| authoring | `.claude/skills/nextseek-viewset/SKILL.md` gains a "register the route" step | reminder |
| before commit | `scripts/validate_viewset_conventions.py` gains a registry check | fast local fail |
| CI | `test_every_route_is_registered` | hard gate |

Layers one and two make it convenient. Layer three makes it independent of memory. The
reason all three exist is that this project already has a worked example of a reminder
failing: the approved design said "enumerate the real list from `seek/urls.py`" and the
implementation did not.

## 9. Pre-existing breakage is pinned, not excluded

Every route is registered and, wherever possible, exercised. Routes that are broken today
are asserted as broken, with the cause in the `xfail` reason, so a fix turns the test green
on its own:

```python
Route(..., xfail="NameError: getPageRequests is not defined")
```

Coverage is then 23 of 23 pages rather than 9, and nothing hides behind an exclusion. Two
pages 500 unconditionally on any request today; several more are known-degraded.

`xfail` is used with `strict=False`, so a route that starts working reports XPASS rather
than failing the suite.

## 10. Public-repo safety inside the registry itself

The registry is a committed file in a public repository. An `exclude` field reading
"deletes rows on a bare superuser GET" would republish precisely the index this project has
agreed to keep out of git.

`exclude` therefore takes a **category code**, never a description:

```
EXCLUDE_COST            calls a paid model
EXCLUDE_EXTERNAL        needs Luria SSH or another external system
EXCLUDE_UNSAFE_METHOD   unsafe to call from an automated sweep
EXCLUDE_DEAD            route cannot function; tracked separately
EXCLUDE_ADMIN           administrative surface, out of scope for CI
```

The mapping from a code to a specific reason lives in the uncommitted findings note. The
registry stays honest and reviewable; it does not become a recipe.

## 11. The page inventory

23 pages, resolved from the URLconf. `sample_timeline` is included: it is a `TemplateView`,
whose `as_view()` returns a function named `view`, so a naive scan for `render(` misses it.

| page | treatment |
|---|---|
| `/seek/search/` | T3, plus the remaining three tabs |
| `/seek/samples/upload/` | T3 |
| `/seek/sample/id=N/` | T3 |
| `/seek/assistant/` | T3 |
| `/seek/sampletree/uid=X/` | T2, exercises UID resolution which `id=` never does |
| `/seek/samples/attributes/` | T2, select a type and assert the grid populates |
| `/seek/projects/` | T2, open a project card |
| `/seek/projects/<id>/` | T2, stats table populates |
| `/seek/samples/query/` | T2, grid loads rows |
| `/seek/sample_types/id=N/` | T2 |
| `/seek/data/upload/` | T2 |
| `/seek/sop/query/` | T2, grid fed by `/nextseek_api/sops/` |
| `/seek/datafile/query/` | T2 |
| `/seek/newsearch/` | T2. Unlinked from the UI; noted so nobody mistakes it for the daily driver |
| `/seek/sample_timeline/` | T2, excluded from the console check (its bundle emits unavoidable 404s) |
| `/seek/help/` | T0, genuinely static and public |
| `/seek/samples/search/` | T0, a pure 302 to `/seek/search/` |
| `/seek/templates/` | T2 + `xfail`: renders zero links on a stock deployment |
| `/seek/admin/clades/` | T2, `local`+`dev` only, `write` auth |
| `/seek/admin/internal_assays/` | T2, `local`+`dev` only, `write` auth |
| `/seek/admin/retrieve/` | T2, `local`+`dev` only |
| `/seek/url/<x>/` | T0 + `xfail`, 500 by inspection |
| `/seek/remote/` | T0 + `xfail`, 500 by inspection |

## 12. The API surface

89 `nextseek_api` routes with format-suffix duplicates removed: 56 have a write method, 28
are read-only, 5 are non-viewset (schema, swagger, redoc). Plus 34 `seek` JSON endpoints
behind the pages.

Treatment by bucket:

| bucket | count | treatment |
|---|---|---|
| read-only `nextseek_api` | 28 | T0 + T1, all three profiles |
| non-viewset | 5 | T0 + T1; `schema/` is the highest-value single check in the suite |
| write, with a safe preview | 5 | T1 asserts the preview writes nothing; T4 does the real write |
| write, no preview | 51 | T4 on `dev` and `local`; excluded on `prod` |
| paid | 11 | `EXCLUDE_COST` |
| external | 1 | `EXCLUDE_EXTERNAL` |
| `seek` JSON | 34 | T0 + T1 where safe; the GET-mutating subset is `EXCLUDE_UNSAFE_METHOD` |

Only **four** endpoints carry a `dry_run` flag: assay-registration, and the attributes
batch-create, batch-patch and batch-delete. `batch-upload/validate/` is the fifth safe
preview but reaches it differently, as a separate action rather than a flag. So 51 of the
56 write-capable routes have no preview mode at all, and the only way to cover them is a
real write in a place where that is acceptable. That single number is the argument for the
`local` disposable stack: without it, T4 on `dev` is the only option and 51 real mutations
land on a shared box.

`GET /nextseek_api/schema/` deserves its own note: drf-spectacular walks every annotated
endpoint to build the document, so that one request validates the schema annotations of all
67 documented paths at once. It is not runtime coverage of them, but it catches a whole
class of breakage across the full surface for the cost of one call.

## 13. Migration

Nothing built on 2026-09-01 is discarded.

| exists today | becomes |
|---|---|
| the four flows in `test_flows.py` | T3 |
| the parametrised sweeps in `test_health.py` | T1 registry entries |
| the individual health assertions | T1, keeping their hand-written bodies |
| `test_write_lane.py` | T4 |
| the two `xfail`s | registry `xfail` fields |
| `check_gateway`, the 502 discriminator | shared helper, used by T0 |
| the `api` / `web` fixtures | the `auth` field's two non-superuser values |

## 14. Non-goals

- Fixing the routes this exercise pins as broken. They are recorded and tracked separately.
- Covering Django admin (110 routes), Mezzanine (28), or DRF format-suffix duplicates (85).
- Running the paid endpoints on any schedule. `EXCLUDE_COST` is not a to-do.
- Making the legacy pytest suite green. Unchanged from the earlier spec.

## 15. Increments

This is too large for one implementation plan. Five increments, each independently
shippable and each leaving the suite green.

| # | Increment | Contains | Blocked by |
|---|---|---|---|
| 1 | **Skeleton and safety** | registry, `GuardedSession`, the three profiles, the browser guard, T0 across all 157 routes, the completeness gate in job 1, the `startup/ci/` shim and the rebuild hook (5a), migration of the existing tests | nothing |
| 2 | Contract assertions | T1 body-shape and identity assertions on every read route | 1 |
| 3 | Page actions | T2 for the 14 pages never requested and the 5 with no interaction | 1 |
| 4 | Write expansion | T4 across the 51 no-preview write routes, on `dev` | 1, plus a CI-only project on dev |
| 5 | Destructive lane | T5, the sync routes and deletions | 1, plus the disposable stack |

Increment 1 deliberately carries the safety mechanism rather than deferring it. T0 is the
first tier that would run against production, so the guard has to ship in the same
increment, not after it.

## 16. Open items

1. **The disposable stack for `local` does not exist yet.** `startup/dev/run_full_test_lane.sh`
   is the closest existing pattern but is pinned to an image digest not present on this
   workstation. T5 is blocked until that is sorted; T0-T4 are not.
2. **The `dev` write target.** A CI-only project must exist on fairdata-dev, and the write
   lane must be scoped to it.
3. **Readiness-gate floor.** Still the design's guessed 300s. Should come down once there
   are real numbers from the box.
4. **What the self-hosted GitHub runner is still for.** Resolved differently than expected.
   The earlier open question was whether `startup.sh` may call `gh workflow run`; section 5a
   makes `startup.sh` run the suite **directly**, so that trigger is no longer needed.

   Which leaves the self-hosted runner without its original job. It retains one real value:
   a durable, shareable record. Run from `startup.sh`, results live in one operator's
   terminal scrollback; run through a workflow, they become job summaries, history, and
   failure notifications the whole group can see.

   Three options, decision deferred until increment 1 is working:

   | option | consequence |
   |---|---|
   | drop the runner | simplest; results exist only where the rebuild was run |
   | keep `ci-smoke.yml` as a second, manual entry point | duplicate invocation paths to keep in step |
   | `startup.sh` posts results to GitHub without a runner | no daemon on a shared box; needs a token and a place to post |

   Worth settling before the runner is installed, since installing it is currently listed as
   an operator prerequisite in the earlier spec and may no longer be one.
