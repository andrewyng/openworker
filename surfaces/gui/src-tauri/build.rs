fn main() {
    // Release packaging stages the compiled Chrome Native Messaging helper
    // here. Keep the ignored directory present for `cargo check` and Tauri dev
    // runs, where the optional helper may not have been built yet.
    let _ = std::fs::create_dir_all("binaries/native-host");
    tauri_build::build()
}
