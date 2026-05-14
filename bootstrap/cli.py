"""NExtSEEK bootstrap CLI."""
from __future__ import annotations

import datetime
from pathlib import Path

import typer

from bootstrap.lib import ui
from bootstrap.lib.instance import (
    InstanceState,
    resolve_instance_name,
    load_instance,
    save_instance,
)
from bootstrap.lib.ports import allocate_ports
from bootstrap.steps import prereqs, config, volumes, seed, build, users, validate

app = typer.Typer(
    name="bootstrap",
    help="Set up and manage local NExtSEEK Docker installs.",
    no_args_is_help=True,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PORTS = {"nextseek": 8000, "seek": 3000, "neo4j_http": 7474, "neo4j_bolt": 7688}


@app.command()
def install(
    instance: str | None = typer.Option(None, "--instance", help="Named instance for multi-install."),
    port_offset: int | None = typer.Option(None, "--port-offset", help="Add N to every default port."),
    no_seed: bool = typer.Option(False, "--no-seed", help="Skip seed import entirely (databases will start empty)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """First-time install: prereqs, config, volumes, seeds, build, users, validate."""
    ui.banner("NExtSEEK Bootstrap")
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
        ui.console.print(f"    Neo4j Bolt       bolt://localhost:{ports['neo4j_bolt']}")
        ui.console.print("  Default credentials (rotate after install per NExtSTEPS.md):")
        ui.console.print(f"    demo / demopassword   (admin)")
        ui.console.print(f"    user / userpassword   (regular)")
        ui.console.print(f"    neo4j / demopassword")
        if no_seed:
            ui.console.print(f"  Seed import         [yellow]SKIPPED (--no-seed)[/yellow]")
        else:
            ui.console.print(f"  Seed import         bootstrap/seed/*.gz (skipped per-db if already populated)")
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
    values = config.default_values(nextseek_port=ports["nextseek"])
    config.render_db_env(REPO_ROOT, values)
    config.render_nextseek_env(REPO_ROOT, values)
    config.render_local_settings(REPO_ROOT, values)
    ui.ok("docker/db.env, docker/nextseek.env, dmac/local_settings.py")

    compose_env = state.compose_env()

    # [5/9] Volumes
    ui.step(5, total, "Creating docker volumes")
    created = volumes.ensure_volumes(prefix)
    ui.ok(f"{len(created)} created, {6 - len(created)} already existed")

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
                seed.load_mysql_dump(REPO_ROOT / "bootstrap" / "seed" / "dmac.sql.gz", "dmac", REPO_ROOT, compose_env)
            ui.ok("dmac loaded")
        if seed.mysql_db_is_populated("seek_production", REPO_ROOT, compose_env):
            ui.ok("seek_production already populated; skipping")
        else:
            with ui.spinner("loading seek_production.sql.gz"):
                seed.load_mysql_dump(REPO_ROOT / "bootstrap" / "seed" / "seek_production.sql.gz", "seek_production", REPO_ROOT, compose_env)
            ui.ok("seek_production loaded")
        if seed.neo4j_is_populated(values.neo4j_password, REPO_ROOT, compose_env):
            ui.ok("neo4j already populated; skipping")
        else:
            ui.info("neo4j.cypher.gz is ~674k CREATE statements — this takes 30-60 min on first install")
            ui.info(f"watch progress in the Neo4j browser at http://localhost:{ports['neo4j_http']}/")
            ui.info(f"  log in as neo4j / {values.neo4j_password}, then run:  MATCH (n) RETURN count(n);")
            with ui.spinner("loading neo4j.cypher.gz (long-running; safe to leave unattended)"):
                seed.load_neo4j_dump(REPO_ROOT / "bootstrap" / "seed" / "neo4j.cypher.gz", values.neo4j_password, REPO_ROOT, compose_env)
            ui.ok("neo4j loaded")

    # [7/9] Build + start
    ui.step(7, total, "Building NExtSEEK image and starting the stack")
    with ui.spinner("building"):
        build.start_seek_side(REPO_ROOT, compose_env)
        build.build_and_start_nextseek(REPO_ROOT, compose_env)
    ui.ok("stack up")

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
    for r in results:
        (ui.ok if r.ok else ui.fail)(f"{r.name}: {r.detail}")
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
    from bootstrap.steps.doctor import diagnose

    ui.banner("NExtSEEK Bootstrap Doctor")
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
    from bootstrap.lib.docker_ops import compose_down

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance to reset — bootstrap/.instance.json missing")
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
            REPO_ROOT / "dmac" / "local_settings.py",
            REPO_ROOT / "bootstrap" / ".instance.json",
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
    from bootstrap.lib.docker_ops import compose_up

    state = load_instance(REPO_ROOT)
    if state is None:
        ui.fail("no instance found — run 'bootstrap install' first")
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


@app.command(name="dump-db")
def dump_db(
    source: str = typer.Option("dev", "--source"),
) -> None:
    """Maintainer-only: regenerate seed dumps from a source DB."""
    import subprocess

    regen_dir = REPO_ROOT / "bootstrap" / "seed" / "regenerate"
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
        ["uv", "run", "--project", "bootstrap", "--group", "maintainer", "python", str(regen_dir / "dump_neo4j.py")],
        check=True,
        cwd=REPO_ROOT,
    )
    ui.ok("Neo4j dump written")


if __name__ == "__main__":
    app()
