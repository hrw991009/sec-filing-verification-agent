"""Tests for the Alembic migration configuration and revision graph."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parents[1]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic.ini"
MIGRATIONS_PATH = BACKEND_ROOT / "migrations"


def load_alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG_PATH))


def test_alembic_configuration_does_not_store_database_credentials() -> None:
    alembic_config = load_alembic_config()

    assert alembic_config.get_main_option("sqlalchemy.url") is None


def test_alembic_paths_are_independent_of_the_current_directory() -> None:
    alembic_config = load_alembic_config()
    dotenv_path = alembic_config.get_main_option("dotenv_path")
    script_directory = ScriptDirectory.from_config(alembic_config)

    assert dotenv_path is not None
    assert Path(dotenv_path).resolve() == REPOSITORY_ROOT / ".env"
    assert Path(script_directory.dir).resolve() == MIGRATIONS_PATH


def test_migration_history_has_exactly_one_head() -> None:
    script_directory = ScriptDirectory.from_config(load_alembic_config())
    heads = script_directory.get_heads()

    assert len(heads) == 1, f"Expected exactly one migration head, found: {heads}"
