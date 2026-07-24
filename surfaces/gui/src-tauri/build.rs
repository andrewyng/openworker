fn main() {
    // Release scripts replace this ignored directory with the PyInstaller sidecar. Keep plain
    // Cargo checks from failing Tauri's resource validation in a fresh checkout.
    std::fs::create_dir_all("binaries/sidecar").expect("create sidecar resource directory");
    tauri_build::build()
}
