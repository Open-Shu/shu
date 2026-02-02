"""Prompt utility functions for Shu.

This module provides utilities for processing and cleaning prompts,
including handling reference/citation conflicts between prompts and KB settings.
"""

import re


def has_citation_instructions(prompt_content: str) -> bool:
    """Detect if prompt content contains citation or reference instructions.

    This function identifies prompts that include their own citation handling,
    which should disable system-level reference generation to avoid duplication.

    Args:
        prompt_content: The prompt content to analyze

    Returns:
        True if citation instructions are detected

    """
    if not prompt_content:
        return False

    # Patterns that indicate the prompt handles its own citations
    citation_patterns = [
        # Direct citation instructions
        r"[Ii]nclude\s+relevant\s+citations?\s*(?:and\s+(?:source\s+)?references?)?\s*(?:in\s+your\s+response)?\.?",
        r"[Ii]nclude\s+(?:source\s+)?references?\s*(?:and\s+citations?)?\s*(?:in\s+your\s+response)?\.?",
        r"[Cc]ite\s+(?:your\s+)?sources?\s*(?:appropriately|properly|when\s+available)?\.?",
        r"[Pp]rovide\s+(?:relevant\s+)?(?:citations?|references?)\s*(?:and\s+(?:sources?|references?))?\.?",
        r"[Mm]aintain\s+academic\s+rigor\s*(?:and\s+include\s+citations?)?\.?",
        # Reference section instructions
        r'[Aa]fter\s+your\s+(?:main\s+)?response,?\s*(?:you\s+)?(?:MUST\s+)?include\s+a?\s*"?[Rr]eferences?"?\s+section\.?',
        r"[Ee]nd\s+your\s+response\s+with\s+(?:a\s+)?(?:list\s+of\s+)?(?:sources?|references?)\.?",
        # Source attribution instructions
        r"[Aa]ttribute\s+(?:all\s+)?(?:information\s+)?to\s+(?:its\s+)?sources?\.?",
        r"[Aa]lways\s+(?:cite|reference)\s+(?:your\s+)?sources?\.?",
        # Academic-style citation instructions
        r"[Uu]se\s+(?:proper\s+)?(?:academic\s+)?citation\s+format\.?",
        r"[Ff]ollow\s+(?:standard\s+)?citation\s+(?:guidelines|practices)\.?",
        # Numbered reference patterns
        r"\[\d+\]|\(\d+\)",  # [1] or (1) style references
        r"references?\s*:\s*$",  # "References:" at end of prompt
    ]

    # Check each pattern
    return any(re.search(pattern, prompt_content, flags=re.IGNORECASE | re.MULTILINE) for pattern in citation_patterns)


def should_disable_system_references(prompt_content: str) -> bool:
    """Determine if system references should be disabled due to prompt-level citation handling.

    Args:
        prompt_content: The prompt content to check

    Returns:
        True if system references should be disabled

    """
    return has_citation_instructions(prompt_content)


def get_effective_reference_setting(kb_include_references: bool, prompt_content: str) -> tuple[bool, str]:
    """Determine the effective reference setting considering both KB config and prompt content.

    Args:
        kb_include_references: Whether KB is configured to include references
        prompt_content: The prompt content to analyze

    Returns:
        Tuple of (should_include_system_references, reason)

    """
    if not prompt_content:
        return kb_include_references, "default"

    if has_citation_instructions(prompt_content):
        return False, "prompt_handles_citations"

    return kb_include_references, "kb_setting"


