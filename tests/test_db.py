"""Tests for core.db.connect — busy_timeout (U3 / R11).

busy_timeout bounds the lock-WAIT, not transaction duration: a writer that
holds a transaction longer than busy_timeout still raises OperationalError
(documented as the known limit, see test_busy_timeout_does_not_extend_*).
"""

import sqlite3
import threading
import time

import pytest

from core.db import connect

_SCHEMA = "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)"


def test_busy_timeout_is_5000(tmp_path):
    db = tmp_path / "x.sqlite"
    with connect(str(db), _SCHEMA) as conn:
        (value,) = conn.execute("PRAGMA busy_timeout").fetchone()
    assert value == 5000


def test_busy_timeout_set_on_every_connection(tmp_path):
    db = tmp_path / "x.sqlite"
    for _ in range(2):
        with connect(str(db), _SCHEMA) as conn:
            (value,) = conn.execute("PRAGMA busy_timeout").fetchone()
            assert value == 5000


def test_second_writer_waits_then_succeeds(tmp_path):
    """Overlapping writers: the second waits (within busy_timeout) and
    succeeds rather than failing instantly with OperationalError."""
    db = tmp_path / "x.sqlite"
    # Initialise the schema once.
    with connect(str(db), _SCHEMA):
        pass

    hold = 0.3  # < 5s busy_timeout, so the waiter should win
    errors: list[Exception] = []
    started = threading.Event()

    def writer_holding_lock():
        with connect(str(db), _SCHEMA) as conn:
            conn.execute("INSERT INTO t (v) VALUES ('a')")
            started.set()
            time.sleep(hold)  # keep the write lock open

    def writer_waiting():
        started.wait()
        time.sleep(0.02)  # ensure we attempt the write while lock is held
        try:
            with connect(str(db), _SCHEMA) as conn:
                conn.execute("INSERT INTO t (v) VALUES ('b')")
        except Exception as exc:  # noqa: BLE001 - record for assertion
            errors.append(exc)

    t1 = threading.Thread(target=writer_holding_lock)
    t2 = threading.Thread(target=writer_waiting)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"second writer should have waited, got {errors!r}"
    with connect(str(db), _SCHEMA) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM t").fetchone()
    assert n == 2


def test_busy_timeout_does_not_extend_transaction_limit(tmp_path):
    """Known limit (data-integrity F6): busy_timeout bounds lock-WAIT only.

    A waiter using a SHORTER per-connection timeout than the lock-holder's
    transaction still raises OperationalError — proving busy_timeout caps the
    wait, it does not make the holder release sooner.
    """
    db = tmp_path / "x.sqlite"
    with connect(str(db), _SCHEMA):
        pass

    holder = sqlite3.connect(str(db))
    holder.execute("PRAGMA busy_timeout=5000")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t (v) VALUES ('held')")
    try:
        waiter = sqlite3.connect(str(db))
        waiter.execute("PRAGMA busy_timeout=50")  # shorter than holder keeps it
        with pytest.raises(sqlite3.OperationalError):
            waiter.execute("BEGIN IMMEDIATE")
            waiter.execute("INSERT INTO t (v) VALUES ('wait')")
        waiter.close()
    finally:
        holder.rollback()
        holder.close()
