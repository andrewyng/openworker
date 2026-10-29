"""Registrable domains for the reviewer's "destination named" fact (OPE-235).

Naming `herokuapp.com` must not name every app hosted on it, and naming `myapp.herokuapp.com`
must still name that app. The Public Suffix List says where the line falls: a public suffix
is a name under which unrelated parties register sites. Only its PRIVATE section (hosting
platforms) is vendored, in `_psl_private.py`; every single-label top-level domain also counts
as a suffix (the PSL's implicit `*` rule).

Not covered: multi-label suffixes from the list's ICANN section (`co.uk`, `com.au`). Naming
one of those on its own still names the sites under it.
"""

from __future__ import annotations

from ._psl_private import RULES


def _normal(domain: str) -> str:
    return domain.strip().strip(".").lower()


def is_public_suffix(domain: str) -> bool:
    """Whether `domain` is a name under which unrelated parties host sites."""
    domain = _normal(domain)
    if not domain:
        return False
    if "." not in domain:
        return True  # every top-level domain
    if "!" + domain in RULES:
        return False
    if domain in RULES:
        return True
    parent = domain.split(".", 1)[1]
    return "*." + parent in RULES


def registrable_domain(host: str) -> str:
    """The shortest name under a public suffix that `host` belongs to, or "" when `host`
    is itself a public suffix (or empty)."""
    labels = _normal(host).split(".")
    if not labels[0]:
        return ""
    # The longest public suffix of the host, plus one label.
    for i in range(len(labels)):
        if is_public_suffix(".".join(labels[i:])):
            return ".".join(labels[i - 1:]) if i > 0 else ""
    return ""
