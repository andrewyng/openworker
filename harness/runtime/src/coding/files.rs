use anyhow::{bail, ensure, Context, Result};
use serde_json::{json, Value};
use std::{
    io::Write,
    path::{Path, PathBuf},
};
const MAX_FILE: u64 = 1024 * 1024;
const LOCK: &str = ".openworker-coding.lock";
pub(super) struct EditLock(std::fs::File);
impl Drop for EditLock {
    fn drop(&mut self) {
        // Closing this descriptor alone can leave an OFD lock held by an
        // unrelated concurrently forked child until its CLOEXEC/exit. Release
        // explicitly so the next edit does not spuriously see a busy lock.
        let _ = fs2::FileExt::unlock(&self.0);
    }
}
pub(super) fn edit_lock(root: &Path) -> Result<EditLock> {
    let path = crate::runner::path(root, LOCK)?;
    if let Ok(meta) = path.symlink_metadata() {
        ensure!(
            meta.is_file() && !crate::platform::linked(&meta),
            "Regular lock file required"
        );
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = options.open(path)?;
    let meta = file.metadata()?;
    ensure!(
        meta.is_file() && !crate::platform::linked(&meta),
        "Regular lock file required"
    );
    fs2::FileExt::try_lock_exclusive(&file)
        .context("Another coding edit is in progress; retry after reading the file")?;
    Ok(EditLock(file))
}
#[cfg(all(test, unix))]
mod lock_tests {
    use super::*;
    #[test]
    fn completed_edit_unlocks_while_a_fork_inherited_descriptor_still_exists() {
        let root = tempfile::tempdir().unwrap();
        let owner = edit_lock(root.path()).unwrap();
        assert!(edit_lock(root.path()).is_err());
        // dup shares the open file description exactly as fork does. No sleeps,
        // actual fork, child cleanup or scheduler luck is needed for this case.
        let inherited = owner.0.try_clone().unwrap();
        drop(owner);
        let next = edit_lock(root.path()).unwrap();
        drop(inherited);
        assert!(edit_lock(root.path()).is_err());
        drop(next);
        assert!(edit_lock(root.path()).is_ok());
    }
}
pub(super) fn text(path: &Path) -> Result<String> {
    String::from_utf8(crate::bundle::read_limited(path, MAX_FILE as usize)?)
        .context("UTF-8 file required (maximum 1 MiB)")
}
fn bounded(v: &Value, key: &str, max: usize) -> Result<String> {
    Ok(crate::runner::bounded_text(v, key, max)?.to_owned())
}
fn hash(s: &str) -> String {
    crate::bundle::digest(s.as_bytes())
}
fn expected(args: &Value, current: &str) -> Result<()> {
    if let Some(value) = args.get("expected_sha256") {
        ensure!(
            value == &hash(current),
            "File changed; read it again before editing"
        );
    }
    Ok(())
}
pub(super) fn write(root: &Path, relative: &str, content: &str, create_only: bool) -> Result<()> {
    ensure!(content.len() <= MAX_FILE as usize, "File exceeds 1 MiB");
    let target = crate::runner::path(root, relative)?;
    let parent = target.parent().context("Parent required")?;
    std::fs::create_dir_all(parent)?;
    crate::runner::path(root, relative)?;
    let temp = parent.join(format!(".ow-edit-{}", uuid::Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut file = opts.open(&temp)?;
        // Preserve executable bits on existing source/scripts.
        if !create_only {
            if let Ok(meta) = std::fs::metadata(&target) {
                file.set_permissions(meta.permissions())?;
            }
        }
        file.write_all(content.as_bytes())?;
        file.sync_all()?;
        drop(file);
        crate::runner::path(root, relative)?;
        if create_only {
            std::fs::hard_link(&temp, &target)?;
        } else {
            std::fs::rename(&temp, &target)?;
        }
        Ok(())
    })();
    let _ = std::fs::remove_file(temp);
    result
}
fn files(root: &Path, args: &Value) -> Result<(Vec<PathBuf>, bool)> {
    let relative = args["path"].as_str().unwrap_or(".");
    let start = if relative == "." {
        root.to_owned()
    } else {
        crate::runner::path(root, relative)?
    };
    ensure!(start.is_dir(), "Directory required");
    // Walk from the workspace to retain its ignore rules for subdirectory searches,
    // pruning unrelated branches without consulting parents outside the workspace.
    let max_depth = start.strip_prefix(root)?.components().count()
        + if args["recursive"] == false { 1 } else { 16 };
    let mut walker = ignore::WalkBuilder::new(root);
    walker
        .hidden(args["include_hidden"] != true)
        .parents(false)
        .git_global(false)
        .git_ignore(args["respect_gitignore"] != false)
        .git_exclude(args["respect_gitignore"] != false)
        .ignore(args["respect_gitignore"] != false)
        .require_git(false)
        .follow_links(false)
        .max_depth(Some(max_depth));
    let filter = if let Some(glob) = args["glob"].as_str() {
        ensure!(glob.len() <= 1000, "Glob too large");
        Some(
            globset::Glob::new(glob)
                .context("Invalid file glob")?
                .compile_matcher(),
        )
    } else {
        None
    };
    let selected = start.clone();
    walker.filter_entry(move |e| {
        (selected.starts_with(e.path()) || e.path().starts_with(&selected))
            && !matches!(
                e.file_name().to_str(),
                Some(".git" | "node_modules" | "target" | ".openworker-coding.lock")
            )
    });
    let mut out = vec![];
    let mut truncated = false;
    for (n, entry) in walker.build().enumerate() {
        if n >= 10000 {
            truncated = true;
            break;
        }
        let e = entry.context("Cannot traverse directory")?;
        if e.depth() == max_depth && e.file_type().is_some_and(|t| t.is_dir()) {
            truncated = true;
        }
        if e.file_type().is_some_and(|t| t.is_file())
            && !crate::platform::linked(&e.metadata()?)
            && filter
                .as_ref()
                .is_none_or(|f| f.is_match(e.path().strip_prefix(&start).unwrap_or(e.path())))
        {
            out.push(e.into_path());
        }
    }
    out.sort();
    Ok((out, truncated))
}
pub fn call(root: &Path, name: &str, args: &Value) -> Result<Value> {
    match name {
        "read_file" => {
            let p = crate::runner::path(root, args["path"].as_str().context("Path required")?)?;
            let content = text(&p)?;
            let offset = args["offset"].as_u64().unwrap_or(0).min(usize::MAX as u64) as usize;
            let limit = args["limit"].as_u64().unwrap_or(200) as usize;
            let all: Vec<_> = content.lines().collect();
            let mut lines = vec![];
            let mut bytes = 0;
            let mut clipped = false;
            for line in all.iter().skip(offset).take(limit) {
                let part: String = line.chars().take(4000).collect();
                let size = serde_json::to_string(&part)?.len();
                if bytes + size > 24000 {
                    break;
                }
                clipped |= part.len() < line.len();
                bytes += size;
                lines.push(part);
            }
            Ok(
                json!({"path":args["path"],"sha256":hash(&content),"offset":offset,"lines":lines,"total_lines":all.len(),"long_lines_truncated":clipped,"truncated":offset.saturating_add(lines.len()) < all.len()}),
            )
        }
        "write_file" | "edit_file" => {
            let relative = args["path"].as_str().context("Path required")?;
            ensure!(relative != LOCK, "Reserved coding lock file");
            let _lock = edit_lock(root)?;
            let p = crate::runner::path(root, relative)?;
            let content = if name == "edit_file" {
                let current = text(&p)?;
                expected(args, &current)?;
                let old = bounded(args, "old_text", MAX_FILE as usize)?;
                ensure!(
                    !old.is_empty() && current.matches(&old).count() == 1,
                    "old_text must match exactly once"
                );
                current.replacen(&old, &bounded(args, "new_text", MAX_FILE as usize)?, 1)
            } else {
                if args.get("expected_sha256").is_some() {
                    expected(args, &text(&p)?)?;
                }
                bounded(args, "content", MAX_FILE as usize)?
            };
            write(root, relative, &content, args["create_only"] == true)?;
            Ok(
                json!({"path":relative,"bytes":content.len(),"sha256":hash(&content),"written":true}),
            )
        }
        "list_files" | "search_files" => {
            let (paths, mut truncated) = files(root, args)?;
            let limit = args["limit"].as_u64().unwrap_or(200) as usize;
            if name == "list_files" {
                truncated |= paths.len() > limit;
                let files: Vec<_> = paths
                    .iter()
                    .take(limit)
                    .map(|p| p.strip_prefix(root).unwrap().to_string_lossy().into_owned())
                    .collect();
                let mut files = files;
                while serde_json::to_vec(&files)?.len() > 24000 {
                    files.pop();
                    truncated = true;
                }
                return Ok(json!({"files":files,"truncated":truncated}));
            }
            let query = bounded(args, "query", 1000)?;
            ensure!(!query.is_empty(), "Nonempty query required");
            let pattern = if args["regex"] == true {
                query
            } else {
                regex::escape(&query)
            };
            let pattern = regex::RegexBuilder::new(&pattern)
                .case_insensitive(args["case_sensitive"] == false)
                .size_limit(1_000_000)
                .dfa_size_limit(1_000_000)
                .build()
                .context("Invalid or oversized search expression")?;
            let mut matches = vec![];
            let mut read = 0;
            let mut skipped = 0;
            'scan: for p in paths {
                if read >= 16 * MAX_FILE {
                    truncated = true;
                    break;
                }
                let rel = p.strip_prefix(root)?;
                crate::runner::path(root, rel.to_str().context("UTF-8 path required")?)?;
                let Ok(content) = text(&p) else {
                    skipped += 1;
                    continue;
                };
                read += content.len() as u64;
                if content.contains('\0') {
                    skipped += 1;
                    continue;
                }
                for (i, line) in content.lines().enumerate() {
                    if pattern.is_match(line) {
                        if matches.len() >= limit {
                            truncated = true;
                            break 'scan;
                        }
                        matches.push(json!({"path":rel,"line":i+1,"text":line.chars().take(240).collect::<String>()}));
                        if serde_json::to_vec(&matches)?.len() > 48000 {
                            matches.pop();
                            truncated = true;
                            break 'scan;
                        }
                    }
                }
            }
            Ok(json!({"matches":matches,"truncated":truncated,"skipped_files":skipped}))
        }
        _ => bail!("Unknown file tool"),
    }
}
