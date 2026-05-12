"""Parse user input (URL, arXiv ID, DOI, title) into a structured ParsedInput."""

from __future__ import annotations

import re
from urllib.parse import unquote

from ..models.schemas import ParsedInput

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# New-style arXiv ID: YYMM.NNNNN or YYMM.NNNNNvN  (e.g. 2501.12345, 2501.12345v2)
_NEW_STYLE_RE = re.compile(
    r"^(\d{4}\.\d{4,5})(?:v(\d+))?$",
)

# Old-style arXiv ID: category/YYMMNNN or category.YYMMNNN
# e.g. cs/0701001, math.GT/0309136, hep-th/9901001v2
_OLD_STYLE_RE = re.compile(
    r"^([a-z][a-z0-9-]*(?:\.[A-Z][A-Z0-9]*)?/(\d{2})(\d{2})(\d{3}))(?:v(\d+))?$",
)

# DOI that points to an arXiv paper
_ARXIV_DOI_RE = re.compile(
    r"^10\.48550/arXiv\.(\d{4}\.\d{4,5})(?:v(\d+))?$",
)

# arXiv URL patterns
# https://arxiv.org/abs/2501.12345v2  (new-style)
# https://arxiv.org/pdf/2501.12345
# http://arxiv.org/abs/cs/0701001     (old-style)
# https://arxiv.org/abs/math.GT/0309136v2
_ARXIV_URL_RE = re.compile(
    r"""^https?://(?:www\.)?arxiv\.org/"""
    r"""(?:abs|pdf|html|format|ps|src|tb|fp)/(.*?)"""
    r"""(?:\.pdf)?/?$""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_new_style(raw_id: str) -> str:
    """Normalize a new-style ID to canonical YYMM.NNNNN form.

    The new-style format is already canonical, so we just ensure consistent
    casing and strip whitespace.
    """
    return raw_id.strip().lower()


def _normalize_old_style(
    prefix: str,
    yy: str,
    mm: str,
    nnn: str,
) -> str:
    """Convert old-style ``category/YYMMNNN`` to canonical ``YYMM.NNNNN``.

    The canonical form zero-pads the sequence number to 5 digits and drops
    the category prefix.  For example ``cs/0701001`` becomes ``0701.00001``.
    """
    seq = f"{int(nnn):05d}"
    return f"{yy}{mm}.{seq}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_input(raw: str) -> ParsedInput:
    """Parse free-form user input into a structured ``ParsedInput``.

    Detection order (first match wins):
      1. arXiv URL
      2. arXiv DOI
      3. New-style arXiv ID
      4. Old-style arXiv ID
      5. Plain title
    """
    text = raw.strip()

    # --- 1. arXiv URL -------------------------------------------------------
    url_match = _ARXIV_URL_RE.match(text)
    if url_match:
        path_id = unquote(url_match.group(1)).strip()

        # Try to parse the path component as a new-style ID (may include version)
        ns = _NEW_STYLE_RE.match(path_id)
        if ns:
            return ParsedInput(
                raw_input=text,
                arxiv_id=_normalize_new_style(ns.group(1)),
                version=int(ns.group(2)) if ns.group(2) else None,
                input_type="url",
            )

        # Try old-style ID from the URL path
        os_match = _OLD_STYLE_RE.match(path_id)
        if os_match:
            return ParsedInput(
                raw_input=text,
                arxiv_id=_normalize_old_style(os_match.group(1), os_match.group(2), os_match.group(3), os_match.group(4)),
                version=int(os_match.group(5)) if os_match.group(5) else None,
                input_type="url",
            )

    # --- 2. arXiv DOI -------------------------------------------------------
    doi_match = _ARXIV_DOI_RE.match(text)
    if doi_match:
        return ParsedInput(
            raw_input=text,
            arxiv_id=_normalize_new_style(doi_match.group(1)),
            version=int(doi_match.group(2)) if doi_match.group(2) else None,
            doi=text,
            input_type="doi",
        )

    # Also handle a bare DOI that contains an arXiv reference but wasn't caught
    # by the specific pattern above (e.g. any DOI starting with 10.48550/arXiv.)
    if text.startswith("10."):
        return ParsedInput(
            raw_input=text,
            doi=text,
            input_type="doi",
        )

    # --- 3. New-style arXiv ID ----------------------------------------------
    # Accept "2501.12345 v3" (space before version) by collapsing spaces
    collapsed = re.sub(r"\s+v(\d+)$", r"v\1", text)
    ns = _NEW_STYLE_RE.match(collapsed)
    if ns:
        return ParsedInput(
            raw_input=text,
            arxiv_id=_normalize_new_style(ns.group(1)),
            version=int(ns.group(2)) if ns.group(2) else None,
            input_type="id",
        )

    # --- 4. Old-style arXiv ID ----------------------------------------------
    os_match = _OLD_STYLE_RE.match(collapsed)
    if os_match:
        return ParsedInput(
            raw_input=text,
            arxiv_id=_normalize_old_style(os_match.group(1), os_match.group(2), os_match.group(3), os_match.group(4)),
            version=int(os_match.group(5)) if os_match.group(5) else None,
            input_type="id",
        )

    # --- 5. Plain title -----------------------------------------------------
    return ParsedInput(
        raw_input=text,
        title_hint=text,
        input_type="title",
    )
