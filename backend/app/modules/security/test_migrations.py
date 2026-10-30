import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import settings


EXPECTED_HEAD = "0002_security_nullable"


def migration_config(database_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    backend_root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path.as_posix()}")
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    return config


def metric_nullability(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        columns = connection.execute("PRAGMA table_info(kavach_events)").fetchall()
    return {row[1]: row[3] for row in columns if row[1] in {"situation_risk", "network_health"}}


def test_migration_is_single_head_child_of_existing_initial_revision(tmp_path: Path, monkeypatch):
    config = migration_config(tmp_path / "chain.sqlite", monkeypatch)
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [EXPECTED_HEAD]
    assert script.get_revision(EXPECTED_HEAD).down_revision == "0001_initial"


def test_fresh_database_upgrades_to_head_with_nullable_metrics(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "fresh.sqlite"
    config = migration_config(database_path, monkeypatch)

    command.upgrade(config, "head")

    assert metric_nullability(database_path) == {"situation_risk": 0, "network_health": 0}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == EXPECTED_HEAD


def test_legacy_revision_upgrade_preserves_values_and_downgrade_refuses_null_rows(
    tmp_path: Path, monkeypatch
):
    database_path = tmp_path / "legacy.sqlite"
    config = migration_config(database_path, monkeypatch)

    # Model a database created by the pre-change 0001 schema, before stamping
    # that exact revision and upgrading through the new migration.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE kavach_events ("
            "event_id VARCHAR(36) PRIMARY KEY, "
            "timestamp DATETIME NOT NULL, "
            "source_module VARCHAR(20) NOT NULL, "
            "situation_risk FLOAT NOT NULL, "
            "network_health FLOAT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO kavach_events VALUES (?, ?, ?, ?, ?)",
            ("legacy-event", "2026-01-01T00:00:00+00:00", "EDGE_AI", 0.37, 91.5),
        )

    command.stamp(config, "0001_initial")
    command.upgrade(config, "head")

    assert metric_nullability(database_path) == {"situation_risk": 0, "network_health": 0}
    with sqlite3.connect(database_path) as connection:
        legacy_values = connection.execute(
            "SELECT situation_risk, network_health FROM kavach_events WHERE event_id=?",
            ("legacy-event",),
        ).fetchone()
        assert legacy_values == (0.37, 91.5)
        connection.execute(
            "INSERT INTO kavach_events VALUES (?, ?, ?, ?, ?)",
            ("security-event", "2026-01-02T00:00:00+00:00", "SECURITY", None, None),
        )

    with pytest.raises(RuntimeError, match="Cannot downgrade while event rows have NULL"):
        command.downgrade(config, "0001_initial")

    assert metric_nullability(database_path) == {"situation_risk": 0, "network_health": 0}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == EXPECTED_HEAD
        assert connection.execute(
            "SELECT situation_risk, network_health FROM kavach_events WHERE event_id=?",
            ("legacy-event",),
        ).fetchone() == (0.37, 91.5)
        connection.execute("DELETE FROM kavach_events WHERE event_id=?", ("security-event",))

    command.downgrade(config, "0001_initial")

    assert metric_nullability(database_path) == {"situation_risk": 1, "network_health": 1}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_initial"
        assert connection.execute(
            "SELECT situation_risk, network_health FROM kavach_events WHERE event_id=?",
            ("legacy-event",),
        ).fetchone() == (0.37, 91.5)
