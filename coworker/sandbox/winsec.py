"""Windows security plumbing for the sandbox providers, through ctypes (no pywin32).

Only the server side imports this; the runner package stays standard library and does not
need to know who its server is beyond the SIDs it is handed.

What is here, in the order a provider uses it:
- `current_user_sid()`, `logon_sid()`: who we are (the desktop's descriptor names both).
- `session_sid()`: a made-up SID for one sandbox. It is no account and never looked up; it is
  what the write-restricted token and the folder entries have in common.
- `write_restricted_token(session)`: our own token, restricted so that a WRITE is allowed only
  where the session SID (or the logon SID, or Everyone) is allowed too. Reads are not
  affected. The token's default DACL names the session SID as well, or the process could not
  open its own token and children (found 2026-09-22: error 5 on every CreateProcess).
- `Desktop`: a window station and desktop of our own, so the sandbox never touches the
  person's windows and clipboard, and so it starts at all from a process with no desktop.
- `spawn(...)`: CreateProcessAsUser with the token, on that desktop, inside a job object that
  ends every descendant when the job closes. Returns a `Process` with the Popen calls the
  providers use (`poll`, `wait`, `terminate`, `kill`, `pid`, `returncode`).
- `grant_write(folder, sid)` / `revoke(folder, sid)`: one inheritable Modify entry for the SID
  on a folder, and its removal.
"""

from __future__ import annotations

import ctypes
import os
import random
import sys
from functools import lru_cache
from typing import Optional, Sequence

if sys.platform == "win32":
    from ctypes import wintypes

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
        ]  # fmt: skip

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class TOKEN_DEFAULT_DACL(ctypes.Structure):
        _fields_ = [("DefaultDacl", ctypes.c_void_p)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]  # fmt: skip

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]  # fmt: skip

    class TRUSTEE_W(ctypes.Structure):
        _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int), ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]

    class EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [("grfAccessPermissions", wintypes.DWORD), ("grfAccessMode", ctypes.c_int), ("grfInheritance", wintypes.DWORD), ("Trustee", TRUSTEE_W)]

    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.LocalFree.argtypes = [ctypes.c_void_p]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.ResumeThread.argtypes = [wintypes.HANDLE]
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _adv.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    _adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
    _adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    _adv.CreateProcessAsUserW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    _adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _adv.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(EXPLICIT_ACCESS_W), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _u32.CreateWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _u32.CreateWindowStationW.restype = wintypes.HANDLE
    _u32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _u32.CreateDesktopW.restype = wintypes.HANDLE
    _u32.GetProcessWindowStation.restype = wintypes.HANDLE
    _u32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
    _u32.CloseWindowStation.argtypes = [wintypes.HANDLE]
    _u32.CloseDesktop.argtypes = [wintypes.HANDLE]

_TOKEN_ALL_ACCESS = 0xF01FF
_TOKEN_QUERY = 0x0008
_TokenUser, _TokenDefaultDacl, _TokenLogonSid = 1, 6, 28
_SDDL_REVISION_1 = 1
_WINSTA_ALL_ACCESS = 0x37F
_DESKTOP_ALL_ACCESS = 0x1FF | 0x000F0000
CREATE_SUSPENDED = 0x4
CREATE_NEW_PROCESS_GROUP = 0x200
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x100
_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_TRUSTEE_IS_SID = 0
_GRANT_ACCESS, _REVOKE_ACCESS = 1, 4
_CONTAINER_INHERIT_ACE, _OBJECT_INHERIT_ACE = 0x2, 0x1
_FILE_GENERIC_MODIFY = 0x1301BF  # icacls "M": read, write, execute, delete
_FILE_GENERIC_READ_EXECUTE = 0x1200A9  # icacls "RX"
_FILE_TRAVERSE_ONLY = 0x20 | 0x80 | 0x100000  # FILE_TRAVERSE, FILE_READ_ATTRIBUTES, SYNCHRONIZE: icacls "(X,RA,S)"
_LOGON_WITH_PROFILE = 0x1
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0


