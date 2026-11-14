fn main() {
    let mut registry = openworker_tools::ToolRegistry::default();
    notification_tools::register(&mut registry).expect("Notification tool registration");
    device_tools::register(&mut registry).expect("Device tool registration");
    computer_tools::register(&mut registry).expect("Computer tool registration");
    openworker_sdk::cli::main_with_registry(registry);
}
