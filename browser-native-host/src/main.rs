use serde_json::{json, Value};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
#[cfg(target_os = "windows")]
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const HOST_NAME: &str = "com.openworker.browser";
const EXTENSION_ID: &str = "djnbhkmnbmjobnphflaopcpfkifbgekl";
const ALLOWED_ORIGIN: &str = "chrome-extension://djnbhkmnbmjobnphflaopcpfkifbgekl/";
const PROTOCOL_VERSION: u64 = 1;
const MAX_EXTENSION_MESSAGE_BYTES: usize = 64 * 1024 * 1024;
const MAX_HOST_MESSAGE_BYTES: usize = 1024 * 1024;
const MAX_HTTP_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Debug)]
struct RuntimeDescriptor {
    api_token: String,
}

#[derive(Clone, Debug)]
struct ServerAddress {
    host: String,
    port: u16,
}

#[derive(Clone, Debug)]
struct BrowserSession {
    session_token: String,
}

#[derive(Clone, Debug)]
struct HostError {
    code: &'static str,
    message: String,
    retryable: bool,
}

impl HostError {
    fn new(code: &'static str, message: impl Into<String>, retryable: bool) -> Self {
        Self {
            code,
            message: message.into(),
            retryable,
        }
    }

    fn to_json(&self) -> Value {
        json!({
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        })
    }
}

fn state_dir() -> PathBuf {
    if let Ok(value) = env::var("COWORKER_STATE_DIR") {
        return PathBuf::from(value);
    }
    #[cfg(target_os = "windows")]
    if let Ok(value) = env::var("APPDATA") {
        return PathBuf::from(value).join("coworker");
    }
    env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".config")
        .join("coworker")
}

fn descriptor_path() -> PathBuf {
    env::var("OPENWORKER_BROWSER_DESCRIPTOR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| state_dir().join("browser-native-host.json"))
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(unix)]
fn process_is_alive(pid: u32) -> bool {
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    if pid == 0 || pid > i32::MAX as u32 {
        return false;
    }
    let result = unsafe { kill(pid as i32, 0) };
    result == 0 || io::Error::last_os_error().raw_os_error() == Some(1)
}

#[cfg(target_os = "windows")]
fn process_is_alive(pid: u32) -> bool {
    type Handle = *mut std::ffi::c_void;
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, process_id: u32) -> Handle;
        fn GetExitCodeProcess(process: Handle, exit_code: *mut u32) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    if pid == 0 {
        return false;
    }
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return false;
    }
    let mut exit_code = 0;
    let ok = unsafe { GetExitCodeProcess(handle, &mut exit_code) } != 0;
    unsafe { CloseHandle(handle) };
    ok && exit_code == STILL_ACTIVE
}

#[cfg(not(any(unix, target_os = "windows")))]
fn process_is_alive(pid: u32) -> bool {
    pid != 0
}

fn validate_descriptor_file(path: &Path) -> Result<(), HostError> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| HostError::new("OPENWORKER_UNAVAILABLE", "OpenWorker is not running", true))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "OpenWorker's browser runtime descriptor is not a regular file",
            false,
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(HostError::new(
                "INSECURE_RUNTIME_DESCRIPTOR",
                "OpenWorker's browser runtime descriptor is not private",
                false,
            ));
        }
    }
    Ok(())
}

fn parse_server_url(value: &str) -> Result<ServerAddress, HostError> {
    let without_scheme = value.strip_prefix("http://127.0.0.1:").ok_or_else(|| {
        HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "The browser bridge must use numeric loopback HTTP",
            false,
        )
    })?;
    let port_text = without_scheme.strip_suffix('/').unwrap_or(without_scheme);
    if port_text.contains('/') || port_text.contains('?') || port_text.contains('#') {
        return Err(HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "The browser bridge URL must not contain a path, query, or fragment",
            false,
        ));
    }
    let port = port_text
        .parse::<u16>()
        .ok()
        .filter(|port| *port != 0)
        .ok_or_else(|| {
            HostError::new(
                "INVALID_RUNTIME_DESCRIPTOR",
                "The browser bridge URL has an invalid port",
                false,
            )
        })?;
    Ok(ServerAddress {
        host: "127.0.0.1".to_owned(),
        port,
    })
}

