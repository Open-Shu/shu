"""Text utilities for normalization and folding."""

# Unicode → ASCII folding table for keyword search compatibility.
# LLMs and word processors commonly substitute these lookalikes for ASCII.
# Exact-match surfaces (JSONB ?| operator) require both sides to agree on encoding.
_UNICODE_TO_ASCII = str.maketrans(
    {
        # Hyphens / dashes → ASCII hyphen-minus (U+002D)
        "\u2010": "-",  # HYPHEN
        "\u2011": "-",  # NON-BREAKING HYPHEN
        "\u2012": "-",  # FIGURE DASH
        "\u2013": "-",  # EN DASH
        "\u2014": "-",  # EM DASH
        "\u2212": "-",  # MINUS SIGN
        "\ufe58": "-",  # SMALL EM DASH
        "\ufe63": "-",  # SMALL HYPHEN-MINUS
        "\uff0d": "-",  # FULLWIDTH HYPHEN-MINUS
        # Single quotes → ASCII apostrophe (U+0027)
        "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
        "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
        "\u201a": "'",  # SINGLE LOW-9 QUOTATION MARK
        "\u2039": "'",  # SINGLE LEFT-POINTING ANGLE QUOTATION MARK
        "\u203a": "'",  # SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
        # Double quotes → ASCII quotation mark (U+0022)
        "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
        "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
        "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK
        # Spaces → ASCII space (U+0020)
        "\u00a0": " ",  # NO-BREAK SPACE
        "\u2007": " ",  # FIGURE SPACE
        "\u202f": " ",  # NARROW NO-BREAK SPACE
        # Ellipsis
        "\u2026": "...",  # HORIZONTAL ELLIPSIS
    }
)


def fold_unicode_to_ascii(text: str) -> str:
    """Fold common Unicode lookalikes to their ASCII equivalents.

    LLMs and word processors frequently substitute characters like non-breaking
    hyphens (U+2011), curly quotes, and non-breaking spaces for their ASCII
    counterparts. This breaks exact-match operations like PostgreSQL's JSONB ?|
    operator. Apply this before storing or querying keywords.
    """
    return text.translate(_UNICODE_TO_ASCII)
