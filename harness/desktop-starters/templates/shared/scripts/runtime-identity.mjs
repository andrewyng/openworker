// Keep the installer architecture aligned with the actual bundled helper, not
// the architecture of Node (which may itself run under Windows emulation).
export function runtimeArch(bytes, platform = process.platform) {
  let machine;
  if (
    platform === "win32" &&
    bytes.subarray(0, 2).toString() === "MZ" &&
    bytes.length >= 64
  ) {
    const pe = bytes.readUInt32LE(60);
    if (
      pe + 6 <= bytes.length &&
      bytes.subarray(pe, pe + 4).equals(Buffer.from("PE\0\0"))
    ) {
      machine = bytes.readUInt16LE(pe + 4);
      if (machine === 0xaa64) return "arm64";
      if (machine === 0x8664) return "x64";
    }
  }
  if (
    platform === "darwin" &&
    bytes.length >= 8 &&
    bytes.readUInt32LE(0) === 0xfeedfacf
  ) {
    machine = bytes.readUInt32LE(4);
    if (machine === 0x100000c) return "arm64";
    if (machine === 0x1000007) return "x64";
  }
  if (
    platform === "linux" &&
    bytes.length >= 20 &&
    bytes.subarray(0, 4).equals(Buffer.from([127, 69, 76, 70])) &&
    bytes[5] === 1
  ) {
    machine = bytes.readUInt16LE(18);
    if (machine === 183) return "arm64";
    if (machine === 62) return "x64";
  }
  throw Error(
    "Runtime must be a native x64/ARM64 executable for this target OS",
  );
}
