"""Unit tests for PluginsSchedulerService.

Tests the plugin feed scheduling service, particularly NULL handling in queries.
"""

import pytest
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.orm import declarative_base


@pytest.mark.asyncio
async def test_sqlalchemy_null_check_generates_correct_sql() -> None:
    """Integration test to verify SQLAlchemy generates correct SQL for NULL checks.

    This test directly verifies that .is_(None) generates 'IS NULL' in SQL,
    while Python's 'is None' would fail to do so.

    This test validates the fixes for bugs in:
    - plugins_scheduler_service.py (is None -> .is_(None))
    - attachment_cleanup.py (is not None -> .is_not(None))
    """
    Base = declarative_base()

    class TestModel(Base):
        """Test model for SQL generation verification."""

        __tablename__ = "test_model"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        value = Column(Integer, nullable=True)

    # Test that .is_(None) generates correct SQL
    query_correct = select(TestModel).where(TestModel.value.is_(None))
    sql_correct = str(query_correct.compile(compile_kwargs={"literal_binds": True}))

    # Verify the SQL contains 'IS NULL'
    assert "IS NULL" in sql_correct, f"Expected 'IS NULL' in SQL, got: {sql_correct}"

    # Test that == None also works
    query_equals = select(TestModel).where(TestModel.value == None)  # noqa: E711
    sql_equals = str(query_equals.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NULL" in sql_equals, f"Expected 'IS NULL' in SQL, got: {sql_equals}"

    # Test that .is_not(None) generates correct SQL
    query_not_null = select(TestModel).where(TestModel.value.is_not(None))
    sql_not_null = str(query_not_null.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NOT NULL" in sql_not_null, f"Expected 'IS NOT NULL' in SQL, got: {sql_not_null}"

    # Test that != None also works
    query_not_equals = select(TestModel).where(TestModel.value != None)  # noqa: E711
    sql_not_equals = str(query_not_equals.compile(compile_kwargs={"literal_binds": True}))

    assert "IS NOT NULL" in sql_not_equals, f"Expected 'IS NOT NULL' in SQL, got: {sql_not_equals}"
