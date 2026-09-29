from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# pre_ping: after a Postgres restart, pooled connections are dead. Without it the api's next request fails,
# and a render that outlived the restart fails its final status write.
engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
# sync def tasks and the CLI (psycopg3 does both)
SyncSession = sessionmaker(create_engine(settings.DATABASE_URL, pool_pre_ping=True), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _set_uid(session, transaction, connection) -> None:
    """A session made with info={"uid": ...} (the api's, per request) runs every transaction as that user:
    row-level security and the user_id defaults read app.uid. Transaction-local, so it never outlives the
    transaction on a pooled connection; a session without one sees no tenant rows as clipper_app."""
    if (uid := session.info.get("uid")) is not None:
        connection.exec_driver_sql("SELECT set_config('app.uid', %s, true)", (str(uid),))