fn load_runtime_descriptor() -> Result<(RuntimeDescriptor, ServerAddress), HostError> {
    let path = descriptor_path();
    validate_descriptor_file(&path)?;
    let bytes = fs::read(&path).map_err(|_| {
        HostError::new(
            "OPENWORKER_UNAVAILABLE",
            "OpenWorker's browser connection could not be read",
            true,
        )
    })?;
    if bytes.len() > 16 * 1024 {
        return Err(HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "OpenWorker's browser runtime descriptor is too large",
            false,
        ));
    }
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| {
        HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "OpenWorker's browser runtime descriptor is invalid",
            false,
        )
    })?;
    if value.get("version").and_then(Value::as_u64) != Some(1) {
        return Err(HostError::new(
            "UNSUPPORTED_RUNTIME_DESCRIPTOR",
            "OpenWorker's browser runtime descriptor version is unsupported",
            false,
        ));
    }
    let server_url = value
        .get("server_url")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let api_token = value
        .get("api_token")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let pid = value
        .get("pid")
        .and_then(Value::as_u64)
        .and_then(|number| u32::try_from(number).ok())
        .unwrap_or(0);
    let expires_at = value.get("expires_at").and_then(Value::as_u64).unwrap_or(0);
    let address = parse_server_url(&server_url)?;
    if api_token.len() != 64 || !api_token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(HostError::new(
            "INVALID_RUNTIME_DESCRIPTOR",
            "OpenWorker's browser runtime token is invalid",
            false,
        ));
    }
    if !process_is_alive(pid) {
        return Err(HostError::new(
            "OPENWORKER_UNAVAILABLE",
            "The OpenWorker process that created the browser connection is no longer running",
            true,
        ));
    }
    if expires_at != 0 && expires_at < unix_now() {
        return Err(HostError::new(
            "OPENWORKER_UNAVAILABLE",
            "OpenWorker's browser connection has expired",
            true,
        ));
    }
    Ok((RuntimeDescriptor { api_token }, address))
}

fn read_frame(reader: &mut impl Read) -> io::Result<Option<Value>> {
    let mut size_bytes = [0_u8; 4];
    match reader.read_exact(&mut size_bytes) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error),
    }
    let size = u32::from_le_bytes(size_bytes) as usize;
    if size == 0 || size > MAX_EXTENSION_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid native message size",
        ));
    }
    let mut bytes = vec![0_u8; size];
    reader.read_exact(&mut bytes)?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid native message JSON"))
}

fn write_frame(writer: &mut impl Write, value: &Value) -> io::Result<()> {
    let bytes = serde_json::to_vec(value)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid response JSON"))?;
    if bytes.len() > MAX_HOST_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "native response exceeds Chrome's message limit",
        ));
    }
    writer.write_all(&(bytes.len() as u32).to_le_bytes())?;
    writer.write_all(&bytes)?;
    writer.flush()
}

fn decode_chunked(mut bytes: &[u8]) -> Result<Vec<u8>, HostError> {
    let mut output = Vec::new();
    loop {
        let Some(line_end) = bytes.windows(2).position(|part| part == b"\r\n") else {
            return Err(HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "Invalid chunked response",
                true,
            ));
        };
        let size_text = std::str::from_utf8(&bytes[..line_end])
            .map_err(|_| HostError::new("BRIDGE_PROTOCOL_ERROR", "Invalid chunk size", true))?;
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or(""), 16)
            .map_err(|_| HostError::new("BRIDGE_PROTOCOL_ERROR", "Invalid chunk size", true))?;
        bytes = &bytes[line_end + 2..];
        if size == 0 {
            return Ok(output);
        }
        if bytes.len() < size + 2 || &bytes[size..size + 2] != b"\r\n" {
            return Err(HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "Truncated chunked response",
                true,
            ));
        }
        output.extend_from_slice(&bytes[..size]);
        if output.len() > MAX_HTTP_RESPONSE_BYTES {
            return Err(HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "Browser bridge response is too large",
                false,
            ));
        }
        bytes = &bytes[size + 2..];
    }
}

