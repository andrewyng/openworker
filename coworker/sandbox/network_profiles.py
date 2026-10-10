"""Where an agent's commands may connect, in OUR words. Every provider renders these into
its own mechanism: OpenShell into `network_policies`, Seatbelt and Windows into the
allow-list proxy.

Two profiles (UX-053 v6): `allowlist`, where only the sites the machine ticked are
reachable (`sandbox_network_hosts`, "host:port"; empty until the user ticks some), and
`open`, with no list at all: files stay isolated, the network is the machine's own
(ruling of 2026-09-25). `open` is the default on Windows for now. `SITES` is the catalogue
the page offers in groups; ticking one puts it on the machine's list.
"""

from __future__ import annotations

import sys

CODE_HOSTS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com", "raw.githubusercontent.com", "gitlab.com"]
PACKAGE_REGISTRIES = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "crates.io", "static.crates.io", "index.crates.io", "proxy.golang.org", "sum.golang.org"]
SEARCH_APIS = ["api.search.brave.com", "api.tavily.com", "html.duckduckgo.com", "duckduckgo.com"]

# The page's catalogue, by group. None is allowed until ticked.
SITES: dict[str, list[str]] = {"code-hosts": CODE_HOSTS, "package-registries": PACKAGE_REGISTRIES, "search-apis": SEARCH_APIS}

# name of the group -> hosts, per profile. Neither profile carries hosts of its own: the
# allow list is the machine's, and `open` has none.
PROFILES: dict[str, dict[str, list[str]]] = {"allowlist": {}, "open": {}}
ALLOWLIST = "allowlist"
OPEN = "open"
DEFAULT_PROFILE = ALLOWLIST


def default_profile(platform: str = sys.platform) -> str:
    """The profile a machine uses until it chooses one: `open` on Windows, `allowlist` elsewhere."""
    return OPEN if platform == "win32" else DEFAULT_PROFILE


def is_open(profile: str) -> bool:
    return check(profile) == OPEN


def check(profile: str) -> str:
    if profile not in PROFILES:
        raise ValueError(f"unknown network profile {profile!r} (known: {', '.join(sorted(PROFILES))})")
    return profile


def clean_host(item: str) -> str:
    """One entry of a machine's own host list as "host:port": a bare host means port 443,
    `*.example.com` any subdomain. Raises ValueError for anything else."""
    text = str(item or "").strip().lower().rstrip(".")
    if "://" in text:
        text = text.split("://", 1)[1].split("/", 1)[0]
    host, sep, port = text.rpartition(":")
    if not sep:
        host, port = text, "443"
    name = host[2:] if host.startswith("*.") else host
    labels = name.split(".")
    if not port.isdigit() or not 0 < int(port) < 65536 or len(labels) < 2 or not all(l and all(c.isalnum() or c == "-" for c in l) for l in labels):
        raise ValueError(f"not a host name: {item!r} (for example api.example.com:443)")
    return f"{host}:{int(port)}"


def clean_hosts(items) -> list[str]:
    """A machine's own host list, cleaned and without repeats; bad entries are dropped."""
    out: list[str] = []
    for item in items or []:
        try:
            host = clean_host(item)
        except ValueError:
            continue
        if host not in out:
            out.append(host)
    return out


def hosts(profile: str) -> list[str]:
    return [h for group in PROFILES[check(profile)].values() for h in group]
