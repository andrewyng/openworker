"""Public connector descriptor registry assembled from bounded catalog modules."""

from typing import Optional

from . import descriptor_core as _core
from .descriptor_core import ConnectorDescriptor, Field, ValidationResult
from .descriptor_catalog_1 import DESCRIPTORS as _DESCRIPTORS_1
from .descriptor_catalog_2 import DESCRIPTORS as _DESCRIPTORS_2
from .descriptor_catalog_3 import DESCRIPTORS as _DESCRIPTORS_3

# Preserve the validator names historically exposed by this module.
_validate_amplitude = _core._validate_amplitude
_validate_apollo = _core._validate_apollo
_validate_asana = _core._validate_asana
_validate_attio = _core._validate_attio
_validate_box = _core._validate_box
_validate_canva = _core._validate_canva
_validate_clickup = _core._validate_clickup
_validate_close = _core._validate_close
_validate_discord = _core._validate_discord
_validate_docusign = _core._validate_docusign
_validate_dropbox = _core._validate_dropbox
_validate_email = _core._validate_email
_validate_figma = _core._validate_figma
_validate_gitlab = _core._validate_gitlab
_validate_google_drive = _core._validate_google_drive
_validate_hubspot = _core._validate_hubspot
_validate_hunter = _core._validate_hunter
_validate_linear = _core._validate_linear
_validate_mixpanel = _core._validate_mixpanel
_validate_notion = _core._validate_notion
_validate_outlook = _core._validate_outlook
_validate_posthog = _core._validate_posthog
_validate_quickbooks = _core._validate_quickbooks
_validate_slack = _core._validate_slack
_validate_telegram = _core._validate_telegram
_validate_whatsapp = _core._validate_whatsapp
_validate_whoami = _core._validate_whoami

DESCRIPTORS: list[ConnectorDescriptor] = [
    *_DESCRIPTORS_1,
    *_DESCRIPTORS_2,
    *_DESCRIPTORS_3,
]
_BY_NAME = {d.name: d for d in DESCRIPTORS}


def register_descriptor(descriptor: ConnectorDescriptor) -> None:
    """Register an extra connector (used by the experimental package and tests)."""
    DESCRIPTORS.append(descriptor)
    _BY_NAME[descriptor.name] = descriptor


# Experimental connectors live in a separate package so release builds can exclude the code
# entirely (see packaging/openworker-server.spec). When the package is absent this is a no-op.
try:
    from .experimental import EXPERIMENTAL_DESCRIPTORS as _EXPERIMENTAL
except ImportError:
    _EXPERIMENTAL = []
for _exp in _EXPERIMENTAL:
    _exp.experimental = True  # enforced here, not trusted from the author
    register_descriptor(_exp)


def list_descriptors() -> list[ConnectorDescriptor]:
    return list(DESCRIPTORS)


def get_descriptor(name: str) -> Optional[ConnectorDescriptor]:
    return _BY_NAME.get(name)
