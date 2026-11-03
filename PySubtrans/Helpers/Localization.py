"""
Passthrough localization helpers.

ikonsub ships no translation catalogs. `_()` and `tr()` mark user-facing strings
and return them unchanged, so a catalog can be added later without touching the
call sites.
"""


def _(text: str) -> str:
    """Return the user-facing string (no catalog is loaded, so it is unchanged)."""
    return text


def tr(context: str, text: str) -> str:
    """Return the user-facing string for a context (the context is ignored)."""
    return _(text)
