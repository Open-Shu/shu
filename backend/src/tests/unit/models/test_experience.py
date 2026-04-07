"""
Unit tests for ExperienceDependency model.

Kept minimal -- only tests behavior, not schema declarations.
"""

from shu.models.experience import ExperienceDependency


class TestExperienceDependency:
    """Tests for the ExperienceDependency association model."""

    def test_repr_includes_both_ids(self):
        """__repr__ surfaces both FK ids for debugging."""
        dep = ExperienceDependency(
            aggregate_experience_id="agg-1",
            dependency_experience_id="dep-1",
        )
        r = repr(dep)
        assert "agg-1" in r
        assert "dep-1" in r
