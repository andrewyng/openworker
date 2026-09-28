"""Loopback filters for the closed Windows sandbox account (Windows Filtering Platform).

Windows Firewall does not look at loopback traffic, so the outbound-block rule that setup
writes for the closed account (`OWSandboxClosedNet`) leaves every local port open to it:
a database, another app's control port, another sandbox's proxy. These filters close that
hole in the kernel, once, at setup (design doc `sandbox-windows-design.md`, 3b and 3d):

  layer ALE_AUTH_CONNECT (IPv4 and IPv6), our own sublayer, for that account only:
    PERMIT  loopback, remote port in the proxy's range (netproxy.WINDOWS_PORTS)  weight 15
    BLOCK   loopback, any port                                                    weight 5

Listening is not touched (`vite`, `next dev` still work; your browser reaches them), and
the open account (`OWSandboxOpenNet`) has no filters at all. The filters are persistent
and carry fixed keys, so `remove` deletes exactly them.

This file runs ELEVATED, started by the setup script, and imports nothing from the
package on purpose: `python windows_wfp.py add|remove ...`, or `openworker-server
sandbox-wfp add|remove ...` from the frozen binary. Standard library only.
"""

from __future__ import annotations

import ctypes
import json
import sys
import uuid
from ctypes import wintypes
from typing import Any, Optional

# -- our fixed keys ---------------------------------------------------------------------
PROVIDER_KEY = uuid.UUID("3d0d3a3e-6c2b-5a3f-9f0a-1b6b7c1e8d21")
SUBLAYER_KEY = uuid.UUID("9a5b0f4c-2e7d-5c19-8a63-4f2e9c7b1d02")
FILTER_KEYS = {
    "permit-proxy-v4": uuid.UUID("c1d2e3f4-0001-5a6b-8c9d-0e1f2a3b4c01"),
    "block-loopback-v4": uuid.UUID("c1d2e3f4-0002-5a6b-8c9d-0e1f2a3b4c02"),
    "permit-proxy-v6": uuid.UUID("c1d2e3f4-0003-5a6b-8c9d-0e1f2a3b4c03"),
    "block-loopback-v6": uuid.UUID("c1d2e3f4-0004-5a6b-8c9d-0e1f2a3b4c04"),
}

# -- Windows' keys (fwpmu.h) ------------------------------------------------------------
LAYER_ALE_AUTH_CONNECT_V4 = uuid.UUID("c38d57d1-05a7-4c33-904f-7fbceee60e82")
LAYER_ALE_AUTH_CONNECT_V6 = uuid.UUID("4a72393b-319f-44bc-84c3-ba54dcb3b6b4")
CONDITION_ALE_USER_ID = uuid.UUID("af043a0a-b34d-4f86-979c-c90371af6e66")
CONDITION_IP_REMOTE_PORT = uuid.UUID("c35a604d-d22b-4e1a-91b4-68f674ee674b")
CONDITION_FLAGS = uuid.UUID("632ce23b-5167-435c-86d7-e903684aa80c")
FLAG_IS_LOOPBACK = 0x00000001

FWP_EMPTY, FWP_UINT8, FWP_UINT16, FWP_UINT32 = 0, 1, 2, 3
FWP_SECURITY_DESCRIPTOR_TYPE = 14
FWP_RANGE_TYPE = 0x102
MATCH_EQUAL, MATCH_RANGE, MATCH_FLAGS_ALL_SET = 0, 5, 6
ACTION_BLOCK, ACTION_PERMIT = 0x1001, 0x1002
FLAG_PERSISTENT = 0x1  # the same bit for providers, sublayers and filters
RPC_C_AUTHN_DEFAULT = 0xFFFFFFFF
SDDL_REVISION_1 = 1
ERROR_SUCCESS = 0


# Fixed-width types on purpose: `U32` is 8 bytes on a Mac, and the layouts are
# checked by a test that runs everywhere.
U16, U32, U64 = ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64