fn http_post(
    descriptor: &RuntimeDescriptor,
    address: &ServerAddress,
    path: &str,
    body: &Value,
    bearer: Option<&str>,
    use_app_token: bool,
    timeout: Duration,
) -> Result<Value, HostError> {
    if let Some(token) = bearer {
        if token.len() < 32
            || token.len() > 512
            || !token.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~')
            })
        {
            return Err(HostError::new(
                "INVALID_SESSION_TOKEN",
                "OpenWorker returned an invalid browser session token",
                false,
            ));
        }
    }
    let socket = (address.host.as_str(), address.port)
        .to_socket_addrs()
        .map_err(|_| HostError::new("BRIDGE_UNAVAILABLE", "OpenWorker is not reachable", true))?
        .next()
        .ok_or_else(|| HostError::new("BRIDGE_UNAVAILABLE", "OpenWorker is not reachable", true))?;
    let mut stream = TcpStream::connect_timeout(&socket, Duration::from_secs(2))
        .map_err(|_| HostError::new("BRIDGE_UNAVAILABLE", "OpenWorker is not reachable", true))?;
    stream.set_read_timeout(Some(timeout)).ok();
    stream.set_write_timeout(Some(Duration::from_secs(10))).ok();
    let body_bytes = serde_json::to_vec(body).map_err(|_| {
        HostError::new(
            "INVALID_REQUEST",
            "Browser request could not be encoded",
            false,
        )
    })?;
    let mut headers = format!(
        "POST {path} HTTP/1.1\r\nHost: {}:{}\r\nContent-Type: application/json\r\nAccept: application/json\r\nConnection: close\r\nContent-Length: {}\r\n",
        address.host,
        address.port,
        body_bytes.len()
    );
    if use_app_token {
        headers.push_str(&format!("X-OpenWorker-Token: {}\r\n", descriptor.api_token));
    }
    if let Some(token) = bearer {
        headers.push_str(&format!("Authorization: Bearer {token}\r\n"));
    }
    headers.push_str("\r\n");
    stream
        .write_all(headers.as_bytes())
        .and_then(|_| stream.write_all(&body_bytes))
        .map_err(|_| HostError::new("BRIDGE_UNAVAILABLE", "OpenWorker is not reachable", true))?;
    let mut response = Vec::new();
    stream
        .take((MAX_HTTP_RESPONSE_BYTES + 64 * 1024) as u64)
        .read_to_end(&mut response)
        .map_err(|_| HostError::new("BRIDGE_UNAVAILABLE", "OpenWorker did not answer", true))?;
    let header_end = response
        .windows(4)
        .position(|part| part == b"\r\n\r\n")
        .ok_or_else(|| {
            HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "OpenWorker returned an invalid response",
                true,
            )
        })?;
    let header_text = std::str::from_utf8(&response[..header_end]).map_err(|_| {
        HostError::new(
            "BRIDGE_PROTOCOL_ERROR",
            "OpenWorker returned invalid headers",
            true,
        )
    })?;
    let mut lines = header_text.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| {
            HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "OpenWorker returned an invalid status",
                true,
            )
        })?;
    let headers: Vec<(String, String)> = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    let wire_body = &response[header_end + 4..];
    let decoded;
    let response_body = if headers
        .iter()
        .any(|(name, value)| name == "transfer-encoding" && value.eq_ignore_ascii_case("chunked"))
    {
        decoded = decode_chunked(wire_body)?;
        decoded.as_slice()
    } else {
        wire_body
    };
    let payload: Value = if response_body.is_empty() {
        json!({})
    } else {
        serde_json::from_slice(response_body).map_err(|_| {
            HostError::new(
                "BRIDGE_PROTOCOL_ERROR",
                "OpenWorker returned invalid JSON",
                true,
            )
        })?
    };
    if !(200..300).contains(&status) {
        let message = payload
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("OpenWorker rejected the browser request")
            .to_owned();
        let code = match status {
            401 | 403 => "UNAUTHENTICATED",
            404 => "BRIDGE_NOT_READY",
            _ if status >= 500 => "BRIDGE_UNAVAILABLE",
            _ => "BRIDGE_REJECTED",
        };
        return Err(HostError::new(
            code,
            message,
            status >= 500 || status == 404,
        ));
    }
    Ok(payload)
}

