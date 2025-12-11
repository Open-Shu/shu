"""
Processors package for Shu RAG Backend.

This package contains document processors for different source types
and text processing operations.
"""

from .text_extractor import TextExtractor

__all__ = [
    "TextExtractor",
]