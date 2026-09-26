"""The three protobuf messages of OpenShell's `ExecSandboxInteractive`, by hand.

Only this one streaming call goes over gRPC (the CLI cannot stream input without a
terminal; spike finding B). Its messages are tiny, so they are encoded and decoded here
with the protobuf wire format directly, which keeps generated code and the protobuf
runtime out of OpenWorker. Field numbers are those of OpenShell 0.0.116 (`proto/
openshell.proto`); the provider refuses any other version.

    message ExecSandboxInput  { oneof payload { ExecSandboxRequest start = 1; bytes stdin = 2; } }
    message ExecSandboxRequest { string sandbox_id = 1; repeated string command = 2; ... bool tty = 7; }
    message ExecSandboxEvent  { oneof payload { Stdout stdout = 1; Stderr stderr = 2; Exit exit = 3; } }
    message ExecSandboxStdout { bytes data = 1; }   (Stderr alike)
    message ExecSandboxExit   { int32 exit_code = 1; }
"""

from __future__ import annotations

from typing import Iterator, Optional

METHOD = "/openshell.v1.OpenShell/ExecSandboxInteractive"
_LEN, _VARINT = 2, 0


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | _LEN) + _varint(len(payload)) + payload


def encode_start(sandbox_id: str, command: list[str]) -> bytes:
    request = _field(1, sandbox_id.encode("utf-8"))
    for part in command:
        request += _field(2, part.encode("utf-8"))
    # tty (field 7) is left at its default, false: no terminal on the stream.
    return _field(1, request)


def encode_stdin(data: bytes) -> bytes:
    return _field(2, data)


def _fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        number, kind = key >> 3, key & 7
        if kind == _VARINT:
            value, pos = _read_varint(buf, pos)
            yield number, kind, value
        elif kind == _LEN:
            size, pos = _read_varint(buf, pos)
            yield number, kind, buf[pos : pos + size]
            pos += size
        elif kind == 5:
            pos += 4
        elif kind == 1:
            pos += 8
        else:
            raise ValueError(f"unsupported protobuf wire type {kind}")


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def decode_event(buf: bytes) -> tuple[str, Optional[bytes], Optional[int]]:
    """(kind, data, exit_code) with kind one of "stdout", "stderr", "exit", "other"."""
    for number, kind, value in _fields(buf):
        if kind != _LEN or not isinstance(value, bytes):
            continue
        if number in (1, 2):
            data = b"".join(v for n, k, v in _fields(value) if n == 1 and k == _LEN and isinstance(v, bytes))
            return ("stdout" if number == 1 else "stderr"), data, None
        if number == 3:
            code = 0
            for n, k, v in _fields(value):
                if n == 1 and k == _VARINT and isinstance(v, int):
                    code = v - (1 << 64) if v >= (1 << 63) else v  # int32 travels sign-extended
            return "exit", None, code
    return "other", None, None
