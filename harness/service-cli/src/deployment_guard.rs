//! Deterministic, uncompressed ZIP export; never uploads or invokes the guard.
use super::*;
#[derive(Subcommand)]
pub enum Command {
    Package {
        #[arg(long)]
        out: PathBuf,
    },
}
fn crc32(bytes: &[u8]) -> u32 {
    let mut c = !0u32;
    for b in bytes {
        c ^= u32::from(*b);
        for _ in 0..8 {
            c = (c >> 1) ^ if c & 1 == 1 { 0xedb88320 } else { 0 };
        }
    }
    !c
}
fn zip(name: &[u8], data: &[u8]) -> Vec<u8> {
    fn u16b(v: &mut Vec<u8>, n: u16) {
        v.extend(n.to_le_bytes())
    }
    fn u32b(v: &mut Vec<u8>, n: u32) {
        v.extend(n.to_le_bytes())
    }
    let mut v = vec![];
    let crc = crc32(data);
    let size = data.len() as u32;
    u32b(&mut v, 0x04034b50);
    for n in [20, 0, 0, 0, 33] {
        u16b(&mut v, n)
    }
    u32b(&mut v, crc);
    u32b(&mut v, size);
    u32b(&mut v, size);
    u16b(&mut v, name.len() as u16);
    u16b(&mut v, 0);
    v.extend(name);
    v.extend(data);
    let offset = v.len() as u32;
    u32b(&mut v, 0x02014b50);
    for n in [0x0314, 20, 0, 0, 0, 33] {
        u16b(&mut v, n)
    }
    u32b(&mut v, crc);
    u32b(&mut v, size);
    u32b(&mut v, size);
    u16b(&mut v, name.len() as u16);
    for _ in 0..4 {
        u16b(&mut v, 0)
    }
    u32b(&mut v, 0o100600 << 16);
    u32b(&mut v, 0);
    v.extend(name);
    let central = v.len() as u32 - offset;
    u32b(&mut v, 0x06054b50);
    for n in [0, 0, 1, 1] {
        u16b(&mut v, n)
    }
    u32b(&mut v, central);
    u32b(&mut v, offset);
    u16b(&mut v, 0);
    v
}
pub(super) fn archive_sha() -> String {
    hash(&zip(
        b"index.js",
        include_bytes!("../../../deploy/service/guard/index.cjs"),
    ))
}
pub(super) fn run(command: &Command) -> Result<()> {
    let Command::Package { out } = command;
    let source = include_bytes!("../../../deploy/service/guard/index.cjs");
    let archive = zip(b"index.js", source);
    let mut d = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        d.mode(0o700);
    }
    d.create(out)
        .context("Guard output directory must not exist")?;
    let manifest = json!({"schemaVersion":1,"entrypoint":"index.handler","runtime":"nodejs22.x","archiveSha256":hash(&archive),"sourceSha256":hash(source),"guardControlVersion":1,"runtimeProvidedDependencies":["@aws-sdk/client-cloudformation","@aws-sdk/client-s3","@aws-sdk/client-ec2","@aws-sdk/client-dynamodb"],"requiresLiveRuntimeQualification":true,"uploaded":false});
    for (name, bytes) in [
        ("cleanup.zip", archive),
        ("package-manifest.json", json_bytes(&manifest)?),
    ] {
        let mut o = fs::OpenOptions::new();
        o.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        let mut file = o.open(out.join(name))?;
        file.write_all(&bytes)?;
        file.sync_all()?;
    }
    crate::output(manifest)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn zip_is_deterministic_and_crc_is_standard() {
        assert_eq!(crc32(b"123456789"), 0xcbf43926);
        assert_eq!(zip(b"index.js", b"guard"), zip(b"index.js", b"guard"));
        let v = zip(b"index.js", b"guard");
        assert_eq!(&v[..4], b"PK\x03\x04");
        assert_eq!(&v[v.len() - 22..v.len() - 18], b"PK\x05\x06");
    }
}
