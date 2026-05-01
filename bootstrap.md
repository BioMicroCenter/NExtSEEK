# Bootstrap Plan

## Goal

Create a future one-command bootstrap workflow so a user can do something close to:

```bash
git clone <repo>
cd NExtSEEK
./run.sh
```

and then be guided through a small CLI flow that:

1. prepares local configuration
2. imports MySQL data
3. imports Neo4j data
4. builds and starts the Docker stack
5. validates that the app is up

This document is a planning note, not an implementation.

## Short Answer

Yes, this is feasible.

The right model is not a single giant image. The right model is a bootstrap layer on top of the existing Docker Compose stack.

That means:

- keep the current multi-service Docker setup
- add a single entrypoint script such as `run.sh`
- have that script call smaller helper scripts
- prompt the user for a few local values
- perform the documented setup steps automatically

## Why Docker Compose Still Makes Sense

This project is already a multi-service system:

- MySQL
- Neo4j
- SEEK
- NExtSEEK
- nginx
- workers

That is a good fit for Docker Compose.

Singularity or Apptainer would not be the first choice here unless the main target were HPC environments and a single-container workflow. For the current architecture, Docker Compose is the better foundation.

## Desired User Experience

Target flow:

```bash
git clone <repo>
cd NExtSEEK
./run.sh
```

Then the script should:

- check prerequisites
- ask a few setup questions
- write or patch local config files
- create volumes and directories
- import MySQL
- import Neo4j
- build the application image
- launch the stack
- run health checks
- print the final URLs

## What A Future `run.sh` Should Do

### Prerequisite checks

- verify `docker` is installed
- verify `docker compose` is available
- verify the script is being run from the expected repo root
- verify required local companion files exist

### Interactive prompts

Examples:

- fresh install or reuse existing volumes
- MySQL root password
- Neo4j password
- local hostname or port
- whether to import bundled demo data
- whether to clone or validate `chat_nextseek/`

### Local file generation

The bootstrap should create or update:

- `docker/db.env`
- `docker/nextseek.env`
- `dmac/local_settings.py`

It should also create:

- `logs/`
- `outputs/`

### Data import

The bootstrap should be able to:

- start MySQL alone
- import the MySQL dump(s)
- start Neo4j alone
- import the Neo4j export

### Build and launch

The bootstrap should:

- build `nextseek`
- start SEEK-side services
- start `nextseek` and nginx
- run post-start validation checks

### Validation

Examples:

- `docker compose ps`
- Django `manage.py check`
- app URL reachable
- nginx reachable
- expected CSRF settings loaded

## Suggested Script Layout

Possible structure:

- `run.sh`
  - simple user entrypoint
- `scripts/bootstrap.sh`
  - orchestrates the full setup
- `scripts/write_local_config.sh`
  - writes env files and `local_settings.py`
- `scripts/import_mysql.sh`
  - imports or migrates MySQL data
- `scripts/import_neo4j.sh`
  - imports Neo4j data
- `scripts/validate_stack.sh`
  - runs health checks

This keeps the implementation maintainable and avoids turning one shell script into an unreadable installer.

## Important Constraints

### 1. The seed data should be cleaned first

Before automating this for other users, the MySQL and Neo4j seed files should be reviewed and cleaned so they contain only what should actually ship with a bootstrap workflow.

That likely includes:

- removing environment-specific data
- removing sensitive or internal-only records
- standardizing schema expectations
- documenting the exact provenance of each seed file

### 2. `chat_nextseek` is still a separate repo

The current Docker build depends on a local `chat_nextseek/` checkout.

That means a bootstrap flow must either:

- require the user to clone it manually first, or
- offer to clone it during setup, or
- switch to a different packaging model later

### 3. First-run versus repeat-run behavior matters

The installer should clearly distinguish:

- fresh install
- rebuild using existing volumes
- reset and reimport

Without this, users will accidentally overwrite local state.

## Current Data File Location

At the time of writing, the MySQL and Neo4j import files are **not** inside the nested `NExtSEEK/` git repo.

In this local workspace they currently live in the parent wrapper repo under:

- `/home/cdemu/code/dmac/docker/resources/dmac_dev_dump.sql`
- `/home/cdemu/code/dmac/docker/resources/seek_production_dump.sql`
- `/home/cdemu/code/dmac/docker/resources/neo4j_export_clean.cypher`

So, to be explicit:

- they are not currently inside the public `BMCBCC/NExtSEEK` repository
- they are currently outside `NExtSEEK/`, in the higher-level private/local workspace repo

If this bootstrap flow is later made shareable, the project will need a deliberate decision about where those seed files should live and how they should be distributed.

## Recommended Future Milestones

### Phase 1: planning and cleanup

- clean the MySQL dump(s)
- clean the Neo4j export
- define the minimal safe bootstrap dataset
- decide how `chat_nextseek` should be handled

### Phase 2: scripted local bootstrap

- implement `run.sh`
- implement helper scripts
- support fresh install and rebuild flows
- validate on a clean machine

### Phase 3: nicer distribution

- reduce manual prompts where sensible
- add a non-interactive mode such as `./run.sh --defaults`
- document recovery and reset workflows
- optionally package a demo-data flow separately from a blank install flow

## Bottom Line

This is realistic and worth doing.

The best approach is:

- keep Docker Compose
- build a bootstrap wrapper around it
- clean the seed data first
- only then automate the full import-and-launch workflow
