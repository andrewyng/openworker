use super::Match;
use super::{Detector, Result, MAX_MATCHES, MAX_TEXT};
use anyhow::ensure;
use regex::{Regex, RegexBuilder};
pub(super) fn regex(pattern: &str, case_insensitive: bool) -> Result<Regex> {
    ensure!(
        !pattern.is_empty() && pattern.len() <= 4096,
        "Invalid safety regex length"
    );
    Ok(RegexBuilder::new(pattern)
        .case_insensitive(case_insensitive)
        .size_limit(1024 * 1024)
        .dfa_size_limit(1024 * 1024)
        .build()?)
}
fn add(out: &mut Vec<Match>, text: &str, start: usize, end: usize) -> Result<()> {
    ensure!(
        start < end && text.is_char_boundary(start) && text.is_char_boundary(end),
        "Invalid safety match boundaries"
    );
    ensure!(out.len() < MAX_MATCHES, "Safety match limit exceeded");
    out.push(Match {
        start,
        end,
        text: text[start..end].into(),
    });
    Ok(())
}
fn normalize(mut out: Vec<Match>, text: &str) -> Vec<Match> {
    out.sort_by_key(|m| (m.start, m.end));
    let mut merged: Vec<Match> = vec![];
    for m in out {
        if let Some(last) = merged.last_mut() {
            if m.start < last.end {
                last.end = last.end.max(m.end);
                last.text = text[last.start..last.end].into();
                continue;
            }
        }
        merged.push(m);
    }
    merged
}
pub fn fragments(text: &str, findings: &[String]) -> Result<Vec<Match>> {
    ensure!(
        text.len() <= MAX_TEXT && findings.len() <= MAX_MATCHES,
        "Model findings exceed safety limits"
    );
    let mut out = vec![];
    for snippet in findings {
        ensure!(
            !snippet.is_empty() && snippet.len() <= MAX_TEXT,
            "Invalid model finding"
        );
        let matches: Vec<_> = text.match_indices(snippet).collect();
        ensure!(
            !matches.is_empty(),
            "Model finding does not occur in original text"
        );
        for (start, value) in matches {
            add(&mut out, text, start, start + value.len())?;
        }
    }
    Ok(normalize(out, text))
}
pub(super) fn detect(detector: &Detector, text: &str) -> Result<Vec<Match>> {
    ensure!(text.len() <= MAX_TEXT, "Text exceeds safety limit");
    let mut out = vec![];
    let patterns:Vec<(Regex,bool)>=match detector{
        Detector::Regex{pattern,case_insensitive}=>vec![(regex(pattern,*case_insensitive)?,false)],
        Detector::CreditCard=>vec![(regex(r"\b(?:[0-9][ -]?){12,18}[0-9]\b",false)?,true)],
        Detector::Secrets=>[
            r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}",
            r"\bgh[pousr]_[A-Za-z0-9]{30,}",
            r"\bgithub_pat_[A-Za-z0-9_]{20,}",
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
            r"(?s)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        ].into_iter().map(|p|Ok((regex(p,false)?,false))).collect::<Result<_>>()?,
        Detector::Model{..}=>anyhow::bail!("Model detector requires a model executor"),
    };
    for (re, card) in patterns {
        for m in re.find_iter(text) {
            if m.is_empty() {
                anyhow::bail!("Zero-length safety regex match is unsupported");
            }
            if card && !luhn(m.as_str()) {
                continue;
            }
            add(&mut out, text, m.start(), m.end())?;
        }
    }
    Ok(normalize(out, text))
}
fn luhn(text: &str) -> bool {
    let digits: Vec<u32> = text.chars().filter_map(|c| c.to_digit(10)).collect();
    if !(13..=19).contains(&digits.len()) || digits.iter().all(|d| *d == digits[0]) {
        return false;
    }
    digits
        .iter()
        .rev()
        .enumerate()
        .map(|(i, d)| {
            if i % 2 == 1 {
                let n = d * 2;
                if n > 9 {
                    n - 9
                } else {
                    n
                }
            } else {
                *d
            }
        })
        .sum::<u32>()
        % 10
        == 0
}
pub(super) fn redact(
    text: &str,
    matches: &[Match],
    replacement: &str,
    keep_last: usize,
    card: bool,
) -> String {
    let mut out = String::new();
    let mut cursor = 0;
    for m in matches {
        out.push_str(&text[cursor..m.start]);
        out.push_str(replacement);
        if keep_last > 0 {
            let chars: Vec<char> = m
                .text
                .chars()
                .filter(|c| !card || c.is_ascii_digit())
                .collect();
            let n = keep_last.min(chars.len().saturating_sub(1));
            if n > 0 {
                out.push(' ');
                out.extend(&chars[chars.len() - n..]);
            }
        }
        cursor = m.end;
    }
    out.push_str(&text[cursor..]);
    out
}
