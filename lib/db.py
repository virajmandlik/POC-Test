"""
MongoDB connection management.

Provides a lazy singleton `get_db()` that returns the pymongo Database object.
Collections are accessed via `get_db()["collection_name"]`.
"""

import logging
from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from lib.config import cfg

log = logging.getLogger("f4f.db")


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    log.info("Connecting to MongoDB at %s ...", cfg.MONGO_URI)
    client = MongoClient(cfg.MONGO_URI, serverSelectionTimeoutMS=5000)
    # Force connection test on first call so we fail fast
    client.admin.command("ping")
    log.info("MongoDB connected — database: %s", cfg.MONGO_DB)
    return client


def get_db() -> Database:
    """Return the application database. Creates indexes on first call."""
    db = _client()[cfg.MONGO_DB]
    _ensure_indexes(db)
    return db


_indexes_created = False


def _ensure_indexes(db: Database) -> None:
    global _indexes_created
    if _indexes_created:
        return

    # Jobs collection indexes
    jobs = db["jobs"]
    jobs.create_index("status")
    jobs.create_index("created_at")
    jobs.create_index([("status", 1), ("created_at", -1)])
    jobs.create_index("job_type")

    # Audit log indexes
    audit = db["audit_logs"]
    audit.create_index("timestamp")
    audit.create_index("action")
    audit.create_index("user")
    audit.create_index([("timestamp", -1)])

    _indexes_created = True
    log.info("MongoDB indexes ensured")
