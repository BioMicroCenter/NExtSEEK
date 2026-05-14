"""Port-availability helpers."""
from __future__ import annotations

import socket


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if a TCP server can bind to (host, port) right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(starting_at: int, max_attempts: int = 200) -> int:
    """Return the first free port >= starting_at. Raises after max_attempts."""
    for offset in range(max_attempts):
        candidate = starting_at + offset
        if is_port_free(candidate):
            return candidate
    raise RuntimeError(
        f"No free port found in range {starting_at}..{starting_at + max_attempts}"
    )


def allocate_ports(desired: dict[str, int]) -> dict[str, int]:
    """Allocate free ports for each service. Skips collisions and inter-service duplicates."""
    result: dict[str, int] = {}
    claimed: set[int] = set()
    for name, start in desired.items():
        candidate = start
        while True:
            if candidate not in claimed and is_port_free(candidate):
                result[name] = candidate
                claimed.add(candidate)
                break
            candidate += 1
            if candidate - start > 200:
                raise RuntimeError(f"No free port for service {name} starting at {start}")
    return result
