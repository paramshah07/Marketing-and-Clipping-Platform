from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

# pre_ping: after a Postgres restart, pooled connections are dead. Without it the api's next request fails,
# and a render that outlived the restart fails its final status write.
engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
# sync def tasks and the CLI (psycopg3 does both)
SyncSession = sessionmaker(create_engine(settings.DATABASE_URL, pool_pre_ping=True), expire_on_commit=False)
