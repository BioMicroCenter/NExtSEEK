"""NExtSEEK bootstrap CLI."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="bootstrap",
    help="Set up and manage local NExtSEEK Docker installs.",
    no_args_is_help=True,
)


@app.command()
def install(
    instance: str | None = typer.Option(None, "--instance", help="Named instance for multi-install."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
) -> None:
    """First-time install: prereqs, config, volumes, seeds, build, users, validate."""
    typer.echo("install: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def doctor(
    instance: str | None = typer.Option(None, "--instance"),
) -> None:
    """Diagnose the running install."""
    typer.echo("doctor: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def reset(
    instance: str | None = typer.Option(None, "--instance"),
    keep_config: bool = typer.Option(False, "--keep-config"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Drop volumes and re-run install."""
    typer.echo("reset: not yet implemented")
    raise typer.Exit(code=1)


@app.command()
def rebuild(
    instance: str | None = typer.Option(None, "--instance"),
    service: str = typer.Option("nextseek", "--service"),
) -> None:
    """Rebuild and restart one or more services without touching volumes."""
    typer.echo("rebuild: not yet implemented")
    raise typer.Exit(code=1)


@app.command(name="seed-users")
def seed_users(instance: str | None = typer.Option(None, "--instance")) -> None:
    """Idempotent: ensure demo + user accounts exist in SEEK."""
    typer.echo("seed-users: not yet implemented")
    raise typer.Exit(code=1)


@app.command(name="dump-db")
def dump_db(
    source: str = typer.Option("dev", "--source"),
    target: str | None = typer.Option(None, "--target"),
) -> None:
    """Maintainer-only: regenerate seed dumps from a source DB."""
    typer.echo("dump-db: not yet implemented")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