fn connect_to_server(payload: &Value) -> Result<(BrowserSession, Value), HostError> {
    let (descriptor, address) = load_runtime_descriptor()?;
    let client = payload.get("client").cloned().unwrap_or_else(|| json!({}));
    let body = json!({
        "client": client,
        "protocol_version": PROTOCOL_VERSION,
        "transport": "native_messaging",
        "extension_id": EXTENSION_ID,
    });
    let mut response = http_post(
        &descriptor,
        &address,
        "/v1/browser-extension/native/connect",
        &body,
        None,
        true,
        Duration::from_secs(10),
    )?;
    let session_id = response
        .get("session_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let session_token = response
        .get("session_token")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if session_id.is_empty() || session_token.len() < 32 {
        return Err(HostError::new(
            "INVALID_CONNECT_RESPONSE",
            "OpenWorker returned an incomplete browser connection",
            true,
        ));
    }
    if let Some(object) = response.as_object_mut() {
        object.remove("session_token");
        object.insert("browser".to_owned(), Value::String("chrome".to_owned()));
        object.insert(
            "transport".to_owned(),
            Value::String("native_messaging".to_owned()),
        );
    }
    Ok((BrowserSession { session_token }, response))
}

fn proxy_request(
    request_type: &str,
    payload: &Value,
    session: &Arc<Mutex<Option<BrowserSession>>>,
) -> Result<Value, HostError> {
    if request_type == "connect" {
        let (new_session, response) = connect_to_server(payload)?;
        *session.lock().unwrap() = Some(new_session);
        return Ok(response);
    }
    let route = match request_type {
        "poll" => "/v1/browser-extension/poll",
        "results" => "/v1/browser-extension/results",
        "events" => "/v1/browser-extension/events",
        "disconnect" => "/v1/browser-extension/disconnect",
        _ => {
            return Err(HostError::new(
                "UNSUPPORTED_REQUEST",
                "The native browser host rejected an unsupported request",
                false,
            ))
        }
    };
    let current = session.lock().unwrap().clone().ok_or_else(|| {
        HostError::new(
            "UNAUTHENTICATED",
            "The Chrome extension must reconnect to OpenWorker",
            true,
        )
    })?;
    let (descriptor, address) = load_runtime_descriptor()?;
    let timeout = if request_type == "poll" {
        Duration::from_secs(35)
    } else {
        Duration::from_secs(15)
    };
    let result = http_post(
        &descriptor,
        &address,
        route,
        payload,
        Some(&current.session_token),
        false,
        timeout,
    );
    if result
        .as_ref()
        .err()
        .is_some_and(|error| error.code == "UNAUTHENTICATED")
    {
        *session.lock().unwrap() = None;
    }
    if request_type == "disconnect" && result.is_ok() {
        *session.lock().unwrap() = None;
    }
    result
}

fn response_for(request: Value, session: &Arc<Mutex<Option<BrowserSession>>>) -> Value {
    let id = request
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if id.is_empty() {
        return json!({"id": "", "ok": false, "error": HostError::new("INVALID_REQUEST", "A request id is required", false).to_json()});
    }
    if request.get("version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return json!({"id": id, "ok": false, "error": HostError::new("UNSUPPORTED_PROTOCOL", "The extension protocol version is unsupported", false).to_json()});
    }
    let request_type = request
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let payload = request.get("payload").cloned().unwrap_or_else(|| json!({}));
    match proxy_request(request_type, &payload, session) {
        Ok(result) => json!({"id": id, "ok": true, "result": result}),
        Err(error) => json!({"id": id, "ok": false, "error": error.to_json()}),
    }
}

fn native_manifest(executable: &Path) -> Value {
    json!({
        "name": HOST_NAME,
        "description": "OpenWorker Chrome bridge",
        "path": executable.to_string_lossy(),
        "type": "stdio",
        "allowed_origins": [ALLOWED_ORIGIN],
    })
}

fn atomic_write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp = path.with_extension(format!("tmp-{}", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    #[cfg(target_os = "windows")]
    if path.exists() {
        fs::remove_file(path)?;
    }
    fs::rename(temp, path)
}

#[cfg(target_os = "macos")]
fn native_manifest_path() -> io::Result<PathBuf> {
    let home =
        env::var("HOME").map_err(|_| io::Error::new(io::ErrorKind::NotFound, "HOME is not set"))?;
    Ok(PathBuf::from(home)
        .join("Library/Application Support/Google/Chrome/NativeMessagingHosts")
        .join(format!("{HOST_NAME}.json")))
}

#[cfg(target_os = "windows")]
fn native_manifest_path() -> io::Result<PathBuf> {
    let local = env::var("LOCALAPPDATA")
        .map_err(|_| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is not set"))?;
    Ok(PathBuf::from(local)
        .join("OpenWorker/NativeMessagingHosts")
        .join(format!("{HOST_NAME}.json")))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn native_manifest_path() -> io::Result<PathBuf> {
    let home =
        env::var("HOME").map_err(|_| io::Error::new(io::ErrorKind::NotFound, "HOME is not set"))?;
    Ok(PathBuf::from(home)
        .join(".config/google-chrome/NativeMessagingHosts")
        .join(format!("{HOST_NAME}.json")))
}

fn install_native_host() -> Result<PathBuf, String> {
    let executable = env::current_exe()
        .and_then(fs::canonicalize)
        .map_err(|error| format!("Could not resolve native host path: {error}"))?;
    let path = native_manifest_path().map_err(|error| error.to_string())?;
    let bytes = serde_json::to_vec_pretty(&native_manifest(&executable))
        .map_err(|error| error.to_string())?;
    atomic_write_private(&path, &bytes).map_err(|error| error.to_string())?;
    #[cfg(target_os = "windows")]
    {
        let key = format!(r"HKCU\Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}");
        let status = Command::new("reg.exe")
            .args(["ADD", &key, "/ve", "/t", "REG_SZ", "/d"])
            .arg(&path)
            .arg("/f")
            .status()
            .map_err(|error| format!("Could not register Chrome native host: {error}"))?;
        if !status.success() {
            return Err("Chrome native host registry update failed".to_owned());
        }
    }
    Ok(path)
}

fn validate_origin() -> Result<(), String> {
    let origin = env::args()
        .skip(1)
        .find(|argument| argument.starts_with("chrome-extension://"));
    if origin.as_deref() != Some(ALLOWED_ORIGIN) {
        return Err("Chrome launched the native host for an untrusted extension origin".to_owned());
    }
    Ok(())
}

fn run_native_host() -> Result<(), String> {
    validate_origin()?;
    let reader = io::stdin();
    let writer = Arc::new(Mutex::new(BufWriter::new(io::stdout())));
    let session = Arc::new(Mutex::new(None));
    let mut reader = BufReader::new(reader.lock());
    loop {
        let Some(request) = read_frame(&mut reader).map_err(|error| error.to_string())? else {
            break;
        };
        let writer = Arc::clone(&writer);
        let session = Arc::clone(&session);
        thread::spawn(move || {
            let response = response_for(request, &session);
            if let Ok(mut output) = writer.lock() {
                let _ = write_frame(&mut *output, &response);
            }
        });
    }
    Ok(())
}

fn main() {
    let mode = env::args().nth(1);
    let result = match mode.as_deref() {
        Some("--install") => install_native_host().map(|path| {
            println!("{}", path.display());
        }),
        Some("--print-manifest") => env::current_exe()
            .map(|path| println!("{}", native_manifest(&path)))
            .map_err(|error| error.to_string()),
        _ => run_native_host(),
    };
    if let Err(error) = result {
        eprintln!("[openworker-browser-native-host] {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_numeric_loopback_without_a_path() {
        assert_eq!(
            parse_server_url("http://127.0.0.1:50300").unwrap().port,
            50300
        );
        assert!(parse_server_url("http://localhost:50300").is_err());
        assert!(parse_server_url("https://127.0.0.1:50300").is_err());
        assert!(parse_server_url("http://127.0.0.1:50300/anything").is_err());
    }

    #[test]
    fn manifest_is_restricted_to_the_stable_extension_origin() {
        let manifest = native_manifest(Path::new("/Applications/OpenWorker.app/host"));
        assert_eq!(manifest["name"], HOST_NAME);
        assert_eq!(manifest["allowed_origins"], json!([ALLOWED_ORIGIN]));
        assert_eq!(manifest["allowed_origins"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn native_frames_round_trip() {
        let value = json!({"version": 1, "id": "request-1", "type": "events"});
        let mut encoded = Vec::new();
        write_frame(&mut encoded, &value).unwrap();
        assert_eq!(read_frame(&mut encoded.as_slice()).unwrap(), Some(value));
    }

    #[test]
    fn chunked_http_bodies_decode() {
        assert_eq!(
            decode_chunked(b"4\r\ntest\r\n3\r\n123\r\n0\r\n\r\n").unwrap(),
            b"test123"
        );
    }
}