class GUID(ctypes.Structure):
    _fields_ = [("Data1", U32), ("Data2", U16), ("Data3", U16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, value: uuid.UUID) -> "GUID":
        g = cls()
        g.Data1, g.Data2, g.Data3 = value.time_low, value.time_mid, value.time_hi_version
        g.Data4[:] = value.bytes[8:]
        return g


class FWP_BYTE_BLOB(ctypes.Structure):
    _fields_ = [("size", U32), ("data", ctypes.c_void_p)]


class FWPM_DISPLAY_DATA0(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("description", wintypes.LPWSTR)]


class _VALUE_UNION(ctypes.Union):
    _fields_ = [("uint8", ctypes.c_ubyte), ("uint16", U16), ("uint32", U32), ("pointer", ctypes.c_void_p)]


class FWP_VALUE0(ctypes.Structure):
    _fields_ = [("type", U32), ("value", _VALUE_UNION)]


class FWP_RANGE0(ctypes.Structure):
    _fields_ = [("valueLow", FWP_VALUE0), ("valueHigh", FWP_VALUE0)]


class FWP_CONDITION_VALUE0(ctypes.Structure):  # the same shape as FWP_VALUE0 (the union gains pointer members)
    _fields_ = [("type", U32), ("value", _VALUE_UNION)]


class FWPM_FILTER_CONDITION0(ctypes.Structure):
    _fields_ = [("fieldKey", GUID), ("matchType", U32), ("conditionValue", FWP_CONDITION_VALUE0)]


class _ACTION_UNION(ctypes.Union):
    _fields_ = [("filterType", GUID), ("calloutKey", GUID), ("bitmapIndex", ctypes.c_ubyte)]


class FWPM_ACTION0(ctypes.Structure):
    _fields_ = [("type", U32), ("u", _ACTION_UNION)]


class _CONTEXT_UNION(ctypes.Union):
    _fields_ = [("rawContext", U64), ("providerContextKey", GUID)]


class FWPM_FILTER0(ctypes.Structure):
    _fields_ = [
        ("filterKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", U32),
        ("providerKey", ctypes.POINTER(GUID)),
        ("providerData", FWP_BYTE_BLOB),
        ("layerKey", GUID),
        ("subLayerKey", GUID),
        ("weight", FWP_VALUE0),
        ("numFilterConditions", U32),
        ("filterCondition", ctypes.POINTER(FWPM_FILTER_CONDITION0)),
        ("action", FWPM_ACTION0),
        ("context", _CONTEXT_UNION),
        ("reserved", ctypes.POINTER(GUID)),
        ("filterId", U64),
        ("effectiveWeight", FWP_VALUE0),
    ]


class FWPM_PROVIDER0(ctypes.Structure):
    _fields_ = [
        ("providerKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", U32),
        ("providerData", FWP_BYTE_BLOB),
        ("serviceName", wintypes.LPWSTR),
    ]


class FWPM_SUBLAYER0(ctypes.Structure):
    _fields_ = [
        ("subLayerKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", U16),
        ("providerKey", ctypes.POINTER(GUID)),
        ("providerData", FWP_BYTE_BLOB),
        ("weight", U16),
    ]


def _fwp() -> Any:
    lib = ctypes.WinDLL("fwpuclnt")
    lib.FwpmEngineOpen0.argtypes = [wintypes.LPCWSTR, U32, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
    lib.FwpmEngineOpen0.restype = U32
    lib.FwpmEngineClose0.argtypes = [wintypes.HANDLE]
    lib.FwpmEngineClose0.restype = U32
    lib.FwpmProviderAdd0.argtypes = [wintypes.HANDLE, ctypes.POINTER(FWPM_PROVIDER0), ctypes.c_void_p]
    lib.FwpmProviderAdd0.restype = U32
    lib.FwpmProviderDeleteByKey0.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID)]
    lib.FwpmProviderDeleteByKey0.restype = U32
    lib.FwpmSubLayerAdd0.argtypes = [wintypes.HANDLE, ctypes.POINTER(FWPM_SUBLAYER0), ctypes.c_void_p]
    lib.FwpmSubLayerAdd0.restype = U32
    lib.FwpmSubLayerDeleteByKey0.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID)]
    lib.FwpmSubLayerDeleteByKey0.restype = U32
    lib.FwpmFilterAdd0.argtypes = [wintypes.HANDLE, ctypes.POINTER(FWPM_FILTER0), ctypes.c_void_p, ctypes.POINTER(U64)]
    lib.FwpmFilterAdd0.restype = U32
    lib.FwpmFilterDeleteByKey0.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID)]
    lib.FwpmFilterDeleteByKey0.restype = U32
    lib.FwpmFilterGetByKey0.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(FWPM_FILTER0))]
    lib.FwpmFilterGetByKey0.restype = U32
    lib.FwpmFreeMemory0.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.FwpmFreeMemory0.restype = None
    return lib


class WfpError(OSError):
    pass


def _check(code: int, what: str) -> None:
    if code != ERROR_SUCCESS:
        raise WfpError(f"{what}: error 0x{code & 0xFFFFFFFF:08x}")


