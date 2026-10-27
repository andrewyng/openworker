//! Small OS boundary. Host lifecycle and private files are not a kernel sandbox.
use anyhow::{bail, Result};
use std::path::Path;
#[cfg(windows)]
pub mod windows;

/// Bundle identities and skill inventory names use portable '/' separators.
pub fn relative_key(path: &Path) -> Result<String> {
    let text = path
        .to_str()
        .ok_or_else(|| anyhow::anyhow!("UTF-8 package path required"))?;
    #[cfg(windows)]
    {
        Ok(text.replace('\\', "/"))
    }
    #[cfg(not(windows))]
    {
        Ok(text.into())
    }
}

pub fn linked(metadata: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}
pub fn private_directory(path: &Path) -> Result<()> {
    validate_path(path)?;
    if std::path::absolute(path)?.parent().is_none() {
        bail!("A filesystem root cannot be a private state directory");
    }
    #[cfg(windows)]
    for ancestor in path.ancestors() {
        if std::fs::symlink_metadata(ancestor).is_ok_and(|m| linked(&m)) {
            bail!("Private directory ancestor is a reparse point");
        }
    }
    std::fs::create_dir_all(path)?;
    if linked(&std::fs::symlink_metadata(path)?) {
        bail!("Private directory cannot be a link/reparse point");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(windows)]
    windows::private_directory(path)?;
    Ok(())
}
/// No device names, alternate data streams, trailing-dot/space aliases or UNC
/// roots for this local host profile. Windows policy roots must be local drives.
pub fn validate_path(path: &Path) -> Result<()> {
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        if path.components().any(|c| matches!(c, Component::Prefix(_))) && !path.is_absolute() {
            bail!("Drive-relative Windows paths are rejected");
        }
        for c in path.components() {
            match c {
                Component::Prefix(p)
                    if !matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)) =>
                {
                    bail!("Only local drive paths are supported")
                }
                Component::Normal(s) => {
                    let s = s
                        .to_str()
                        .ok_or_else(|| anyhow::anyhow!("UTF-8 path required"))?;
                    let stem = s.split('.').next().unwrap_or("").to_ascii_uppercase();
                    if s.contains(':')
                        || s.ends_with(['.', ' '])
                        || s.bytes().any(|b| b < 32)
                        || matches!(
                            stem.as_str(),
                            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
                        )
                        || (stem.len() == 4
                            && (stem.starts_with("COM") || stem.starts_with("LPT"))
                            && matches!(stem.as_bytes()[3], b'1'..=b'9'))
                    {
                        bail!("Device/stream/ambiguous Windows path rejected");
                    }
                }
                _ => {}
            }
        }
    }
    #[cfg(not(windows))]
    let _ = path;
    Ok(())
}
/// OpenShell executes its pinned runner inside Linux, even when the controller
/// is a Mac. Unsandboxed runners execute on the host and retain host validation.
pub fn runner_executable(bytes: &[u8], backend: crate::execution::Backend) -> Result<()> {
    match backend {
        crate::execution::Backend::Openshell => {
            if !bytes.starts_with(b"\x7fELF") {
                bail!("OpenShell runner must be a Linux ELF executable");
            }
            Ok(())
        }
        crate::execution::Backend::Off => executable(bytes),
    }
}

#[cfg(test)]
mod runner_tests {
    use super::*;
    #[test]
    fn runner_validation_uses_execution_location() {
        use crate::execution::Backend;
        assert!(runner_executable(b"\x7fELF", Backend::Openshell).is_ok());
        assert!(runner_executable(b"#!/bin/sh", Backend::Openshell).is_err());
        assert!(runner_executable(b"\xcf\xfa\xed\xfe", Backend::Openshell).is_err());
        #[cfg(target_os = "macos")]
        {
            assert!(runner_executable(b"\x7fELF", Backend::Off).is_err());
            assert!(runner_executable(b"\xcf\xfa\xed\xfe", Backend::Off).is_ok());
        }
    }
}

pub fn executable(bytes: &[u8]) -> Result<()> {
    #[cfg(windows)]
    {
        let offset = bytes
            .get(0x3c..0x40)
            .map(|b| u32::from_le_bytes(b.try_into().unwrap()) as usize);
        if !bytes.starts_with(b"MZ")
            || offset.and_then(|p| bytes.get(p..p + 4)) != Some(b"PE\0\0".as_slice())
        {
            bail!("Runner must be a native Windows PE executable");
        }
    }
    #[cfg(target_os = "macos")]
    if ![
        b"\xcf\xfa\xed\xfe".as_slice(),
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
    ]
    .iter()
    .any(|magic| bytes.starts_with(magic))
    {
        bail!("Runner must be a native macOS Mach-O executable");
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    if !bytes.starts_with(b"\x7fELF") {
        bail!("Sandbox runner must be a native Linux ELF binary (not a script/Python/Mach-O)");
    }
    Ok(())
}

pub fn clean_environment(cmd: &mut std::process::Command, workspace: &Path) {
    cmd.env_clear();
    #[cfg(windows)]
    for key in ["SystemRoot", "WINDIR", "SystemDrive"] {
        if let Some(value) = std::env::var_os(key) {
            cmd.env(key, value);
        }
    }
    #[cfg(windows)]
    cmd.env("TEMP", workspace)
        .env("TMP", workspace)
        .env("USERPROFILE", workspace);
    #[cfg(not(windows))]
    cmd.env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("LANG", "C.UTF-8");
    cmd.env("HOME", workspace);
}
