//! Projection v1, rebuilt from bounded immutable raw records. It is not a bill,
//! audit ledger, or proof that absent spans/counters never existed.
use crate::{query::Page, Step};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
pub const VERSION: u8 = 1;
pub const MAX_GROUPS: usize = 128;
#[derive(Clone, Debug, Default, Serialize)]
pub struct Measure {
    pub total: Option<u64>,
    pub known: u64,
    pub unknown: u64,
    pub overflow: bool,
}
impl Measure {
    fn add(&mut self, value: Option<u64>) {
        match value {
            None => self.unknown += 1,
            Some(n) => {
                self.total = if self.known == 0 {
                    Some(n)
                } else {
                    self.total.and_then(|t| t.checked_add(n))
                };
                self.known += 1;
                if self.total.is_none() {
                    self.overflow = true;
                }
            }
        }
    }
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Usage {
    pub input: Measure,
    pub output: Measure,
    pub cache_read: Measure,
    pub cache_write: Measure,
    pub reasoning: Measure,
}
impl Usage {
    fn add(&mut self, u: Option<&crate::Usage>) {
        self.input.add(u.and_then(|u| u.input));
        self.output.add(u.and_then(|u| u.output));
        self.cache_read.add(u.and_then(|u| u.cache_read));
        self.cache_write.add(u.and_then(|u| u.cache_write));
        self.reasoning.add(u.and_then(|u| u.reasoning));
    }
}
#[derive(Debug, Default, Serialize)]
pub struct Group {
    pub name: Option<String>,
    pub purpose: Option<String>,
    pub calls: u64,
    pub outcomes: BTreeMap<String, u64>,
    pub accumulated_us: Measure,
    pub active_union_us: Option<u64>,
    pub first_output_us: Measure,
    pub returned_bytes: Measure,
    pub usage: Option<Usage>,
}
#[derive(Default)]
struct Building {
    group: Group,
    intervals: Vec<(u64, u64)>,
}
impl Building {
    fn add(&mut self, s: &Step, model: bool) {
        self.group.calls += 1;
        *self.group.outcomes.entry(s.outcome.clone()).or_default() += 1;
        self.group.accumulated_us.add(s.duration_us);
        if let Some(interval) = interval(s) {
            self.intervals.push(interval);
        }
        if model {
            self.group.first_output_us.add(s.first_output_us);
            self.group
                .usage
                .get_or_insert_with(Default::default)
                .add(s.usage.as_ref());
        } else {
            self.group.returned_bytes.add(s.output_bytes);
        }
    }
    fn finish(mut self) -> Group {
        self.group.active_union_us = (!self.intervals.is_empty())
            .then(|| union(&mut self.intervals))
            .flatten();
        self.group
    }
}
#[derive(Default)]
pub struct Canonical {
    pub terminal: bool,
    pub expected_segments: Option<u32>,
    pub first_started_ms: Option<u64>,
    pub last_span_id: Option<String>,
    pub pending_waits: Option<u64>,
}
#[derive(Debug, Serialize)]
pub struct Coverage {
    pub guarantee: &'static str,
    pub scanned_steps: usize,
    pub scan_truncated: bool,
    pub group_truncated: bool,
    pub expected_segments: Option<u32>,
    pub recorded_segments: usize,
    pub all_visible_segments_closed: bool,
    pub pending_waits: Option<u64>,
    pub requests_without_completion: usize,
    pub completions_without_start: usize,
    pub unknown_timing_steps: usize,
    pub unknown_outcome_steps: usize,
    pub clock_or_range_anomaly: bool,
    pub hierarchy_incomplete: bool,
}
#[derive(Debug, Serialize)]
pub struct Summary {
    pub projection_version: u8,
    pub through: u64,
    pub coverage: Coverage,
    pub wall_elapsed_us: Option<u64>,
    pub observed_window_us: Option<u64>,
    pub active_segments_accumulated_us: Measure,
    pub model_accumulated_us: Measure,
    pub tool_accumulated_us: Measure,
    pub exclusive_observed_us: BTreeMap<&'static str, u64>,
    pub usage: Usage,
    pub models: Vec<Group>,
    pub tools: Vec<Group>,
}
fn interval(s: &Step) -> Option<(u64, u64)> {
    let start = s.started_ms.checked_mul(1000)?;
    let end = start.checked_add(s.duration_us?)?;
    Some((start, end))
}
fn union(intervals: &mut [(u64, u64)]) -> Option<u64> {
    intervals.sort_unstable();
    let mut end = 0;
    let mut total = 0u64;
    for &(start, stop) in intervals.iter() {
        let added = stop.saturating_sub(start.max(end));
        total = total.checked_add(added)?;
        end = end.max(stop);
    }
    Some(total)
}
// Subtract only proven descendants from a policy container. A different run's
// concurrent model is independent policy/model work, not a nested reviewer.
fn policy_intervals(rows: &[crate::StoredStep]) -> (Vec<(u64, u64)>, bool) {
    let parents: BTreeMap<_, _> = rows
        .iter()
        .map(|r| ((r.step.trace_id.as_str(), r.step.span_id.as_str()), &r.step))
        .collect();
    let mut children: BTreeMap<(&str, &str), Vec<(u64, u64)>> = BTreeMap::new();
    let mut incomplete = false;
    for row in rows {
        let s = &row.step;
        if !matches!(
            s.operation.as_str(),
            "model.attempt" | "tool.execute" | "approval.wait" | "question.wait"
        ) {
            continue;
        }
        let Some(interval) = interval(s) else {
            continue;
        };
        let mut parent = s
            .parent_trace_id
            .as_deref()
            .zip(s.parent_span_id.as_deref());
        let mut seen = BTreeSet::new();
        for _ in 0..64 {
            let Some(key) = parent else {
                break;
            };
            if !seen.insert(key) {
                incomplete = true;
                parent = None;
                break;
            }
            let Some(ancestor) = parents.get(&key) else {
                incomplete = true;
                parent = None;
                break;
            };
            if ancestor.operation == "policy.review" {
                children.entry(key).or_default().push(interval);
            }
            parent = ancestor
                .parent_trace_id
                .as_deref()
                .zip(ancestor.parent_span_id.as_deref());
        }
        if parent.is_some() {
            incomplete = true;
        }
    }
    let mut pieces = vec![];
    for row in rows {
        let s = &row.step;
        if s.operation != "policy.review" {
            continue;
        }
        let Some((start, end)) = interval(s) else {
            continue;
        };
        let mut at = start;
        if let Some(children) = children.get_mut(&(s.trace_id.as_str(), s.span_id.as_str())) {
            children.sort_unstable();
            for &(a, b) in children.iter() {
                let (a, b) = (a.max(start).min(end), b.max(start).min(end));
                if a > at {
                    pieces.push((at, a));
                }
                at = at.max(b);
            }
        }
        if at < end || start == end {
            pieces.push((at, end));
        }
    }
    (pieces, incomplete)
}
// Segment spans are background containers. Proven model children are removed
// from policy intervals above; remaining overlapping phases are parallel work.
fn phase(active: &[i32; 6]) -> &'static str {
    if [active[1], active[2], active[3], active[4], active[5]]
        .into_iter()
        .filter(|n| *n > 0)
        .count()
        > 1
    {
        return "parallel";
    }
    if active[1] > 0 {
        "model"
    } else if active[2] > 0 {
        "tool"
    } else if active[4] > 0 {
        "approval_wait"
    } else if active[5] > 0 {
        "question_wait"
    } else if active[3] > 0 {
        "policy"
    } else if active[0] > 0 {
        "other_active"
    } else {
        "unobserved_gap"
    }
}
pub fn build(page: &Page, canonical: Canonical) -> Summary {
    let mut models: BTreeMap<(Option<String>, Option<String>), Building> = BTreeMap::new();
    let mut tools: BTreeMap<Option<String>, Building> = BTreeMap::new();
    let mut usage = Usage::default();
    let (mut segments_total, mut model_total, mut tool_total) =
        (Measure::default(), Measure::default(), Measure::default());
    let mut changes: BTreeMap<u64, [i32; 6]> = BTreeMap::new();
    let mut roots = vec![];
    let (mut started, mut completed) = (BTreeSet::new(), BTreeSet::new());
    let (mut unknown_timing, mut unknown_outcomes, mut anomaly) = (0, 0, false);
    let scan_truncated =
        page.next.is_some() || page.steps.len() > crate::query::MAX_SUMMARY_STEPS as usize;
    let rows = &page.steps[..page
        .steps
        .len()
        .min(crate::query::MAX_SUMMARY_STEPS as usize)];
    let (policy_pieces, hierarchy_incomplete) = policy_intervals(rows);
    for row in rows {
        let s = &row.step;
        if s.duration_us.is_none() {
            unknown_timing += 1;
        }
        if matches!(s.outcome.as_str(), "unknown" | "interrupted") {
            unknown_outcomes += 1;
        }
        let category = match s.operation.as_str() {
            "agent.execute" => {
                roots.push(s);
                segments_total.add(s.duration_us);
                Some(0)
            }
            "model.attempt" => {
                model_total.add(s.duration_us);
                usage.add(s.usage.as_ref());
                completed.insert((s.trace_id.clone(), s.span_id.clone()));
                models
                    .entry((s.model.clone(), s.purpose.clone()))
                    .or_default()
                    .add(s, true);
                Some(1)
            }
            "model.requested" => {
                if let (Some(trace), Some(span)) = (&s.parent_trace_id, &s.parent_span_id) {
                    started.insert((trace.clone(), span.clone()));
                }
                None
            }
            "tool.execute" => {
                tool_total.add(s.duration_us);
                tools.entry(s.tool.clone()).or_default().add(s, false);
                Some(2)
            }
            "policy.review" => Some(3),
            "approval.wait" => Some(4),
            "question.wait" => Some(5),
            _ => None,
        };
        if let Some(index) = category {
            if let Some((start, end)) = interval(s) {
                changes.entry(start).or_default();
                if end > start && index != 3 {
                    changes.entry(start).or_default()[index] += 1;
                    changes.entry(end).or_default()[index] -= 1;
                }
            } else if s.duration_us.is_some() || s.ended_ms < s.started_ms {
                anomaly = true;
            }
        }
    }
    for (start, end) in policy_pieces {
        changes.entry(start).or_default();
        if end > start {
            changes.entry(start).or_default()[3] += 1;
            changes.entry(end).or_default()[3] -= 1;
        }
    }
    let mut exclusive = BTreeMap::new();
    let mut active = [0i32; 6];
    let mut previous = None;
    let mut window = 0u64;
    for (at, delta) in changes {
        if let Some(prev) = previous {
            let length = at - prev;
            *exclusive.entry(phase(&active)).or_insert(0) += length;
            window += length;
        }
        for n in 0..6 {
            active[n] += delta[n];
        }
        previous = Some(at);
    }
    let last = canonical
        .last_span_id
        .as_ref()
        .and_then(|id| roots.iter().find(|s| &s.span_id == id));
    let closed = canonical
        .expected_segments
        .is_some_and(|n| n as usize == roots.len())
        && last.is_some()
        && canonical.terminal
        && !scan_truncated
        && roots.iter().all(|s| s.duration_us.is_some());
    let elapsed = if closed && !anomaly {
        canonical
            .first_started_ms
            .zip(last.map(|s| s.ended_ms))
            .and_then(|(start, end)| end.checked_sub(start)?.checked_mul(1000))
    } else {
        None
    };
    if closed && elapsed.is_none() {
        anomaly = true;
    }
    let group_truncated = models.len() > MAX_GROUPS || tools.len() > MAX_GROUPS;
    // Overall totals still cover every scanned row. Group overflow is explicit;
    // no claim that the omitted individual groups sum to the overall total.
    let models = models
        .into_iter()
        .take(MAX_GROUPS)
        .map(|((name, purpose), mut b)| {
            b.group.name = name;
            b.group.purpose = purpose;
            b.finish()
        })
        .collect();
    let tools = tools
        .into_iter()
        .take(MAX_GROUPS)
        .map(|(name, mut b)| {
            b.group.name = name;
            b.finish()
        })
        .collect();
    Summary {
        projection_version: VERSION,
        through: page.through,
        coverage: Coverage {
            guarantee: "best_effort",
            scanned_steps: page
                .steps
                .len()
                .min(crate::query::MAX_SUMMARY_STEPS as usize),
            scan_truncated,
            group_truncated,
            expected_segments: canonical.expected_segments,
            recorded_segments: roots.len(),
            all_visible_segments_closed: closed,
            pending_waits: canonical.pending_waits,
            requests_without_completion: started.difference(&completed).count(),
            completions_without_start: completed.difference(&started).count(),
            unknown_timing_steps: unknown_timing,
            unknown_outcome_steps: unknown_outcomes,
            clock_or_range_anomaly: anomaly,
            hierarchy_incomplete,
        },
        wall_elapsed_us: elapsed,
        observed_window_us: previous.map(|_| window),
        active_segments_accumulated_us: segments_total,
        model_accumulated_us: model_total,
        tool_accumulated_us: tool_total,
        exclusive_observed_us: exclusive,
        usage,
        models,
        tools,
    }
}
