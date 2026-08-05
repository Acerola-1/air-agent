"""Checkpointer 初始化缓存测试."""

from __future__ import annotations

from common.config import checkpointing


class CloseableConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CloseableCheckpointer:
    def __init__(self) -> None:
        self.conn = CloseableConnection()


def test_get_checkpointer_reuses_first_successful_instance(monkeypatch) -> None:
    checkpointing.reset_checkpointer_cache()
    calls = 0
    sentinel = object()

    def fake_create_checkpointer():
        nonlocal calls
        calls += 1
        return sentinel

    monkeypatch.setattr(checkpointing, "_create_checkpointer", fake_create_checkpointer)

    assert checkpointing.get_checkpointer() is sentinel
    assert checkpointing.get_checkpointer() is sentinel
    assert calls == 1


def test_get_checkpointer_does_not_cache_failure(monkeypatch) -> None:
    checkpointing.reset_checkpointer_cache()
    calls = 0
    sentinel = object()

    def fake_create_checkpointer():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("pg unavailable")
        return sentinel

    monkeypatch.setattr(checkpointing, "_create_checkpointer", fake_create_checkpointer)

    try:
        checkpointing.get_checkpointer()
    except RuntimeError:
        pass

    assert checkpointing.get_checkpointer() is sentinel
    assert calls == 2


def test_reset_checkpointer_cache_closes_previous_connection(monkeypatch) -> None:
    checkpointing.reset_checkpointer_cache()
    checkpointer = CloseableCheckpointer()
    monkeypatch.setattr(checkpointing, "_create_checkpointer", lambda: checkpointer)

    assert checkpointing.get_checkpointer() is checkpointer
    checkpointing.reset_checkpointer_cache()

    assert checkpointer.conn.closed is True
