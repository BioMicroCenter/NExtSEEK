"""Tests for startup.lib.ports."""
from __future__ import annotations

import socket

import pytest

from startup.lib.ports import is_port_free, find_free_port, allocate_ports


def test_is_port_free_for_clearly_free_port() -> None:
    # 0 means "let the OS pick"; bind once, then check the picked port is busy.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        bound_port = s.getsockname()[1]
        assert not is_port_free(bound_port)


def test_is_port_free_returns_true_for_unbound_port() -> None:
    # Use a high random port unlikely to be in use.
    assert is_port_free(54321) is True or is_port_free(54322) is True


def test_find_free_port_returns_starting_port_if_free() -> None:
    # Pick a high port that's almost certainly free.
    port = find_free_port(54321)
    assert port >= 54321
    assert is_port_free(port)


def test_find_free_port_walks_past_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        occupied = s.getsockname()[1]
        # Find a free port starting from the occupied one — should skip past it.
        free = find_free_port(occupied)
        assert free != occupied
        assert free > occupied


def test_allocate_ports_returns_dict_with_all_free() -> None:
    desired = {"a": 54330, "b": 54331, "c": 54332, "d": 54333}
    result = allocate_ports(desired)
    assert set(result.keys()) == set(desired.keys())
    for port in result.values():
        assert is_port_free(port)


def test_allocate_ports_skips_collisions() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        result = allocate_ports({"a": busy})
        assert result["a"] != busy
        assert is_port_free(result["a"])


def test_allocate_ports_avoids_duplicates_in_one_call() -> None:
    # Two services asking for the same starting port should not collide with each other.
    result = allocate_ports({"a": 54400, "b": 54400})
    assert result["a"] != result["b"]