class Engine:
    """One open session with the Base Filtering Engine. Not dynamic: what is added with
    the persistent flag outlives it."""

    def __init__(self) -> None:
        self._lib = _fwp()
        self.handle = wintypes.HANDLE()
        _check(self._lib.FwpmEngineOpen0(None, RPC_C_AUTHN_DEFAULT, None, None, ctypes.byref(self.handle)), "FwpmEngineOpen0")

    def close(self) -> None:
        if self.handle:
            self._lib.FwpmEngineClose0(self.handle)
            self.handle = wintypes.HANDLE()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- keep references to every buffer a struct points at until the call returns -------
    def _display(self, name: str, description: str, keep: list) -> FWPM_DISPLAY_DATA0:
        n, d = ctypes.c_wchar_p(name), ctypes.c_wchar_p(description)
        keep += [n, d]
        return FWPM_DISPLAY_DATA0(ctypes.cast(n, wintypes.LPWSTR), ctypes.cast(d, wintypes.LPWSTR))

    def add_provider(self) -> None:
        keep: list = []
        provider = FWPM_PROVIDER0()
        provider.providerKey = GUID.of(PROVIDER_KEY)
        provider.displayData = self._display("OpenWorker sandbox", "Loopback filters for the OpenWorker sandbox account", keep)
        provider.flags = FLAG_PERSISTENT
        _check(self._lib.FwpmProviderAdd0(self.handle, ctypes.byref(provider), None), "FwpmProviderAdd0")

    def add_sublayer(self) -> None:
        keep: list = []
        sub = FWPM_SUBLAYER0()
        sub.subLayerKey = GUID.of(SUBLAYER_KEY)
        sub.displayData = self._display("OpenWorker sandbox", "Loopback: the allow-list proxy only", keep)
        sub.flags = FLAG_PERSISTENT
        provider_key = GUID.of(PROVIDER_KEY)
        keep.append(provider_key)
        sub.providerKey = ctypes.pointer(provider_key)
        sub.weight = 0xFFFF  # evaluated before the firewall's own sublayer
        _check(self._lib.FwpmSubLayerAdd0(self.handle, ctypes.byref(sub), None), "FwpmSubLayerAdd0")

    def add_filter(self, key: uuid.UUID, *, layer: uuid.UUID, name: str, action: int, weight: int, conditions: list) -> int:
        keep: list = []
        array = (FWPM_FILTER_CONDITION0 * len(conditions))()
        for i, (field, match, value) in enumerate(conditions):
            array[i].fieldKey = GUID.of(field)
            array[i].matchType = match
            array[i].conditionValue = value(keep)
        flt = FWPM_FILTER0()
        flt.filterKey = GUID.of(key)
        flt.displayData = self._display(f"OpenWorker sandbox: {name}", "Written by `openworker machine sandbox setup`", keep)
        flt.flags = FLAG_PERSISTENT
        provider_key = GUID.of(PROVIDER_KEY)
        keep.append(provider_key)
        flt.providerKey = ctypes.pointer(provider_key)
        flt.layerKey = GUID.of(layer)
        flt.subLayerKey = GUID.of(SUBLAYER_KEY)
        flt.weight.type = FWP_UINT8
        flt.weight.value.uint8 = weight
        flt.numFilterConditions = len(conditions)
        flt.filterCondition = ctypes.cast(array, ctypes.POINTER(FWPM_FILTER_CONDITION0))
        flt.action.type = action
        filter_id = U64()
        _check(self._lib.FwpmFilterAdd0(self.handle, ctypes.byref(flt), None, ctypes.byref(filter_id)), f"FwpmFilterAdd0 ({name})")
        return int(filter_id.value)

    def delete_filter(self, key: uuid.UUID) -> bool:
        return self._lib.FwpmFilterDeleteByKey0(self.handle, ctypes.byref(GUID.of(key))) == ERROR_SUCCESS

    def delete_sublayer(self) -> bool:
        return self._lib.FwpmSubLayerDeleteByKey0(self.handle, ctypes.byref(GUID.of(SUBLAYER_KEY))) == ERROR_SUCCESS

    def delete_provider(self) -> bool:
        return self._lib.FwpmProviderDeleteByKey0(self.handle, ctypes.byref(GUID.of(PROVIDER_KEY))) == ERROR_SUCCESS

    def filter_exists(self, key: uuid.UUID) -> Optional[bool]:
        """True or False, or None when this process may not ask (not an administrator)."""
        out = ctypes.POINTER(FWPM_FILTER0)()
        code = self._lib.FwpmFilterGetByKey0(self.handle, ctypes.byref(GUID.of(key)), ctypes.byref(out))
        if code == ERROR_SUCCESS:
            self._lib.FwpmFreeMemory0(ctypes.byref(ctypes.cast(out, ctypes.c_void_p)))
            return True
        if code & 0xFFFFFFFF == 0x80320003:  # FWP_E_FILTER_NOT_FOUND
            return False
        return None


