from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.config import get_settings

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_migrations_upgrade_to_head(engine):
    config = Config(toml_file=PYPROJECT)

    command.upgrade(config, "head")

    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head


def test_every_migration_can_be_undone(scratch_engine, monkeypatch):
    # migrations read the database from settings, so point those at the scratch database
    monkeypatch.setenv("DATABASE_URL", scratch_engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    config = Config(toml_file=PYPROJECT)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        with scratch_engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert connection.scalar(text("SELECT count(*) FROM pg_proc WHERE proname = 'reject_update'")) == 0
        # anything a downgrade left behind would make this fail
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
