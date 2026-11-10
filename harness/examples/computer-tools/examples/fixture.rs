//! Harmless interactive Windows fixture for real input/capture acceptance.
//! No network, credentials, files or commands are exposed in this window.
#[cfg(windows)]
fn main() {
    use std::{
        io::{self, Write},
        ptr::{null, null_mut},
    };
    use windows_sys::Win32::{
        Foundation::*,
        Graphics::Gdi::*,
        UI::{HiDpi::*, Input::KeyboardAndMouse::*, WindowsAndMessaging::*},
    };
    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(Some(0)).collect()
    }
    unsafe {
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let w = GetSystemMetrics(SM_CXSCREEN);
        let h = GetSystemMetrics(SM_CYSCREEN);
        let main = CreateWindowExW(
            WS_EX_TOPMOST,
            wide("STATIC").as_ptr(),
            wide("Luma harmless computer-use fixture").as_ptr(),
            WS_POPUP | WS_VISIBLE | WS_CLIPCHILDREN,
            0,
            0,
            w,
            h,
            null_mut(),
            null_mut(),
            null_mut(),
            null(),
        );
        assert!(!main.is_null());
        let label = CreateWindowExW(
            0,
            wide("STATIC").as_ptr(),
            wide("LUMA TEST WINDOW — synthetic content only. The verification word is BLUEBERRY.")
                .as_ptr(),
            WS_CHILD | WS_VISIBLE,
            40,
            30,
            w - 80,
            50,
            main,
            null_mut(),
            null_mut(),
            null(),
        );
        assert!(!label.is_null());
        let font = CreateFontW(
            42,
            0,
            0,
            0,
            600,
            0,
            0,
            0,
            DEFAULT_CHARSET as u32,
            0,
            0,
            0,
            0,
            wide("Segoe UI").as_ptr(),
        );
        SendMessageW(label, WM_SETFONT, font as usize, 1);
        let edit = CreateWindowExW(
            WS_EX_CLIENTEDGE,
            wide("EDIT").as_ptr(),
            wide("").as_ptr(),
            WS_CHILD
                | WS_VISIBLE
                | WS_TABSTOP
                | WS_VSCROLL
                | ES_MULTILINE as u32
                | ES_AUTOVSCROLL as u32,
            40,
            110,
            w - 80,
            h - 180,
            main,
            null_mut(),
            null_mut(),
            null(),
        );
        assert!(!edit.is_null());
        SetForegroundWindow(main);
        SetFocus(edit);
        println!(
            "{{\"window\":{},\"edit\":{},\"width\":{},\"height\":{}}}",
            main as usize, edit as usize, w, h
        );
        io::stdout().flush().unwrap();
        let target = edit as usize;
        std::thread::spawn(move || {
            use std::io::BufRead;
            let mut other: HWND = null_mut();
            for line in io::stdin().lock().lines() {
                match line.unwrap_or_default().as_str() {
                    "read" => {
                        let mut b = vec![0u16; 2048];
                        let n = GetWindowTextW(target as HWND, b.as_mut_ptr(), b.len() as i32);
                        println!(
                            "{}",
                            serde_json::json!({"text":String::from_utf16_lossy(&b[..n as usize])})
                        );
                        io::stdout().flush().unwrap();
                    }
                    "focus" => {
                        if !other.is_null() {
                            DestroyWindow(other);
                            other = null_mut();
                        }
                        // Focus belongs to the window's UI thread, not this
                        // stdin thread. Acknowledge only after it is verified.
                        PostMessageW(GetParent(target as HWND), WM_APP + 1, 0, 0);
                    }
                    "blur" => {
                        other = CreateWindowExW(
                            WS_EX_TOPMOST,
                            wide("STATIC").as_ptr(),
                            wide("Second synthetic test window").as_ptr(),
                            WS_POPUP | WS_VISIBLE,
                            0,
                            0,
                            500,
                            300,
                            null_mut(),
                            null_mut(),
                            null_mut(),
                            null(),
                        );
                        SetForegroundWindow(other);
                        println!("{{\"blurred\":true}}");
                        io::stdout().flush().unwrap();
                    }
                    "stop" => {
                        let event = |key, flags| INPUT {
                            r#type: INPUT_KEYBOARD,
                            Anonymous: INPUT_0 {
                                ki: KEYBDINPUT {
                                    wVk: key,
                                    wScan: 0,
                                    dwFlags: flags,
                                    time: 0,
                                    dwExtraInfo: 0,
                                },
                            },
                        };
                        let down = [event(VK_CONTROL, 0), event(VK_MENU, 0), event(VK_ESCAPE, 0)];
                        SendInput(3, down.as_ptr(), std::mem::size_of::<INPUT>() as i32);
                        std::thread::sleep(std::time::Duration::from_millis(150));
                        let up = [
                            event(VK_ESCAPE, KEYEVENTF_KEYUP),
                            event(VK_MENU, KEYEVENTF_KEYUP),
                            event(VK_CONTROL, KEYEVENTF_KEYUP),
                        ];
                        SendInput(3, up.as_ptr(), std::mem::size_of::<INPUT>() as i32);
                        println!("{{\"stop_shortcut_sent\":true}}");
                        io::stdout().flush().unwrap();
                    }
                    "exit" => std::process::exit(0),
                    _ => {}
                }
            }
            std::process::exit(0);
        });
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, null_mut(), 0, 0) > 0 {
            if msg.message == WM_APP + 1 {
                SetForegroundWindow(main);
                SetFocus(edit);
                println!(
                    "{}",
                    serde_json::json!({"focused": GetForegroundWindow() == main && GetFocus() == edit})
                );
                io::stdout().flush().unwrap();
                continue;
            }
            // Classic Win32 EDIT does not implement Ctrl+A by itself. This test
            // app deliberately implements the familiar editor shortcut.
            if msg.hwnd == edit
                && msg.message == WM_KEYDOWN
                && msg.wParam == b'A' as usize
                && GetKeyState(VK_CONTROL as i32) < 0
            {
                SendMessageW(edit, 0x00B1 /* EM_SETSEL */, 0, -1);
                continue;
            }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}
#[cfg(not(windows))]
fn main() {
    eprintln!("Windows-only fixture");
}
