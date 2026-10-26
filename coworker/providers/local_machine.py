"""What this machine can give a local model: the memory models run in, and the context
size that memory supports.

A local model's context window is a trade: every token of context costs memory while the
model is loaded, and a window the machine cannot hold either fails to load or crawls. The
model's trained maximum (131K to 1M on current models) is the wrong default; the machine
decides. Ollama does the same on its side (4K under 24 GiB of GPU memory, 32K to 48 GiB,
256K above) but its lowest tier is smaller than OpenWorker's first request, so OpenWorker
sets the window itself (ollama_context.py) and uses the tiers here.

Memory is read once per process: it does not change while OpenWorker runs. An NVIDIA card
reports its own memory through `nvidia-smi`; everything else (Apple silicon, CPU-only
machines, DGX Spark with its shared memory, where nvidia-smi reports none) uses system
memory, which is what those models load into.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Optional

GB = 1024**3

# Context tiers by the memory models run in. Each step keeps the KV cache well inside
# what is left beside a 20 to 30 GB model: measured on a 30B Q4 model, 128K of context
# costs about 13 GB on top of the weights.
CONTEXT_TIERS: tuple[tuple[int, int], ...] = (
    (16 * GB, 16_384),
    (32 * GB, 32_768),
    (64 * GB, 65_536),
)
CONTEXT_ABOVE_TIERS = 131_072

# Below this, OpenWorker's own first request (system prompt plus tools, ~6K tokens) does
# not fit with room to work.
MIN_AGENT_CONTEXT = 16_384


def system_memory_bytes() -> Optional[int]:
    """Total system memory, or None when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullTotalPhys)
            return None
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page > 0:
            return int(pages) * int(page)
    except (AttributeError, ValueError, OSError):
        pass
    return None


def nvidia_gpu_memory_bytes() -> Optional[int]:
    """Memory of the largest NVIDIA card `nvidia-smi` reports, or None: no tool, no
    card, or a card with no memory of its own (DGX Spark answers "N/A")."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    out = _run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
    best = 0
    for line in out.splitlines():
        try:
            best = max(best, int(float(line.strip())) * 1024 * 1024)
        except ValueError:
            continue
    return best or None


@functools.lru_cache(maxsize=1)
def model_memory_bytes() -> Optional[int]:
    """The memory a local model loads into on this machine."""
    return nvidia_gpu_memory_bytes() or system_memory_bytes()


def recommended_context(
    model_max: Optional[int], memory_bytes: Optional[int]
) -> Optional[int]:
    """The context window to run a local model at, from the memory available.

    None when the memory is unknown: the caller keeps its own default. Never above the
    model's own maximum, never below the smallest tier."""
    if memory_bytes is None:
        return None
    tier = CONTEXT_ABOVE_TIERS
    for limit, ctx in CONTEXT_TIERS:
        if memory_bytes < limit:
            tier = ctx
            break
    if model_max:
        tier = min(tier, model_max)
    return max(tier, min(MIN_AGENT_CONTEXT, model_max or MIN_AGENT_CONTEXT))


# -- what this machine is -----------------------------------------------------------------
# For the "Your system" section and the fit advice next to each model. Read on request
# and cached: none of it changes while OpenWorker runs except free storage, which is
# re-read each time.

# A model "runs well" when its file leaves this much of the memory for context and the
# rest of the system; "tight" up to the second share; beyond that it is too large.
RUNS_WELL_SHARE = 0.65
TIGHT_SHARE = 0.85


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip().strip("\x00")
    except OSError:
        return ""


# Windows: a console program started from the desktop app would flash a black window.
_NO_WINDOW: dict[str, Any] = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=5, check=False, **_NO_WINDOW
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows_processor() -> str:
    """The marketing name Windows keeps in the registry ("Intel(R) Core(TM) i7-12700H");
    platform.processor() gives "Intel64 Family 6 Model 154 Stepping 3, GenuineIntel"."""
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        try:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        finally:
            winreg.CloseKey(key)
        return " ".join(str(value).split())
    except Exception:
        return ""


def _nvidia_gpu_name() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    out = _run([exe, "--query-gpu=name", "--format=csv,noheader"])
    return out.splitlines()[0].strip() if out else ""


@functools.lru_cache(maxsize=1)
def _static_facts() -> dict[str, Any]:
    """Processor, graphics, memory and the machine's kind. Cached for the process."""
    import platform

    processor = ""
    graphics = ""
    kind = "cpu"
    if sys.platform == "darwin":
        processor = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if processor.startswith("Apple"):
            kind = "apple_silicon"
            graphics = "Built into the chip, shares the memory"
    elif sys.platform.startswith("linux"):
        for line in _read("/proc/cpuinfo").splitlines():
            if line.lower().startswith("model name"):
                processor = line.split(":", 1)[1].strip()
                break
        product = " ".join(
            _read(p) for p in ("/sys/class/dmi/id/product_name", "/sys/firmware/devicetree/base/model")
        )
        if re.search(r"DGX[ _-]*Spark", product, re.IGNORECASE):
            kind = "dgx_spark"
            graphics = "NVIDIA GB10, shares the memory"
        elif re.search(r"Jetson|Tegra|Orin|Thor", product, re.IGNORECASE):
            kind = "jetson"
            graphics = product.strip() + ", shares the memory"
    elif sys.platform == "win32":
        processor = _windows_processor() or platform.processor() or ""
    else:
        processor = platform.processor() or ""
    if kind == "cpu":
        name = _nvidia_gpu_name()
        vram = nvidia_gpu_memory_bytes()
        if name:
            kind = "nvidia"
            graphics = f"{name} · {round(vram / GB)} GB" if vram else name
    return {
        "processor": processor or platform.machine() or "Unknown",
        "graphics": graphics or "None found",
        "kind": kind,
        "memory_bytes": system_memory_bytes(),
        "gpu_memory_bytes": nvidia_gpu_memory_bytes() if kind == "nvidia" else None,
        "platform": sys.platform,
    }


def system_facts() -> dict[str, Any]:
    """Facts for the "Your system" section, with the memory models load into and the
    largest model file that runs well here."""
    facts = dict(_static_facts())
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        facts["storage_free_bytes"] = int(usage.free)
        facts["storage_total_bytes"] = int(usage.total)
    except OSError:
        facts["storage_free_bytes"] = facts["storage_total_bytes"] = None
    memory = model_memory_bytes()
    facts["model_memory_bytes"] = memory
    facts["runs_well_up_to_bytes"] = int(memory * RUNS_WELL_SHARE) if memory else None
    return facts


def fit_for(model_bytes: Optional[int], memory_bytes: Optional[int] = None) -> str:
    """How a model file of this size sits on this machine: `runs_well`, `tight`,
    `too_large`, or `unknown`."""
    memory = memory_bytes if memory_bytes is not None else model_memory_bytes()
    if not model_bytes or not memory:
        return "unknown"
    if model_bytes <= memory * RUNS_WELL_SHARE:
        return "runs_well"
    if model_bytes <= memory * TIGHT_SHARE:
        return "tight"
    return "too_large"