def analyze_response_references(response_content: str, available_sources: list | None = None) -> dict:
    """Analyze an LLM response to detect existing references and citation patterns.

    Args:
        response_content: The LLM response content to analyze
        available_sources: List of available source metadata to check against

    Returns:
        Dictionary with analysis results including:
        - has_source_citations: Whether response mentions any available sources
        - cited_sources: List of sources mentioned in the response
        - citation_patterns: Types of citation patterns found
        - reference_section_indicators: List of potential reference section headers found

    """
    if not response_content:
        return {
            "has_source_citations": False,
            "cited_sources": [],
            "citation_patterns": [],
            "reference_section_indicators": [],
        }

    cited_sources = []
    citation_patterns = []
    reference_section_indicators = []

    # Look for various reference section headers (simplified patterns)
    reference_headers = [
        r"References\s*:",
        r"Sources\s*:",
        r"Resources\s*:",
        r"Bibliography\s*:",
        r"Further Reading\s*:",
        r"Additional Documents\s*:",
        r"See Also\s*:",
        r"Related Materials\s*:",
        r"Supporting Documents\s*:",
    ]

    for header_pattern in reference_headers:
        if re.search(header_pattern, response_content):
            reference_section_indicators.append(header_pattern)

    # If we have available sources, check if they're mentioned in the response
    if available_sources:
        response_lower = response_content.lower()

        for source in available_sources:
            source_title = source.get("document_title", "")
            source_url = source.get("source_url", "")

            # Check for exact title match
            if source_title and source_title.lower() in response_lower:
                cited_sources.append(source_title)
                continue

            # Check for partial title match (handle abbreviations)
            if source_title and len(source_title) > 10:  # Only for longer titles
                # Split title into words and check if most words are present
                title_words = [word.lower() for word in source_title.split() if len(word) > 3]
                if title_words:
                    matches = sum(1 for word in title_words if word in response_lower)
                    if matches >= len(title_words) * 0.6:  # 60% of words match
                        cited_sources.append(source_title)
                        continue

            # Check for URL match
            if source_url and source_url.lower() in response_lower:
                cited_sources.append(source_title or source_url)
                continue

            # Check for filename match (extract filename from title or URL)
            filename_patterns = [
                r"([^/\\]+\.(?:md|py|js|pdf|docx?|txt|html?|xlsx?|pptx?))",  # File extensions incl. md/py/js
                r"([A-Z][A-Za-z0-9_\-\s]+\.(?:md|py|js|pdf|docx?|txt|html?|xlsx?|pptx?))",  # Capitalized filenames incl. md/py/js
            ]

            for pattern in filename_patterns:
                matches = re.findall(pattern, source_title or "", re.IGNORECASE)
                for match in matches:
                    if match.lower() in response_lower:
                        cited_sources.append(source_title)
                        break

    # Look for citation patterns in the text
    if re.search(r"\[\d+\]", response_content):
        citation_patterns.append("numbered_brackets")
    if re.search(r"\(\d+\)", response_content):
        citation_patterns.append("numbered_parentheses")
    if re.search(r"according to|as stated in|from|source:|based on", response_content, re.IGNORECASE):
        citation_patterns.append("inline_mentions")

    # Look for markdown links
    if re.search(r"\[([^\]]+)\]\([^)]+\)", response_content):
        citation_patterns.append("markdown_links")

    # Look for bullet/numbered lists that might contain references
    if re.search(r"^\s*(?:[-*•]|\d+\.)\s*[A-Z]", response_content, re.MULTILINE):
        citation_patterns.append("list_format")

    return {
        "has_source_citations": len(cited_sources) > 0,
        "cited_sources": list(set(cited_sources)),  # Remove duplicates
        "citation_patterns": citation_patterns,
        "reference_section_indicators": reference_section_indicators,
    }


def should_add_system_references(
    response_content: str, available_sources: list, kb_include_references: bool = True
) -> tuple[bool, str, list]:
    """Determine if system references should be added to a response based on robust content analysis.

    Args:
        response_content: The LLM response content
        available_sources: List of source metadata dicts available for citation
        kb_include_references: Whether KB is configured to include references

    Returns:
        Tuple of (should_add_references, reason, missing_sources)

    """
    if not kb_include_references:
        return False, "kb_disabled", []

    if not available_sources:
        return False, "no_sources", []

    # Use the enhanced analysis that checks for actual source mentions
    analysis = analyze_response_references(response_content, available_sources)

    # If no sources are cited at all, add all system references
    if not analysis["has_source_citations"] and not analysis["citation_patterns"]:
        return True, "no_citations_found", available_sources

    # If some sources are cited, check which ones are missing
    if analysis["has_source_citations"]:
        cited_titles = {title.lower() for title in analysis["cited_sources"]}
        missing_sources = []

        for source in available_sources:
            source_title = source.get("document_title", "")
            if source_title and source_title.lower() not in cited_titles:
                # Double-check with partial matching
                is_cited = any(
                    source_title.lower() in cited.lower() or cited.lower() in source_title.lower()
                    for cited in analysis["cited_sources"]
                )
                if not is_cited:
                    missing_sources.append(source)

        if missing_sources:
            return True, "missing_sources", missing_sources
        return False, "complete_citations", []

    # If response has citation patterns but no actual source citations, add system references
    if analysis["citation_patterns"] and not analysis["has_source_citations"]:
        return True, "citations_without_sources", available_sources

    # If response has reference section indicators but no actual sources cited
    if analysis["reference_section_indicators"] and not analysis["has_source_citations"]:
        return True, "reference_section_without_sources", available_sources

    return False, "citations_present", []


def get_citation_conflict_info(prompt_content: str, kb_include_references: bool) -> dict | None:
    """Get information about citation conflicts and recommendations.

    Args:
        prompt_content: The prompt content to check
        kb_include_references: Whether KB has references enabled

    Returns:
        Dictionary with conflict info and recommendations, None if no conflict

    """
    if not prompt_content:
        return None

    prompt_has_citations = has_citation_instructions(prompt_content)

    if prompt_has_citations and kb_include_references:
        return {
            "has_conflict": True,
            "prompt_has_citations": True,
            "kb_has_references": True,
            "recommendation": "disable_system_references",
            "message": (
                "This prompt includes citation instructions. System references "
                "will be automatically disabled to prevent duplication. The prompt "
                "will handle citations directly in the response."
            ),
            "effective_setting": False,
        }
    if prompt_has_citations:
        return {
            "has_conflict": False,
            "prompt_has_citations": True,
            "kb_has_references": False,
            "recommendation": "keep_prompt_citations",
            "message": "This prompt handles citations directly.",
            "effective_setting": False,
        }
    if kb_include_references:
        return {
            "has_conflict": False,
            "prompt_has_citations": False,
            "kb_has_references": True,
            "recommendation": "use_system_references",
            "message": "System references will be automatically added.",
            "effective_setting": True,
        }

    return None
