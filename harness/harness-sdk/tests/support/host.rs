// Development-only conformance host. Never ship this fixture to partners.
mod tool;
fn main() {
    // Explicit test database setup for cross-language conformance clients.
    // This binary is gated by test-support and is never a partner deliverable.
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("sdk-test-migrate") {
        let config: openworker_sdk::HostConfig =
            serde_json::from_slice(&std::fs::read(&args[2]).unwrap()).unwrap();
        config.storage.migrate().unwrap();
        return;
    }
    if std::env::args().any(|s| s == "sdk-malformed") {
        println!("not a protocol frame");
        return;
    }
    openworker_sdk::cli::main_with_registry(tool::registry());
}