def _fail(what: str) -> OSError:
    err = ctypes.get_last_error()
    return OSError(err, f"{what}: {ctypes.FormatError(err).strip()}")


def _token_info(token: int, kind: int) -> ctypes.Array:
    needed = wintypes.DWORD()
    _adv.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
    buffer = ctypes.create_string_buffer(needed.value)
    if not _adv.GetTokenInformation(token, kind, buffer, needed, ctypes.byref(needed)):
        raise _fail("GetTokenInformation")
    return buffer


def _sid_text(sid: int) -> str:
    text = ctypes.c_wchar_p()
    if not _adv.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text)):
        raise _fail("ConvertSidToStringSid")
    try:
        return text.value or ""
    finally:
        _k32.LocalFree(text)


class _Sid:
    """A binary SID from its text, kept alive as long as the object."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pointer = ctypes.c_void_p()
        if not _adv.ConvertStringSidToSidW(text, ctypes.byref(self.pointer)):
            raise _fail(f"ConvertStringSidToSid {text!r}")

    def __del__(self) -> None:
        if getattr(self, "pointer", None) and self.pointer.value:
            _k32.LocalFree(self.pointer)


class _OwnToken:
    def __init__(self, access: int) -> None:
        self.handle = wintypes.HANDLE()
        if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), access, ctypes.byref(self.handle)):
            raise _fail("OpenProcessToken")

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = wintypes.HANDLE()


@lru_cache(maxsize=1)
def current_user_sid() -> str:
    """The SID of the account this process runs as, as a string (`S-1-5-21-...`)."""
    if sys.platform != "win32":
        raise OSError("Windows only")
    token = _OwnToken(_TOKEN_QUERY)
    try:
        buffer = _token_info(token.handle, _TokenUser)  # TOKEN_USER starts with SID_AND_ATTRIBUTES.Sid
        return _sid_text(ctypes.c_void_p.from_buffer(buffer).value)
    finally:
        token.close()


@lru_cache(maxsize=1)
def logon_sid() -> str:
    """The SID of this logon session (`S-1-5-5-X-Y`); the desktop and other per-session
    objects grant it, so a restricted token must carry it or nothing starts."""
    token = _OwnToken(_TOKEN_QUERY)
    try:
        buffer = _token_info(token.handle, _TokenLogonSid)  # TOKEN_GROUPS: count, then the entries
        entry = SID_AND_ATTRIBUTES.from_buffer(buffer, ctypes.sizeof(ctypes.c_void_p))
        return _sid_text(entry.Sid)
    finally:
        token.close()


def _descriptor(sddl: str) -> ctypes.c_void_p:
    descriptor = ctypes.c_void_p()
    size = wintypes.ULONG()
    if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)):
        raise _fail(f"security descriptor from {sddl!r}")
    return descriptor


class Desktop:
    """A window station and desktop of our own that the user, the session SID and this
    logon may open. `name` is what goes into STARTUPINFO.lpDesktop."""

    def __init__(self, session: str) -> None:
        sddl = f"D:(A;;GA;;;{current_user_sid()})(A;;GA;;;{session})(A;;GA;;;{logon_sid()})"
        self._descriptor = _descriptor(sddl)
        attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), self._descriptor, False)
        self.station_name = "owsb-%08x" % random.SystemRandom().getrandbits(32)
        self.station = _u32.CreateWindowStationW(self.station_name, 0, _WINSTA_ALL_ACCESS, ctypes.byref(attributes))
        if not self.station:
            raise _fail("CreateWindowStation")
        previous = _u32.GetProcessWindowStation()
        _u32.SetProcessWindowStation(self.station)  # CreateDesktop works on the process's station
        try:
            self.desktop = _u32.CreateDesktopW("default", None, None, 0, _DESKTOP_ALL_ACCESS, ctypes.byref(attributes))
        finally:
            _u32.SetProcessWindowStation(previous)
        if not self.desktop:
            _u32.CloseWindowStation(self.station)
            raise _fail("CreateDesktop")
        self.name = f"{self.station_name}\\default"

    def close(self) -> None:
        if getattr(self, "desktop", None):
            _u32.CloseDesktop(self.desktop)
            self.desktop = None
        if getattr(self, "station", None):
            _u32.CloseWindowStation(self.station)
            self.station = None


class Process:
    """A process started by `spawn`, with the calls a provider uses on a Popen."""

    def __init__(self, info: "PROCESS_INFORMATION", job: int) -> None:
        self._handle = info.hProcess
        self._job = job
        self.pid = int(info.dwProcessId)
        self.returncode: Optional[int] = None
        _k32.CloseHandle(info.hThread)

    def poll(self) -> Optional[int]:
        if self.returncode is None and _k32.WaitForSingleObject(self._handle, 0) == _WAIT_OBJECT_0:
            code = wintypes.DWORD()
            _k32.GetExitCodeProcess(self._handle, ctypes.byref(code))
            self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        ms = _INFINITE if timeout is None else int(timeout * 1000)
        if _k32.WaitForSingleObject(self._handle, ms) != _WAIT_OBJECT_0:
            import subprocess

            raise subprocess.TimeoutExpired("sandbox daemon", timeout or 0)
        code = self.poll()
        assert code is not None
        return code

    def terminate(self) -> None:
        """Ends the whole job: the daemon, its shells and everything they started."""
        if self.poll() is None:
            _k32.TerminateJobObject(self._job, 1)

    kill = terminate

    def close(self) -> None:
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None
        if self._job:
            _k32.CloseHandle(self._job)  # kill-on-close: whatever is left in the job ends here
            self._job = None


def spawn_as_account(
    argv: Sequence[str],
    *,
    account: str,
    password: str,
    desktop: Desktop,
    cwd: str,
    stderr_path: Optional[str] = None,
) -> Process:
    """Start `argv` logged on as another local account (CreateProcessWithLogonW, which is
    the secondary logon service: no privilege needed), on our desktop, in a job that ends
    with the Process. The environment is the account's own; a provider passes what it
    needs through the daemon's `--env`. stdin is closed; stdout and stderr go to
    `stderr_path` when given."""
    import subprocess

    _adv.CreateProcessWithLogonW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
    ]  # fmt: skip
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        raise _fail("CreateJobObject")
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise _fail("SetInformationJobObject")
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.lpDesktop = desktop.name
    # Handles cannot be inherited across a logon, so the daemon's stderr is a file it opens
    # itself: the command line names it through a redirect done by cmd.
    command = subprocess.list2cmdline(argv)
    if stderr_path:
        command = f'cmd.exe /d /c "{command} > "{stderr_path}" 2>&1"'
    buffer = ctypes.create_unicode_buffer(command)
    info = PROCESS_INFORMATION()
    flags = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
    if not _adv.CreateProcessWithLogonW(account, ".", password, _LOGON_WITH_PROFILE, None, buffer, flags, None, cwd, ctypes.byref(startup), ctypes.byref(info)):
        raise _fail("CreateProcessWithLogonW")
    if not _k32.AssignProcessToJobObject(job, info.hProcess):
        _k32.TerminateProcess(info.hProcess, 1)
        raise _fail("AssignProcessToJobObject")
    _k32.ResumeThread(info.hThread)
    return Process(info, job)


def _explicit(sid: _Sid, mode: int, permissions: int = 0, inherit: bool = True) -> "EXPLICIT_ACCESS_W":
    entry = EXPLICIT_ACCESS_W()
    entry.grfAccessPermissions = permissions or _FILE_GENERIC_MODIFY
    entry.grfAccessMode = mode
    entry.grfInheritance = (_CONTAINER_INHERIT_ACE | _OBJECT_INHERIT_ACE) if inherit else 0
    entry.Trustee.TrusteeForm = _TRUSTEE_IS_SID
    entry.Trustee.TrusteeType = 0  # TRUSTEE_IS_UNKNOWN: it is no account
    entry.Trustee.ptstrName = sid.pointer.value
    return entry


def _change_dacl(folder: str, sid_text: str, mode: int, permissions: int = 0, inherit: bool = True) -> None:
    sid = _Sid(sid_text)
    old = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = _adv.GetNamedSecurityInfoW(folder, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, ctypes.byref(old), None, ctypes.byref(descriptor))
    if status:
        raise OSError(status, f"GetNamedSecurityInfo {folder}: {ctypes.FormatError(status).strip()}")
    try:
        new = ctypes.c_void_p()
        entry = _explicit(sid, mode, permissions, inherit)
        status = _adv.SetEntriesInAclW(1, ctypes.byref(entry), old, ctypes.byref(new))
        if status:
            raise OSError(status, f"SetEntriesInAcl {folder}: {ctypes.FormatError(status).strip()}")
        try:
            status = _adv.SetNamedSecurityInfoW(folder, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, new, None)
            if status:
                raise OSError(status, f"SetNamedSecurityInfo {folder}: {ctypes.FormatError(status).strip()}")
        finally:
            _k32.LocalFree(new)
    finally:
        _k32.LocalFree(descriptor)


def grant_write(folder: str, sid_text: str, *, inherit: bool = True) -> None:
    """One inheritable Modify entry for the SID on the folder (what `icacls /grant
    *SID:(OI)(CI)M` would do, without the account lookup that icacls insists on).
    `inherit=False` for one file."""
    _change_dacl(folder, sid_text, _GRANT_ACCESS, inherit=inherit)


def grant_traverse(folder: str, sid_text: str) -> None:
    """Pass through the folder without listing it (one non-inheritable entry: traverse,
    read attributes, synchronize). Needed on every folder between a granted folder and the
    profile root: PowerShell resolves a path by reading each parent's attributes, and
    "bypass traverse checking" does not cover that, so without it a shell cannot enter
    a granted folder under the person's profile (found 2026-09-28)."""
    _change_dacl(folder, sid_text, _GRANT_ACCESS, _FILE_TRAVERSE_ONLY, inherit=False)