# -- the condition values ---------------------------------------------------------------
def _user_condition(sid: str):
    """The account, as a security descriptor the token is checked against (the same
    `D:(A;;CC;;;SID)` Windows Firewall's LocalUser takes)."""

    def build(keep: list) -> FWP_CONDITION_VALUE0:
        adv = ctypes.WinDLL("advapi32")
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, U32, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(U32)]
        descriptor, size = ctypes.c_void_p(), U32()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:(A;;CC;;;{sid})", SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)):
            raise WfpError(f"cannot build a security descriptor for {sid}")
        blob = FWP_BYTE_BLOB(size.value, descriptor)
        keep += [descriptor, blob]
        value = FWP_CONDITION_VALUE0()
        value.type = FWP_SECURITY_DESCRIPTOR_TYPE
        value.value.pointer = ctypes.addressof(blob)
        return value

    return build


def _loopback_condition():
    def build(keep: list) -> FWP_CONDITION_VALUE0:
        value = FWP_CONDITION_VALUE0()
        value.type = FWP_UINT32
        value.value.uint32 = FLAG_IS_LOOPBACK
        return value

    return build


def _port_range_condition(low: int, high: int):
    def build(keep: list) -> FWP_CONDITION_VALUE0:
        rng = FWP_RANGE0()
        rng.valueLow.type = rng.valueHigh.type = FWP_UINT16
        rng.valueLow.value.uint16, rng.valueHigh.value.uint16 = low, high
        keep.append(rng)
        value = FWP_CONDITION_VALUE0()
        value.type = FWP_RANGE_TYPE
        value.value.pointer = ctypes.addressof(rng)
        return value

    return build


# -- the operations ---------------------------------------------------------------------
def add(sid: str, port_low: int, port_high: int) -> dict[str, int]:
    """Write the provider, the sublayer and the four filters for `sid`. Idempotent: what
    exists under our keys is deleted first. Returns the filter ids by name."""
    with Engine() as engine:
        remove_with(engine)
        engine.add_provider()
        engine.add_sublayer()
        ids: dict[str, int] = {}
        for suffix, layer in (("v4", LAYER_ALE_AUTH_CONNECT_V4), ("v6", LAYER_ALE_AUTH_CONNECT_V6)):
            user = (CONDITION_ALE_USER_ID, MATCH_EQUAL, _user_condition(sid))
            loopback = (CONDITION_FLAGS, MATCH_FLAGS_ALL_SET, _loopback_condition())
            ports = (CONDITION_IP_REMOTE_PORT, MATCH_RANGE, _port_range_condition(port_low, port_high))
            ids[f"permit-proxy-{suffix}"] = engine.add_filter(
                FILTER_KEYS[f"permit-proxy-{suffix}"], layer=layer, name=f"loopback to the allow-list proxy ({suffix})",
                action=ACTION_PERMIT, weight=15, conditions=[user, loopback, ports],
            )  # fmt: skip
            ids[f"block-loopback-{suffix}"] = engine.add_filter(
                FILTER_KEYS[f"block-loopback-{suffix}"], layer=layer, name=f"no other local port ({suffix})",
                action=ACTION_BLOCK, weight=5, conditions=[user, loopback],
            )  # fmt: skip
        return ids


def remove_with(engine: Engine) -> int:
    removed = sum(engine.delete_filter(key) for key in FILTER_KEYS.values())
    engine.delete_sublayer()
    engine.delete_provider()
    return removed


def remove() -> int:
    with Engine() as engine:
        return remove_with(engine)


def present() -> Optional[bool]:
    """All four filters exist; False when any is missing; None when this process may not
    look (the check then falls to the in-sandbox probe at session start)."""
    try:
        with Engine() as engine:
            seen = [engine.filter_exists(key) for key in FILTER_KEYS.values()]
    except OSError:
        return None
    if any(s is None for s in seen):
        return None
    return all(seen)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="windows_wfp")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("--sid", required=True)
    p_add.add_argument("--port-low", type=int, required=True)
    p_add.add_argument("--port-high", type=int, required=True)
    sub.add_parser("remove")
    sub.add_parser("present")
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            print(json.dumps({"ok": True, "filters": add(args.sid, args.port_low, args.port_high)}))
        elif args.command == "remove":
            print(json.dumps({"ok": True, "removed": remove()}))
        else:
            print(json.dumps({"ok": True, "present": present()}))
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
