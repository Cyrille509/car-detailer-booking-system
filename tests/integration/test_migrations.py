from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_migrations_upgrade_to_head(engine):
    config = Config(toml_file=PYPROJECT)

    command.upgrade(config, "head")

    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head
