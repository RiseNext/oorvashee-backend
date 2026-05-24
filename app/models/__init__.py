"""ORM model registry.

Every concrete model module MUST be imported here so that Alembic's
`Base.metadata` reflects all tables. Cycle 1+ will add real models below.
"""

from app.db.base import Base

__all__ = ["Base"]
