"""Where an agent's commands may connect, in OUR words. Every provider renders these lists
into its own mechanism: OpenShell into `network_policies`, Seatbelt into the allow-list
proxy. HTTPS (port 443) only.
"""

from __future__ import annotations

CODE_HOSTS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com", "raw.githubusercontent.com", "gitlab.com"]
PACKAGE_REGISTRIES = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "crates.io", "static.crates.io", "index.crates.io", "proxy.golang.org", "sum.golang.org"]
SEARCH_APIS = ["api.search.brave.com", "api.tavily.com", "html.duckduckgo.com", "duckduckgo.com"]

# name of the group -> hosts, per profile
PROFILES: dict[str, dict[str, list[str]]] = {
    # git and package registries: enough to clone, install and push.
    "strict": {"code-hosts": CODE_HOSTS, "package-registries": PACKAGE_REGISTRIES},
    # plus the search APIs.
    "standard": {"code-hosts": CODE_HOSTS, "package-registries": PACKAGE_REGISTRIES, "search-apis": SEARCH_APIS},
}
DEFAULT_PROFILE = "strict"


def check(profile: str) -> str:
    if profile not in PROFILES:
        raise ValueError(f"unknown network profile {profile!r} (known: {', '.join(sorted(PROFILES))})")
    return profile


def hosts(profile: str) -> list[str]:
    return [h for group in PROFILES[check(profile)].values() for h in group]
