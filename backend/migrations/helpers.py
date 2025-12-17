"""
Shared helper functions for Alembic migrations.

These helpers enable idempotent migrations by checking existence before
creating/dropping schema objects.
"""

from typing import Any
import sqlalchemy as sa
from alembic import op


def column_exists(inspector: Any, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table to check
        column_name: Name of the column to check for
        
    Returns:
        True if the column exists, False otherwise
    """
    try:
        return any(col["name"] == column_name for col in inspector.get_columns(table_name))
    except Exception:
        return False


def table_exists(inspector: Any, table_name: str) -> bool:
    """Check if a table exists in the database.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table to check
        
    Returns:
        True if the table exists, False otherwise
    """
    return table_name in inspector.get_table_names()


def index_exists(inspector: Any, table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table containing the index
        index_name: Name of the index to check for
        
    Returns:
        True if the index exists, False otherwise
    """
    try:
        indexes = inspector.get_indexes(table_name)
        return any(idx["name"] == index_name for idx in indexes)
    except Exception:
        return False


def drop_column_if_exists(inspector: Any, table_name: str, column_name: str) -> None:
    """Drop a column if it exists.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table containing the column
        column_name: Name of the column to drop
    """
    if column_exists(inspector, table_name, column_name):
        op.drop_column(table_name, column_name)


def drop_table_if_exists(inspector: Any, table_name: str) -> None:
    """Drop a table if it exists.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table to drop
    """
    if table_exists(inspector, table_name):
        op.drop_table(table_name)


def add_column_if_not_exists(
    inspector: Any,
    table_name: str,
    column: sa.Column,
) -> None:
    """Add a column if it doesn't already exist.
    
    Args:
        inspector: SQLAlchemy Inspector instance
        table_name: Name of the table to add the column to
        column: SQLAlchemy Column object to add
    """
    if not column_exists(inspector, table_name, column.name):
        op.add_column(table_name, column)

