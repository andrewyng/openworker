use super::*;
use ::windows::{
    core::{w, PCWSTR},
    Win32::{
        Graphics::Gdi::*,
        Media::Audio::{Endpoints::*, *},
        System::{Com::*, Power::*},
        UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL},
    },
};
fn unavailable() -> ToolError {
    ToolError::new(
        "device_unavailable",
        "Windows could not access this device or capability. No simulated result was substituted.",
    )
}
pub(super) fn execute(action: Action, args: Value) -> Result<Value, ToolError> {
    match action {
        Action::Computer => unsafe {
            let mut power = SYSTEM_POWER_STATUS::default();
            let power_ok = GetSystemPowerStatus(&mut power).is_ok();
            let mut displays = vec![];
            for index in 0..32 {
                let mut device = DISPLAY_DEVICEW {
                    cb: std::mem::size_of::<DISPLAY_DEVICEW>() as u32,
                    ..Default::default()
                };
                if !EnumDisplayDevicesW(None, index, &mut device, 0).as_bool() {
                    break;
                }
                if device
                    .StateFlags
                    .contains(DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
                {
                    let end = device
                        .DeviceString
                        .iter()
                        .position(|&c| c == 0)
                        .unwrap_or(device.DeviceString.len());
                    displays.push(json!({"name":String::from_utf16_lossy(&device.DeviceString[..end]),"primary":device.StateFlags.contains(DISPLAY_DEVICE_PRIMARY_DEVICE)}));
                }
            }
            Ok(
                json!({"platform":"windows","battery_percent":if power_ok && power.BatteryLifePercent<=100{Some(power.BatteryLifePercent)}else{None},"battery_present":power_ok && power.BatteryFlag!=255 && power.BatteryFlag & 128==0,"plugged_in":if power_ok && power.ACLineStatus<=1 {Some(power.ACLineStatus==1)}else{None},"displays":displays}),
            )
        },
        Action::Audio | Action::Volume => unsafe {
            CoInitializeEx(None, COINIT_MULTITHREADED)
                .ok()
                .map_err(|_| unavailable())?;
            struct Com;
            impl Drop for Com {
                fn drop(&mut self) {
                    unsafe {
                        CoUninitialize();
                    }
                }
            }
            let _com = Com;
            let enumerator: IMMDeviceEnumerator =
                CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)
                    .map_err(|_| unavailable())?;
            let device = enumerator
                .GetDefaultAudioEndpoint(eRender, eMultimedia)
                .map_err(|_| unavailable())?;
            let endpoint: IAudioEndpointVolume = device
                .Activate(CLSCTX_ALL, None)
                .map_err(|_| unavailable())?;
            let before = endpoint
                .GetMasterVolumeLevelScalar()
                .map_err(|_| unavailable())?;
            if matches!(action, Action::Volume) {
                endpoint
                    .SetMasterVolumeLevelScalar(
                        args["percent"].as_u64().unwrap() as f32 / 100.0,
                        std::ptr::null(),
                    )
                    .map_err(|_| unavailable())?;
            }
            let after=endpoint.GetMasterVolumeLevelScalar().map_err(|_|ToolError::new("verification_failed","Could not read back volume; change may have occurred, do not automatically retry"))?;
            let mute = endpoint.GetMute().map_err(|_| unavailable())?.as_bool();
            Ok(
                json!({"volume_percent":(after*100.0).round() as u32,"muted":mute,"before_percent":(before*100.0).round() as u32,"changed":matches!(action,Action::Volume)}),
            )
        },
        Action::Settings => unsafe {
            let uri: PCWSTR = match args["page"].as_str() {
                Some("sound") => w!("ms-settings:sound"),
                Some("display") => w!("ms-settings:display"),
                Some("wifi") => w!("ms-settings:network-wifi"),
                _ => return Err(unavailable()),
            };
            let code = ShellExecuteW(None, w!("open"), uri, None, None, SW_SHOWNORMAL);
            if code.0 as isize <= 32 {
                return Err(ToolError::new(
                    "launch_failed",
                    "Windows settings could not be opened",
                ));
            }
            Ok(json!({"page":args["page"],"launch_requested":true,"settings_changed":false}))
        },
    }
}
