use anyhow::{bail, ensure, Context, Result};
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::Path};
#[derive(Default)]
struct Hunk {
    old: Vec<String>,
    new: Vec<String>,
    at: Option<usize>,
    new_at: Option<usize>,
    end: bool,
}
enum Kind {
    Add,
    Update,
    Delete,
}
struct Edit {
    path: String,
    kind: Kind,
    hunks: Vec<Hunk>,
}
fn path(s: &str) -> Result<String> {
    crate::runner::relative_path(s)?;
    ensure!(
        !s.eq_ignore_ascii_case(".openworker-coding.lock"),
        "Reserved file"
    );
    Ok(s.into())
}
fn codex(lines: &[&str]) -> Result<Vec<Edit>> {
    ensure!(
        lines.first() == Some(&"*** Begin Patch") && lines.last() == Some(&"*** End Patch"),
        "Patch envelope required"
    );
    let mut result = vec![];
    let mut i = 1;
    while i + 1 < lines.len() {
        let (kind, name) = if let Some(p) = lines[i].strip_prefix("*** Update File: ") {
            (Kind::Update, p)
        } else if let Some(p) = lines[i].strip_prefix("*** Add File: ") {
            (Kind::Add, p)
        } else if let Some(p) = lines[i].strip_prefix("*** Delete File: ") {
            (Kind::Delete, p)
        } else {
            bail!("Expected Add/Update/Delete File; moves are unsupported")
        };
        let mut edit = Edit {
            path: path(name)?,
            kind,
            hunks: vec![],
        };
        i += 1;
        if matches!(edit.kind, Kind::Add) {
            let mut h = Hunk::default();
            while i < lines.len() && !lines[i].starts_with("*** ") {
                h.new.push(
                    lines[i]
                        .strip_prefix('+')
                        .context("Added file lines must start with +")?
                        .into(),
                );
                i += 1;
            }
            edit.hunks.push(h);
        } else if matches!(edit.kind, Kind::Update) {
            while i < lines.len() && !lines[i].starts_with("*** ") {
                ensure!(
                    lines[i] == "@@",
                    "Codex hunks require @@ with exact context (no fuzzy section headings)"
                );
                i += 1;
                let mut h = Hunk::default();
                while i < lines.len() && lines[i] != "@@" && !lines[i].starts_with("*** ") {
                    let line = lines[i];
                    ensure!(!line.is_empty(), "Hunk lines require a prefix");
                    match line.as_bytes()[0] {
                        b' ' => {
                            h.old.push(line[1..].into());
                            h.new.push(line[1..].into());
                        }
                        b'-' => h.old.push(line[1..].into()),
                        b'+' => h.new.push(line[1..].into()),
                        _ => bail!("Invalid patch line"),
                    }
                    i += 1;
                }
                if lines.get(i) == Some(&"*** End of File") {
                    h.end = true;
                    i += 1;
                }
                ensure!(!h.old.is_empty(), "Update hunks need exact old context");
                edit.hunks.push(h);
            }
            ensure!(!edit.hunks.is_empty(), "Empty update");
        }
        result.push(edit);
    }
    Ok(result)
}
fn unified(lines: &[&str]) -> Result<Vec<Edit>> {
    let header = regex::Regex::new(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")?;
    let mut edits = vec![];
    let mut i = 0;
    while i < lines.len() {
        if lines[i].starts_with("diff --git ") || lines[i].starts_with("index ") {
            i += 1;
            continue;
        }
        let old = lines[i]
            .strip_prefix("--- ")
            .context("Expected unified --- header")?
            .split('\t')
            .next()
            .unwrap();
        i += 1;
        let new = lines
            .get(i)
            .and_then(|s| s.strip_prefix("+++ "))
            .context("Expected unified +++ header")?
            .split('\t')
            .next()
            .unwrap();
        i += 1;
        let old = old.strip_prefix("a/").unwrap_or(old);
        let new = new.strip_prefix("b/").unwrap_or(new);
        let (kind, p) = if old == "/dev/null" {
            (Kind::Add, new)
        } else if new == "/dev/null" {
            (Kind::Delete, old)
        } else {
            ensure!(old == new, "Renames are unsupported");
            (Kind::Update, new)
        };
        let mut edit = Edit {
            path: path(p)?,
            kind,
            hunks: vec![],
        };
        while i < lines.len() && lines[i].starts_with("@@") {
            let captures = header
                .captures(lines[i])
                .context("Invalid unified hunk header")?;
            let n = |idx| -> Result<usize> {
                Ok(captures.get(idx).map_or("1", |m| m.as_str()).parse()?)
            };
            let start = n(1)?;
            let old_count = n(2)?;
            let new_start = n(3)?;
            let new_count = n(4)?;
            ensure!(
                (old_count == 0 || start > 0) && (new_count == 0 || new_start > 0),
                "Invalid hunk line number"
            );
            i += 1;
            let mut h = Hunk {
                new_at: Some(if new_count == 0 {
                    new_start
                } else {
                    new_start - 1
                }),
                at: Some(if old_count == 0 {
                    start
                } else {
                    start.saturating_sub(1)
                }),
                ..Default::default()
            };
            while h.old.len() < old_count || h.new.len() < new_count {
                let line = *lines.get(i).context("Incomplete hunk")?;
                let prefix = line.chars().next().context("Hunk lines need a prefix")?;
                match prefix {
                    ' ' => {
                        h.old.push(line[1..].into());
                        h.new.push(line[1..].into());
                    }
                    '-' => h.old.push(line[1..].into()),
                    '+' => h.new.push(line[1..].into()),
                    _ => bail!("Unsupported hunk line; no-newline markers are not accepted"),
                }
                ensure!(
                    h.old.len() <= old_count && h.new.len() <= new_count,
                    "Hunk count mismatch"
                );
                i += 1;
            }
            edit.hunks.push(h);
        }
        ensure!(!edit.hunks.is_empty(), "Unified patch needs hunks");
        edits.push(edit);
    }
    Ok(edits)
}
fn render(edit: &Edit, original: Option<&str>) -> Result<Option<String>> {
    match edit.kind {
        Kind::Add => {
            ensure!(original.is_none(), "Added file already exists");
            ensure!(
                edit.hunks.len() == 1 && edit.hunks[0].new_at.is_none_or(|n| n == 0),
                "Added file requires one hunk starting at line one"
            );
            ensure!(
                edit.hunks
                    .iter()
                    .all(|h| h.old.is_empty() && h.at.is_none_or(|n| n == 0)),
                "Added file hunks must start from an empty file"
            );
            let has_lines = edit.hunks.iter().any(|h| !h.new.is_empty());
            let content = edit
                .hunks
                .iter()
                .flat_map(|h| h.new.iter())
                .cloned()
                .collect::<Vec<_>>()
                .join("\n");
            Ok(Some(if !has_lines {
                content
            } else {
                format!("{content}\n")
            }))
        }
        Kind::Delete => {
            let old = original.context("Deleted file does not exist")?;
            if !edit.hunks.is_empty() {
                let u = Edit {
                    path: edit.path.clone(),
                    kind: Kind::Update,
                    hunks: edit
                        .hunks
                        .iter()
                        .map(|h| Hunk {
                            old: h.old.clone(),
                            new: h.new.clone(),
                            at: h.at,
                            new_at: h.new_at,
                            end: h.end,
                        })
                        .collect(),
                };
                ensure!(
                    render(&u, Some(old))?.unwrap().is_empty(),
                    "Delete hunks must remove the entire file"
                );
            }
            Ok(None)
        }
        Kind::Update => {
            let old = original.context("Updated file does not exist")?;
            ensure!(
                !old.contains("\r\n") || !old.replace("\r\n", "").contains('\n'),
                "Mixed line endings are unsupported; use an explicit full-file edit"
            );
            let lines = old.lines().map(str::to_owned).collect::<Vec<_>>();
            let mut out = vec![];
            let mut cursor = 0;
            for h in &edit.hunks {
                let at = if let Some(at) = h.at {
                    at
                } else {
                    let matches = (cursor..=lines.len().saturating_sub(h.old.len()))
                        .filter(|&n| {
                            lines.get(n..n + h.old.len()) == Some(h.old.as_slice())
                                && (!h.end || n + h.old.len() == lines.len())
                        })
                        .collect::<Vec<_>>();
                    ensure!(
                        matches.len() == 1,
                        "Hunk context must match exactly once; read the file again"
                    );
                    matches[0]
                };
                ensure!(
                    at >= cursor
                        && at <= lines.len()
                        && lines.get(at..at + h.old.len()) == Some(h.old.as_slice()),
                    "Hunk context/position mismatch"
                );
                out.extend_from_slice(&lines[cursor..at]);
                ensure!(
                    h.new_at.is_none_or(|n| n == out.len()),
                    "New hunk line position mismatch"
                );
                out.extend_from_slice(&h.new);
                cursor = at + h.old.len();
            }
            out.extend_from_slice(&lines[cursor..]);
            let delimiter = if old.contains("\r\n") { "\r\n" } else { "\n" };
            let mut result = out.join(delimiter);
            if old.ends_with('\n') && !out.is_empty() {
                result.push_str(delimiter);
            }
            Ok(Some(result))
        }
    }
}
pub fn apply(root: &Path, args: &Value) -> Result<Value> {
    let patch = crate::runner::bounded_text(args, "patch", 128000)?;
    let lines = patch.lines().collect::<Vec<_>>();
    let edits = if patch.starts_with("*** Begin Patch") {
        codex(&lines)?
    } else {
        unified(&lines)?
    };
    ensure!(
        !edits.is_empty() && edits.len() <= 32,
        "Patch must touch 1–32 files"
    );
    let _lock = super::files::edit_lock(root)?;
    let mut names = BTreeSet::new();
    let mut prepared = vec![];
    for e in edits {
        ensure!(names.insert(e.path.clone()), "Duplicate file in patch");
        let p = crate::runner::path(root, &e.path)?;
        let old = if p.exists() {
            Some(super::files::text(&p)?)
        } else {
            None
        };
        if let Some(expected) = args["expected_hashes"].get(&e.path) {
            ensure!(
                old.as_ref()
                    .is_some_and(|s| expected == &crate::bundle::digest(s.as_bytes())),
                "Stale file hash"
            );
        }
        let mode = std::fs::metadata(&p).ok().map(|m| m.permissions());
        let next = render(&e, old.as_deref())?;
        ensure!(
            next.as_ref().is_none_or(|s| s.len() <= 1024 * 1024),
            "Patched file exceeds 1 MiB"
        );
        prepared.push((e.path, old, next, mode));
    }
    let mut done = 0;
    let result = (|| -> Result<()> {
        for (relative, old, next, _) in &prepared {
            let p = crate::runner::path(root, relative)?;
            let current = if p.exists() {
                Some(super::files::text(&p)?)
            } else {
                None
            };
            ensure!(&current == old, "File changed during patch preparation");
            if let Some(text) = next {
                super::files::write(root, relative, text, old.is_none())?;
            } else {
                std::fs::remove_file(p)?;
            }
            done += 1;
        }
        Ok(())
    })();
    if let Err(e) = result {
        let mut restored = true;
        for (relative, old, _, mode) in prepared[..done].iter().rev() {
            let rollback = (|| -> Result<()> {
                let p = crate::runner::path(root, relative)?;
                if let Some(text) = old {
                    super::files::write(root, relative, text, false)?;
                    if let Some(mode) = mode {
                        std::fs::set_permissions(p, mode.clone())?;
                    }
                } else {
                    std::fs::remove_file(p)?;
                }
                Ok(())
            })();
            restored &= rollback.is_ok();
        }
        bail!("Patch failed ({e}); rollback_complete={restored}. Inspect files before retrying");
    }
    Ok(
        json!({"files":prepared.iter().map(|(p,_,s,_)|json!({"path":p,"deleted":s.is_none(),"sha256":s.as_ref().map(|s|crate::bundle::digest(s.as_bytes()))})).collect::<Vec<_>>(),"atomicity":"per-file; all hunks validated before commit"}),
    )
}
