fn main() {
    // tauri-build validates every `bundle.resources` path on every build, dev included, but
    // `binaries/sidecar` is only staged by the release scripts and `/binaries` is gitignored —
    // so a fresh checkout died on `resource path 'binaries/sidecar' doesn't exist`. Dev needs no
    // packaged server (`server_bin()` falls back to the venv) and empty resource dirs are
    // skipped, so a placeholder is enough.
    std::fs::create_dir_all("binaries/sidecar").expect("create the sidecar resource dir");

    tauri_build::build()
}
