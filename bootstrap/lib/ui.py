"""Rich-console wrappers for consistent bootstrap UI."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def banner(title: str) -> None:
    console.print(Panel(title, expand=False, border_style="cyan"))


def step(n: int, total: int, label: str) -> None:
    console.print(f"[bold cyan][{n}/{total}][/bold cyan] {label}")


def info(message: str) -> None:
    console.print(f"       {message}")


def ok(message: str) -> None:
    console.print(f"       [green]✓[/green] {message}")


def warn(message: str) -> None:
    console.print(f"       [yellow]![/yellow] {message}")


def fail(message: str) -> None:
    console.print(f"       [red]✗[/red] {message}", style="red")


def remediation(message: str) -> None:
    console.print(f"       [dim]→ {message}[/dim]")


@contextmanager
def spinner(label: str) -> Iterator[None]:
    """Context manager that shows a spinner while the block runs."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(description=label, total=None)
        yield
