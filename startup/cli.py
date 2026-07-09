"""NExtSEEK startup CLI."""
from __future__ import annotations

import datetime
from pathlib import Path

import typer

from startup.lib import ui
from startup.lib.env import read_env
from startup.lib.instance import (
    InstanceState,
    resolve_instance_name,
    load_instance,
    save_instance,
)
from startup.lib.ports import allocate_ports
from startup.steps import prereqs, config, volumes, seed, seed_filestore, build, users, validate, schema_fixups, seed_cleanup

app = typer.Typer(
    name="startup",
    help="Set up and manage local NExtSEEK Docker installs.",
    no_args_is_help=True,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PORTS = {"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7687}


def _warn_if_proxy_token_empty(proxy_env_path: Path) -> None:
    if not read_env(proxy_env_path).get("AWS_BEARER_TOKEN_BEDROCK"):
        ui.warn(
            "AWS_BEARER_TOKEN_BEDROCK is EMPTY — CC model calls disabled until "
            "docker/bedrock-proxy/proxy-secret.env is filled and bedrock-proxy recreated"
        )


def _print_health_results(results: list[validate.HealthResult]) -> None:
    for r in results:
        printer = ui.warn if getattr(r, "warn", False) else (ui.ok if r.ok else ui.fail)
        printer(f"{r.name}: {r.detail}")


@app.command()
def install(
    instance: str | None = typer.Option(None, "--instance", help="Named instance for multi-install."),
    port_offset: int | None = typer.Option(None, "--port-offset", help="Add N to every default port."),
    no_seed: bool = typer.Option(False, "--no-seed", help="Skip seed import entirely (databases will start empty)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """First-time install: prereqs, config, volumes, seeds, build, users, validate."""
    ui.banner("NExtSEEK Startup")
    total = 9

    # [1/9] Prereqs
    ui.step(1, total, "Checking prerequisites")
    failed = [r for r in prereqs.run_all() if not r.ok]
    if failed:
        for r in failed:
            ui.fail(f"{r.name}: {r.detail}")
            if r.remediation:
                ui.remediation(r.remediation)
        raise typer.Exit(code=1)
    ui.ok("docker, compose, uv, disk all OK")

    # [2/9] chat_nextseek vendored
    ui.step(2, total, "Verifying vendored chat_nextseek/")
    if not (REPO_ROOT / "chat_nextseek" / "pyproject.toml").exists():
        ui.fail("chat_nextseek/ is missing or empty")
        ui.remediation("This repo ships chat_nextseek as a vendored directory; re-clone NExtSEEK")
        raise typer.Exit(code=1)
    ui.ok("chat_nextseek/ present")

    # [3/9] Resolve instance + ports
    ui.step(3, total, "Configuring instance")
    name = resolve_instance_name(REPO_ROOT, instance)
    existing = load_instance(REPO_ROOT)
    if existing and not yes:
        ui.warn(f"Existing install detected (instance={existing.name}). Continuing will re-run install.")

    if port_offset is not None:
        desired = {k: v + port_offset for k, v in DEFAULT_PORTS.items()}
    else:
        desired = dict(DEFAULT_PORTS)
    ports = allocate_ports(desired)
    prefix = "" if name == REPO_ROOT.name else f"{name}-"
    state = InstanceState(
        name=name,
        prefix=prefix,
        ports=ports,
        compose_project_name=f"nextseek{('-' + name) if prefix else ''}",
        created=datetime.datetime.now().astimezone().isoformat(),
    )

    # Show the install summary before any destructive action (config writes,
    # volume creation, seed import). The prompt accepts plain Y/n or one of a
    # few flag toggles so the user can adjust without aborting + re-running.
    # --yes skips the prompt but keeps the summary visible.
    def _print_summary() -> None:
        ui.console.print()
        ui.console.print("[bold]About to install with this configuration:[/bold]")
        ui.console.print(f"  Working directory   {REPO_ROOT}")
        ui.console.print(f"  Instance name       {name}")
        ui.console.print(f"  Volume prefix       {prefix or '(none)'}")
        ui.console.print(f"  Compose project     {state.compose_project_name}")
        ui.console.print("  Published ports:")
        ui.console.print(f"    NExtSEEK         http://localhost:{ports['nextseek']}")
        ui.console.print(f"    SEEK             http://localhost:{ports['seek']}")
        ui.console.print(f"    Neo4j HTTP       http://localhost:{ports['neo4j_http']}")
        ui.console.print(f"    Neo4j Bolt       [link=bolt://localhost:{ports['neo4j_bolt']}]bolt://localhost:{ports['neo4j_bolt']}[/link]")
        ui.console.print("  Default credentials (rotate after install per NExtSTEPS.md):")
        ui.console.print(f"    demo / demopassword   (admin)")
        ui.console.print(f"    user / userpassword   (regular)")
        ui.console.print(f"    neo4j / demopassword")
        if no_seed:
            ui.console.print(f"  Seed import         [yellow]SKIPPED (--no-seed)[/yellow]")
        else:
            ui.console.print(f"  Seed import         startup/seed/*.gz (skipped per-db if already populated)")
        ui.console.print()

    while True:
        _print_summary()
        if yes:
            break
        response = typer.prompt(
            "Proceed? [Y/n]  (or --no-seed to toggle the seed-skip flag, then re-prompt)",
            default="y",
        ).strip()
        lower = response.lower()
        if lower in ("", "y", "yes"):
            break
        if lower in ("n", "no"):
            raise typer.Abort()
        if response == "--no-seed":
            no_seed = not no_seed
            ui.ok(f"toggled: --no-seed = {no_seed}")
            continue
        ui.fail(f"unrecognized response {response!r}. valid: y, n, --no-seed")

    save_instance(REPO_ROOT, state)
    ui.ok(f"instance={name} prefix={prefix or '(none)'} ports={ports}")

    # [4/9] Config templates
    ui.step(4, total, "Writing config templates")
    values = config.default_values(nextseek_port=ports["nextseek"], seek_port=ports["seek"])
    config.render_db_env(REPO_ROOT, values)
    config.render_nextseek_env(REPO_ROOT, values)
    config.render_local_settings(REPO_ROOT, values)

    compose_env = state.compose_env()
    proxy_env_path = config.render_proxy_secret_env(REPO_ROOT)
    _warn_if_proxy_token_empty(proxy_env_path)
    config.render_root_env(REPO_ROOT, compose_env)
    ui.ok(
        "docker/db.env, docker/nextseek.env, "
        "docker/bedrock-proxy/proxy-secret.env, dmac/local_settings.py, .env"
    )

    # [5/9] Volumes
    ui.step(5, total, "Creating docker volumes")
    created = volumes.ensure_volumes(prefix)
    ui.ok(f"{len(created)} created, "
          f"{len(volumes.REQUIRED_VOLUMES) - len(created)} already existed")
    # G7-11 Task 13 (iter-1 M-1): bootstrap the `_staging` dir inside
    # dmac-cc-users ahead of Task 14's sidecar subpath mount -- no new
    # operator step, folded into the same install phase as the volumes above.
    volumes.ensure_cc_staging_dir(prefix)

    # [6/9] Seeds (gated on populated check, or skipped entirely with --no-seed)
    ui.step(6, total, "Importing seed databases")
    if no_seed:
        ui.warn("--no-seed: starting database containers only, no seed import")
        build.start_databases(REPO_ROOT, compose_env)
        ui.ok("databases up (empty — populate them yourself or re-run without --no-seed)")
    else:
        missing = seed.seed_files_present(REPO_ROOT)
        if missing:
            ui.fail(f"missing seed files: {', '.join(missing)}")
            raise typer.Exit(code=1)
        build.start_databases(REPO_ROOT, compose_env)
        if seed.mysql_db_is_populated("dmac", REPO_ROOT, compose_env):
            ui.ok("dmac already populated; skipping")
        else:
            with ui.spinner("loading dmac.sql.gz"):
                seed.load_mysql_dump(REPO_ROOT / "startup" / "seed" / "dmac.sql.gz", "dmac", REPO_ROOT, compose_env)
            ui.ok("dmac loaded")
        if seed.mysql_db_is_populated("seek_production", REPO_ROOT, compose_env):
            ui.ok("seek_production already populated; skipping")
        else:
            with ui.spinner("loading seek_production.sql.gz"):
                seed.load_mysql_dump(REPO_ROOT / "startup" / "seed" / "seek_production.sql.gz", "seek_production", REPO_ROOT, compose_env)
            ui.ok("seek_production loaded")
        if seed.neo4j_is_populated(values.neo4j_password, REPO_ROOT, compose_env):
            ui.ok("neo4j already populated; skipping")
        else:
            ui.info("neo4j.cypher.gz — bulk-loaded via UNWIND batches (~1-2 min)")
            ui.info(f"watch progress in the Neo4j browser at http://localhost:{ports['neo4j_http']}/")
            ui.info(f"  log in as neo4j / {values.neo4j_password}, then run:  MATCH (n) RETURN count(n);")
            with ui.spinner("loading neo4j.cypher.gz via UNWIND batches"):
                seed.load_neo4j_dump(REPO_ROOT / "startup" / "seed" / "neo4j.cypher.gz", values.neo4j_password, REPO_ROOT, compose_env)
            ui.ok("neo4j loaded")

    # Schema fixups: applied whether or not seeds ran, so re-installs over an
    # existing populated DB also self-heal. apply_all is idempotent + skips
    # entries whose target table doesn't exist (e.g., post --no-seed on a
    # fresh volume — there's nothing to fix up yet).
    for fqn, status in schema_fixups.apply_all(REPO_ROOT, compose_env):
        (ui.ok if status != "applied" else ui.warn)(f"schema fixup {fqn}: {status}")

    # [7/9] Build + start
    ui.step(7, total, "Building NExtSEEK image and starting the stack")
    with ui.spinner("building"):
        build.start_seek_side(REPO_ROOT, compose_env)
        build.build_and_start_nextseek(REPO_ROOT, compose_env)
        build.start_cc_stack(REPO_ROOT, compose_env)
    with ui.spinner(f"waiting for NExtSEEK to respond on :{ports['nextseek']} (gunicorn ~1-2 min to boot)"):
        build.wait_for_nextseek_http(ports["nextseek"])
    ui.ok("stack up")

    # Seed the SEEK filestore: the content blobs that seek_production.sql.gz's
    # metadata points at. Runs here (not in the [6/9] DB block) because it needs
    # the seek container up with its filestore dirs initialized — which happens
    # in start_seek_side() above. Gated like the DB seeds: skipped with
    # --no-seed, skipped if assets already exist. The ~215MB archive isn't in
    # git; if it's absent locally we download it from S3, and only warn-and-skip
    # if that download fails (so install still completes offline).
    if not no_seed:
        if seed_filestore.filestore_is_populated(REPO_ROOT, compose_env):
            ui.ok("SEEK filestore already populated; skipping")
        else:
            if not seed_filestore.archive_present(REPO_ROOT):
                try:
                    with ui.spinner("downloading filestore.tar.gz (~215MB) from S3"):
                        seed_filestore.download_archive(REPO_ROOT)
                    ui.ok("filestore.tar.gz downloaded")
                except Exception as exc:  # network/checksum — non-fatal, keep installing
                    ui.warn(f"could not download filestore archive ({exc}); skipping filestore seed")
            if seed_filestore.archive_present(REPO_ROOT):
                with ui.spinner("loading filestore.tar.gz into the seek volume"):
                    seed_filestore.load_filestore(REPO_ROOT, compose_env)
                ui.ok("SEEK filestore loaded")

    # Data cleanup: the dev seed brings along the dev user's chat history
    # which mostly shows up as untitled "New chat" placeholders. Clear those
    # so a fresh install presents an empty assistant sidebar.
    #
    # MUST run after phase 7. The 'title' column is added by Django migration
    # 0003_chatsession_title (real DDL AddField, not state-only) which runs
    # when the nextseek container starts — i.e., during phase 7. Running this
    # earlier hits MySQL error 1054 (Unknown column 'title') because the seed
    # dump's CREATE TABLE predates that migration.
    stale_chats = seed_cleanup.clear_stale_chat_sessions(REPO_ROOT, compose_env)
    if stale_chats > 0:
        ui.ok(f"cleared {stale_chats} stale chat session(s) with no title")

    # [8/9] Users
    ui.step(8, total, "Verifying test users")
    missing_users = users.verify_users_present(REPO_ROOT, compose_env)
    if missing_users:
        ui.warn(f"missing users: {', '.join(missing_users)} (seed dump should include them; investigate)")
    else:
        ui.ok("demo + user present")

    # [9/9] Validate
    ui.step(9, total, "Health checks")
    results = validate.run_all_health_checks(ports, REPO_ROOT, compose_env)
    failed_checks = [r for r in results if not r.ok]
    _print_health_results(results)
    if failed_checks:
        raise typer.Exit(code=1)

    ui.banner(f"Ready — http://localhost:{ports['nextseek']}/")
    ui.info("")
    ui.info("Next steps before this install goes beyond a private localhost demo:")
    ui.info("  → Read NExtSTEPS.md (top of the repo)")
    ui.info("  → At minimum: rotate demo/demopassword + user/userpassword via SEEK admin UI")
    ui.info(f"  → Log in: http://localhost:{ports['nextseek']}/  (demo/demopassword for admin)")


@app.command()
def doctor(
    instance: str | None = typer.Option(None, "--instance"),
) -> None:
    """Read-only diagnostic for an existing install."""
    from startup.steps.doctor import diagnose

    ui.banner("NExtSEEK Startup Doctor")
    results = diagnose(REPO_ROOT)
    any_failed = False
    for name, ok, detail in results:
        if ok:
            ui.ok(f"{name}: {detail}")
        else:
            ui.fail(f"{name}: {detail}")
            any_failed = True
    if any_failed:
        raise typer.Exit(code=1)


@app.command()
def reset(
    instance: str | None = typer.Option(None, "--instance"),
    keep_config: bool = typer.Option(False, "--keep-config"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Drop volumes and re-run install."""
    from startup.lib.docker_ops import compose_down

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance to reset — startup/.instance.json missing")
        raise typer.Exit(code=1)

    if not yes:
        ui.warn(f"This will DROP all data for instance '{state.name}' (volumes: {state.prefix}*)")
        typer.confirm("Continue?", abort=True)

    ui.step(1, 3, "Stopping containers and dropping volumes")
    compose_down(project_dir=REPO_ROOT, env=state.compose_env(), volumes=True)
    ui.ok("stack down, volumes dropped")

    if not keep_config:
        ui.step(2, 3, "Removing config files")
        for p in [
            REPO_ROOT / "docker" / "db.env",
            REPO_ROOT / "docker" / "nextseek.env",
            REPO_ROOT / "docker" / "bedrock-proxy" / "proxy-secret.env",
            REPO_ROOT / "dmac" / "local_settings.py",
            REPO_ROOT / ".env",
            REPO_ROOT / "startup" / ".instance.json",
        ]:
            if p.exists():
                p.unlink()
                ui.ok(f"removed {p.relative_to(REPO_ROOT)}")
    else:
        ui.step(2, 3, "Keeping config files (--keep-config)")

    ui.step(3, 3, "Re-running install")
    install(instance=instance, port_offset=None, yes=True)


@app.command()
def rebuild(
    instance: str | None = typer.Option(None, "--instance"),
    service: str = typer.Option("nextseek", "--service"),
) -> None:
    """Rebuild and restart a service without touching volumes."""
    from startup.lib.docker_ops import compose_up

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance found — run 'startup install' first")
        raise typer.Exit(code=1)

    ui.banner(f"Rebuilding {service} for instance {state.name}")
    with ui.spinner(f"rebuilding {service}"):
        compose_up(
            services=[service],
            project_dir=REPO_ROOT,
            env=state.compose_env(),
            build=True,
        )
    ui.ok(f"{service} rebuilt and restarted")


@app.command(name="seed-filestore")
def seed_filestore_cmd(
    instance: str | None = typer.Option(None, "--instance"),
    force: bool = typer.Option(False, "--force", help="Re-seed even if the filestore already has assets."),
) -> None:
    """Load startup/seed/filestore.tar.gz into the running seek container's filestore volume.

    Standalone counterpart to the filestore seeding that `install` does in phase
    7 — use it to (re)seed an already-running stack without a full reinstall.
    Downloads the archive from S3 if it isn't already present locally.
    """
    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance found — run 'startup install' first")
        raise typer.Exit(code=1)
    compose_env = state.compose_env()

    ui.banner("Seeding SEEK filestore")
    if not seed_filestore.archive_present(REPO_ROOT):
        try:
            with ui.spinner("downloading filestore.tar.gz (~215MB) from S3"):
                seed_filestore.download_archive(REPO_ROOT)
            ui.ok("filestore.tar.gz downloaded")
        except Exception as exc:
            ui.fail(f"could not download {seed_filestore.FILESTORE_ARCHIVE}: {exc}")
            ui.remediation(f"Fetch it manually:  curl -o {seed_filestore.FILESTORE_ARCHIVE} {seed_filestore.FILESTORE_URL}")
            raise typer.Exit(code=1)
    if not force and seed_filestore.filestore_is_populated(REPO_ROOT, compose_env):
        ui.ok("filestore already populated; nothing to do (pass --force to re-seed)")
        return
    with ui.spinner("loading filestore.tar.gz into the seek volume"):
        seed_filestore.load_filestore(REPO_ROOT, compose_env)
    ui.ok("SEEK filestore loaded")


@app.command(name="dump-db")
def dump_db(
    source: str = typer.Option("dev", "--source"),
) -> None:
    """Maintainer-only: regenerate seed dumps from a source DB."""
    import subprocess

    regen_dir = REPO_ROOT / "startup" / "seed" / "regenerate"
    env_file = regen_dir / "dump-source.env"
    if not env_file.exists():
        ui.fail(f"{env_file.relative_to(REPO_ROOT)} missing")
        ui.remediation("This command is maintainer-only. Copy dump-source.env.example and fill in real credentials.")
        raise typer.Exit(code=2)

    ui.banner(f"Regenerating seed dumps from {source}")

    ui.step(1, 2, "MySQL (dmac + seek_production)")
    subprocess.run([str(regen_dir / "dump_mysql.sh")], check=True)
    ui.ok("MySQL dumps written")

    ui.step(2, 2, "Neo4j")
    subprocess.run(
        ["uv", "run", "--project", "startup", "--group", "maintainer", "python", str(regen_dir / "dump_neo4j.py")],
        check=True,
        cwd=REPO_ROOT,
    )
    ui.ok("Neo4j dump written")


if __name__ == "__main__":
    app()
