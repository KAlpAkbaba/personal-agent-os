from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# EVERY module's models must be on Base.metadata. Autogenerate compares it
# against the database, so an unregistered module's tables look like tables to
# DROP. They are no longer listed here by hand (the list had drifted: 21 of 46
# modules on 2026-10-06): register_models() imports every app module named
# `models` or `*_models`, and tests/unit/test_alembic_env_registers_every_model.py
# pins that set to the tree. A new package needs no line here. The migrations
# remain authoritative for PostgreSQL-only constructs (CHECK constraints, the
# pgvector hnsw index, BigInteger identity): the ORM models stay portable so the
# service layer unit-tests on SQLite, which is why `alembic check` reports those
# as differences by design.
from app.config import get_settings
from app.models import Base
from app.registry import register_models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Runtime settings (env vars / .env) win over the alembic.ini fallback.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

register_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