def grant_read(folder: str, sid_text: str) -> None:
    """One inheritable Read-and-execute entry (icacls "RX") for the SID on the folder."""
    _change_dacl(folder, sid_text, _GRANT_ACCESS, _FILE_GENERIC_READ_EXECUTE)


def revoke(folder: str, sid_text: str) -> None:
    """Remove every entry for the SID from the folder (the children inherit the removal)."""
    _change_dacl(folder, sid_text, _REVOKE_ACCESS)


def private_acl(path: str) -> None:
    """Make a folder (and what is under it) readable by this user, administrators and
    SYSTEM only, with no inheritance: what Windows OpenSSH demands of a key file."""
    import subprocess

    # The folder gets the three entries and stops inheriting; everything under it is then
    # reset to inherit from the folder. (One `/T` pass instead would leave the files with
    # an EMPTY list: the inheritable flags are not valid on a file and the grant is dropped.)
    steps = [
        ["icacls", path, "/inheritance:r", "/grant:r", f"*{current_user_sid()}:(OI)(CI)F", "/grant:r", "*S-1-5-18:(OI)(CI)F", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "/Q"],
        ["icacls", os.path.join(path, "*"), "/reset", "/T", "/Q"],
    ]  # fmt: skip
    for argv in steps:
        done = subprocess.run(argv, capture_output=True, text=True)
        if done.returncode != 0:
            raise OSError(done.returncode, f"{' '.join(argv[:2])}: {(done.stderr or done.stdout).strip()}")


def entries_for(folder: str, sid_text: str) -> bool:
    """Whether the folder's own DACL still names the SID (for tests and clean-up checks).
    `icacls /findsid` matches by SID, so a real account (which icacls would print by name)
    is found the same way as a made-up one."""
    import subprocess

    done = subprocess.run(["icacls", folder, "/findsid", f"*{sid_text}"], capture_output=True, text=True)
    return "SID Found" in done.stdout
