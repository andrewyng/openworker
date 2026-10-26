"""windows_wfp.py is loaded only on Windows, but its structures are plain ctypes and can be
checked anywhere: a GUID round-trips, and the layouts match the 64-bit headers."""

from __future__ import annotations

import ctypes
import uuid

from coworker.sandbox import windows_wfp as wfp


def test_guid_round_trips_and_layouts_match_the_headers():
    value = uuid.UUID("c38d57d1-05a7-4c33-904f-7fbceee60e82")
    g = wfp.GUID.of(value)
    assert (g.Data1, g.Data2, g.Data3, bytes(g.Data4)) == (0xC38D57D1, 0x05A7, 0x4C33, bytes.fromhex("904f7fbceee60e82"))
    assert ctypes.sizeof(wfp.GUID) == 16
    assert ctypes.sizeof(wfp.FWP_VALUE0) == 16 and ctypes.sizeof(wfp.FWP_CONDITION_VALUE0) == 16
    assert ctypes.sizeof(wfp.FWPM_FILTER_CONDITION0) == 40
    assert ctypes.sizeof(wfp.FWPM_ACTION0) == 20
    assert ctypes.sizeof(wfp.FWPM_FILTER0) == 200 if ctypes.sizeof(ctypes.c_void_p) == 8 else True
    assert len(wfp.FILTER_KEYS) == 4 and len({str(k) for k in wfp.FILTER_KEYS.values()}) == 4
    assert wfp.ACTION_BLOCK == 0x1001 and wfp.ACTION_PERMIT == 0x1002
