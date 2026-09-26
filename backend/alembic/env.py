import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.models import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def include_name(name, type_, parent_names):
    # procrastinate_* tables belong to Procrastinate's own schema; never let autogenerate drop them
    return not (type_ == "table" and name.startswith("procrastinate_"))


def connect(engine, tries=30):
    # `docker compose restart` ignores depends_on, so postgres may still be starting (connection refused)
    for attempt in range(tries):
        try:
            return engine.connect()
        except OperationalError:
            if attempt == tries - 1:
                raise
            time.sleep(1)


engine = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
with connect(engine) as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()
