from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from industry_platform.core.config import Settings
from industry_platform.core.database import build_database_url
from industry_platform.model_registry import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    # Alembic can run inside pytest or another long-lived application process.
    # Preserve loggers that are not declared in alembic.ini instead of silently
    # disabling them for the rest of that process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def load_migration_settings() -> Settings:
    """Load migration settings independently of the current directory."""

    env_file = config.get_main_option("dotenv_path")
    return Settings(_env_file=env_file)


def run_migrations_offline() -> None:
    """Render migration SQL without opening a database connection."""

    database_url = build_database_url(load_migration_settings())

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations through a real synchronous database connection."""

    database_url = build_database_url(load_migration_settings())
    engine = create_engine(
        database_url,
        poolclass=NullPool,
    )

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
