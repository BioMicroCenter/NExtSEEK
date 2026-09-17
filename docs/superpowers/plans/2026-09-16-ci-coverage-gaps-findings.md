# What `makemigrations` reports, and how far the check can be restored

Task C2 of [`2026-09-16-ci-coverage-gaps.md`](2026-09-16-ci-coverage-gaps.md). Investigation only, no code.
Measured 2026-09-17 in the Django lane at branch `feat/graph-behaviour-tests`, after task C1 named the TurnLedger
index.

Command:

```
manage.py makemigrations --check --dry-run --skip-checks -v 2
```

## Outcome: per-app, `nextseek_api` only

Decision A2 asked whether the whole-app check can be switched on. It cannot, and one of the two remaining apps is
blocked by a defect rather than by noise. `nextseek_api` is clean and is what task C3 wires in.

## Per app

### `nextseek_api`: clean

`No changes detected in app 'nextseek_api'`, exit 0, after C1. This is the app the check exists to protect.

### `blog`, `core`, `generic`, `pages`: vendor artefact, unfixable here

Mezzanine's own apps. The proposed migrations would be written **into the installed package**, not into this
repository:

```
/app/.venv/lib/python3.14/site-packages/mezzanine/blog/migrations/0004_alter_blogpost_related_posts_alter_blogpost_user.py
/app/.venv/lib/python3.14/site-packages/mezzanine/core/migrations/0003_alter_sitepermission_user.py
/app/.venv/lib/python3.14/site-packages/mezzanine/generic/migrations/0004_alter_rating_user.py
/app/.venv/lib/python3.14/site-packages/mezzanine/pages/migrations/0005_alter_page_in_menus.py
```

Every operation is `~ Alter field` on `user`, `related_posts` or `in_menus`: the shape Django regenerates
differently from what the installed Mezzanine shipped. Nothing in this repository can resolve them, short of
vendoring Mezzanine's migrations, and `uv` would overwrite any edit to `site-packages` on the next sync.

**They must be excluded from any migration check.** A whole-app check is therefore permanently impossible while
Mezzanine is a dependency.

### `seek`: blocked on a real defect, not on noise

Proposed:

```
+ Create model Project_template_bundles
+ Create model Sample_attributes_unique
+ Create model Sample_type_requirements
+ Create model Sample_types_context
+ Create model Session_state
+ Add field description to sample_attributes
~ Alter field assay_id, internal_assay_id on assays_internal_assays
~ Alter field clade_id, sample_type_id on sample_types_clades
```

These five models have their DDL applied out of band by the installer's schema fixups, never by a migration, which
is why no migration declares them. `seek/CLAUDE.md` states the invariant they are supposed to keep:

> A model whose DDL is applied out of band must stay `managed = False`. Because `allow_migrate` returns `None` for
> this app label, a managed model would let the next unrelated `makemigrations` create the table on both aliases.

Measured against the live model registry:

| Model | `managed` | Invariant |
|---|---|---|
| `Sample_attributes_unique` | `False` | kept |
| `Sample_type_requirements` | `False` | kept |
| `Project_template_bundles` | `False` | kept |
| **`Sample_types_context`** | **`True`** | **violated** |
| **`Session_state`** | **`True`** | **violated** |

So two tables created out of band are declared managed. That is the exact condition `seek/CLAUDE.md` warns about:
any unrelated `makemigrations` in this app would generate a `CreateModel` for them, and applying it would attempt
the table on both aliases.

`Sample_types_context` is the context table the assistant's catalogs are built from, so this sits next to the
context work rather than apart from it.

**`seek` cannot join the migration check until those two flags are ruled on.** Flipping them is a behaviour change
with a documented blast radius and is not part of restoring a CI check, so it is left as an open question rather
than folded in here.

## What this means for task C3

Wire the check as `makemigrations --check --dry-run --skip-checks nextseek_api`, naming the app explicitly. Record
in `ci/CLAUDE.md` that Mezzanine is permanently excluded and that `seek` is excluded pending the managed-flag
ruling, so the next reader does not assume the narrow scope was laziness.

## Open questions this raised

1. Should `Sample_types_context` and `Session_state` become `managed = False`, matching their three siblings and the
   stated invariant? This is the operator's call.
2. Are the four `~ Alter field` operations on `assays_internal_assays` and `sample_types_clades` also vendor-shaped
   drift, or a real divergence between those models and their out-of-band DDL? Not established; not investigated.
3. `CSRF_TRUSTED_ORIGINS` still makes the check fail without `--skip-checks` (spec decision A3). Unchanged here.
