from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.artifacts.models  # noqa: F401 - register artifact/task tables on Base.metadata
import app.broker.models  # noqa: F401 - register broker tables on Base.metadata
import app.memory.models  # noqa: F401 - register memory tables on Base.metadata
import app.selfhealing.models  # noqa: F401 - register self-healing tables on Base.metadata
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Runtime settings (env vars / .env) win over the alembic.ini fallback.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

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
