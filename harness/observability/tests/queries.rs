use openworker_observability::{
    query::{Page, Query, Scope},
    sqlite::Sqlite,
    summary::{self, Canonical},
    Step, Store, StoredStep,
};
use serde_json::json;
fn step(id: u64, op: &str, start: u64, duration: u64) -> Step {
    serde_json::from_value(json!({"version":2,"deployment":"test","role":"worker","trace_id":format!("{:032x}",1),"span_id":format!("{id:016x}"),"session_hash":format!("{:064x}",1),"turn_hash":format!("{:064x}",2),"operation":op,"outcome":"ok","started_ms":start,"ended_ms":start+duration/1000,"duration_us":duration})).unwrap()
}
fn query() -> Query {
    Query {
        deployment: "test".into(),
        scopes: vec![Scope {
            session_hash: format!("{:064x}", 1),
            turn_hash: format!("{:064x}", 2),
        }],
        after: 0,
        through: None,
        limit: 1,
    }
}
fn contract(store: &mut dyn Store) {
    let a = step(1, "agent.execute", 1000, 100000);
    let b = step(2, "tool.execute", 1010, 20000);
    store.put(&a).unwrap();
    store.put(&b).unwrap();
    let mut same_session = step(3, "tool.execute", 1010, 10000);
    same_session.turn_hash = Some(format!("{:064x}", 3));
    store.put(&same_session).unwrap();
    let mut foreign = step(4, "tool.execute", 1010, 10000);
    foreign.session_hash = Some(format!("{:064x}", 9));
    store.put(&foreign).unwrap();
    let mut other = step(5, "tool.execute", 1010, 10000);
    other.deployment = "other".into();
    store.put(&other).unwrap();
    let mut q = query();
    let first = store.timeline(&q).unwrap();
    assert_eq!(first.steps.len(), 1);
    assert_eq!(first.steps[0].step, a);
    assert!(first.next.is_some());
    store.put(&step(6, "tool.execute", 1030, 1000)).unwrap();
    q.after = first.next.unwrap();
    q.through = Some(first.through);
    let next = store.timeline(&q).unwrap();
    assert_eq!(next.steps.len(), 1);
    assert_eq!(next.steps[0].step, b);
    assert!(next.next.is_none());
    assert_eq!(next.through, first.through);
    q.after = 0;
    q.through = None;
    q.limit = 4096;
    q.scopes.push(Scope {
        session_hash: format!("{:064x}", 9),
        turn_hash: format!("{:064x}", 2),
    });
    let all = store.timeline(&q).unwrap();
    assert_eq!(all.steps.len(), 4);
    assert!(all
        .steps
        .iter()
        .all(|r| r.step.deployment == "test" && r.step.turn_hash == Some(format!("{:064x}", 2))));
    q.scopes.clear();
    assert!(store.timeline(&q).is_err());
    q = query();
    q.limit = 4097;
    assert!(store.timeline(&q).is_err());
    q = query();
    q.after = u64::MAX;
    assert!(store.timeline(&q).is_err());
    store.prune("test", 2000, 1000).unwrap();
    q = query();
    q.through = Some(all.through);
    assert!(store.timeline(&q).unwrap().steps.is_empty());
    assert_eq!(store.list("other", None, 0, 100).unwrap().len(), 1);
}
#[test]
fn sqlite_scoped_pages_watermark_retention_and_validation() {
    let root = tempfile::tempdir().unwrap();
    let mut store = Sqlite::open(&root.path().join("obs.db")).unwrap();
    contract(&mut store);
}
#[cfg(feature = "postgres")]
#[test]
#[ignore = "disposable OW_OBSERVABILITY_TEST_URL required"]
fn postgres_scoped_pages_watermark_retention_and_validation() {
    use openworker_observability::postgres::{Config, Postgres, Tls};
    let url = std::env::var("OW_OBSERVABILITY_TEST_URL").unwrap();
    let schema = format!(
        "ow_query_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let mut c = Config::new(url.clone(), schema.clone());
    c.tls = Tls::LoopbackPlaintext;
    Postgres::migrate(&c).unwrap();
    let mut store = Postgres::open(&c).unwrap();
    contract(&mut store);
    drop(store);
    postgres::Client::connect(&url, postgres::NoTls)
        .unwrap()
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .unwrap();
}
#[test]
fn corrupted_row_cannot_return_another_sessions_payload() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("obs.db");
    let mut store = Sqlite::open(&path).unwrap();
    store.put(&step(1, "tool.execute", 1000, 1000)).unwrap();
    let mut foreign = step(1, "tool.execute", 1000, 1000);
    foreign.session_hash = Some(format!("{:064x}", 9));
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute(
        "UPDATE ow_observation_steps SET data=?1",
        [serde_json::to_string(&foreign).unwrap()],
    )
    .unwrap();
    assert!(store.timeline(&query()).is_err());
}
fn page(steps: Vec<Step>) -> Page {
    Page {
        steps: steps
            .into_iter()
            .enumerate()
            .map(|(n, step)| StoredStep {
                cursor: n as u64 + 1,
                step,
            })
            .collect(),
        through: 100,
        next: None,
    }
}
fn model(id: u64, start: u64, duration: u64, usage: bool) -> Step {
    let mut s = step(id, "model.attempt", start, duration);
    s.model = Some("fixture".into());
    s.purpose = Some("main".into());
    s.first_output_us = Some(1000.min(duration));
    if usage {
        s.usage = Some(openworker_observability::Usage {
            input: Some(100),
            output: Some(20),
            cache_read: Some(40),
            cache_write: None,
            reasoning: Some(5),
        });
    }
    s
}
fn requested(id: u64, m: &Step) -> Step {
    let mut s = step(id, "model.requested", m.started_ms, 0);
    s.parent_trace_id = Some(m.trace_id.clone());
    s.parent_span_id = Some(m.span_id.clone());
    s
}
#[test]
fn summaries_reconcile_retries_usage_containers_parallel_time_and_waits() {
    let root = step(1, "agent.execute", 1000, 100000);
    let last = step(2, "agent.execute", 2000, 50000);
    let mut a = model(3, 1010, 40000, true);
    a.parent_trace_id = Some(a.trace_id.clone());
    a.parent_span_id = Some(format!("{:016x}", 9));
    let mut b = model(4, 2010, 10000, false);
    b.outcome = "error".into();
    b.purpose = Some("compaction".into());
    let mut x = step(5, "tool.execute", 1060, 20000);
    x.tool = Some("coding.read".into());
    x.output_bytes = Some(12);
    let mut y = step(6, "tool.execute", 2015, 20000);
    y.tool = x.tool.clone();
    let p = page(vec![
        root,
        last.clone(),
        a.clone(),
        b.clone(),
        x,
        y,
        requested(7, &a),
        requested(8, &b),
        step(9, "policy.review", 1005, 50000),
        step(10, "approval.wait", 1090, 910000),
    ]);
    for row in &p.steps {
        row.step.validate().unwrap();
    }
    let summary = summary::build(
        &p,
        Canonical {
            terminal: true,
            expected_segments: Some(2),
            first_started_ms: Some(1000),
            last_span_id: Some(last.span_id),
            pending_waits: Some(0),
        },
    );
    let golden: serde_json::Value =
        serde_json::from_str(include_str!("../../tests/contracts/observability.json")).unwrap();
    let expected = &golden["summary_overlap"];
    assert_eq!(
        serde_json::to_value(&summary.exclusive_observed_us).unwrap(),
        expected["exclusive_observed_us"]
    );
    assert_eq!(
        serde_json::to_value(summary.wall_elapsed_us).unwrap(),
        expected["wall_elapsed_us"]
    );
    assert_eq!(summary.model_accumulated_us.total, Some(50000));
    assert_eq!(summary.tool_accumulated_us.total, Some(40000));
    assert_eq!(summary.active_segments_accumulated_us.total, Some(150000));
    assert_eq!(summary.wall_elapsed_us, Some(1050000));
    assert_eq!(summary.observed_window_us, Some(1050000));
    assert_eq!(summary.exclusive_observed_us.values().sum::<u64>(), 1050000);
    assert_eq!(summary.exclusive_observed_us["model"], 45000);
    assert_eq!(summary.exclusive_observed_us["tool"], 35000);
    assert_eq!(summary.exclusive_observed_us["parallel"], 5000);
    assert_eq!(summary.exclusive_observed_us["policy"], 10000);
    assert_eq!(summary.exclusive_observed_us["approval_wait"], 910000);
    assert_eq!(summary.usage.input.total, Some(100));
    assert_eq!(summary.usage.input.known, 1);
    assert_eq!(summary.usage.input.unknown, 1);
    assert_eq!(summary.usage.cache_read.total, Some(40));
    assert_eq!(summary.usage.reasoning.total, Some(5));
    assert_eq!(summary.models.len(), 2);
    assert_eq!(summary.tools[0].calls, 2);
    assert_eq!(summary.tools[0].active_union_us, Some(40000));
    assert_eq!(summary.tools[0].returned_bytes.unknown, 1);
    assert_eq!(summary.coverage.requests_without_completion, 0);
    assert!(summary.coverage.all_visible_segments_closed);
    assert_eq!(summary.coverage.guarantee, "best_effort");
    let again = summary::build(&p, Canonical::default());
    assert_eq!(again.usage.input.total, summary.usage.input.total);
    assert!(again.wall_elapsed_us.is_none());
}
#[test]
fn union_is_not_accumulation_known_zero_is_not_missing_and_gaps_are_explicit() {
    let mut a = model(1, 1000, 100000, true);
    a.usage.as_mut().unwrap().input = Some(0);
    a.usage.as_mut().unwrap().cache_read = None;
    let b = model(2, 1050, 100000, false);
    let p = page(vec![a, b, step(3, "tool.execute", 1200, 10000)]);
    let s = summary::build(&p, Canonical::default());
    assert_eq!(s.models[0].accumulated_us.total, Some(200000));
    assert_eq!(s.models[0].active_union_us, Some(150000));
    assert_eq!(s.usage.input.total, Some(0));
    assert_eq!(s.usage.input.unknown, 1);
    assert_eq!(s.exclusive_observed_us["unobserved_gap"], 50000);
    assert_eq!(s.coverage.completions_without_start, 2);
}
#[test]
fn truncation_orphan_requests_clock_anomalies_and_overflow_never_fake_completeness() {
    let a = model(1, 1000, 1000, true);
    let mut skew = step(2, "approval.wait", 2000, 0);
    skew.duration_us = None;
    skew.ended_ms = 1999;
    skew.validate().unwrap();
    let mut p = page(vec![requested(3, &a), skew]);
    p.next = Some(2);
    let s = summary::build(
        &p,
        Canonical {
            terminal: true,
            expected_segments: Some(2),
            ..Default::default()
        },
    );
    assert!(s.wall_elapsed_us.is_none());
    assert!(s.coverage.scan_truncated);
    assert!(s.coverage.clock_or_range_anomaly);
    assert_eq!(s.coverage.requests_without_completion, 1);
    assert_eq!(s.coverage.unknown_timing_steps, 1);
    let p = page(
        (1..=3)
            .map(|n| {
                let mut s = model(n, 1000, 1000, true);
                s.usage.as_mut().unwrap().input = Some(i64::MAX as u64);
                s
            })
            .collect(),
    );
    let s = summary::build(&p, Canonical::default());
    assert!(s.usage.input.overflow);
    assert!(s.usage.input.total.is_none());
}
#[test]
fn groups_are_bounded_but_overall_usage_still_counts_the_whole_scanned_page() {
    let p = page(
        (1..=129)
            .map(|n| {
                let mut s = model(n, 1000, 1000, true);
                s.model = Some(format!("model-{n}"));
                s
            })
            .collect(),
    );
    let s = summary::build(&p, Canonical::default());
    assert_eq!(s.models.len(), 128);
    assert!(s.coverage.group_truncated);
    assert_eq!(s.usage.input.total, Some(12900));
}

#[test]
fn projection_enforces_its_own_row_budget_and_preserves_zero_duration() {
    let p = page((1..=4097).map(|n| model(n, 1000, 0, true)).collect());
    let s = summary::build(&p, Canonical::default());
    assert_eq!(s.coverage.scanned_steps, 4096);
    assert!(s.coverage.scan_truncated);
    assert_eq!(s.usage.input.total, Some(409600));
    assert_eq!(s.model_accumulated_us.total, Some(0));
    assert_eq!(s.observed_window_us, Some(0));
}

#[test]
fn unrelated_policy_work_is_parallel_and_missing_or_cyclic_parents_are_explicit() {
    let policy = step(1, "policy.review", 1000, 100000);
    let mut m = model(2, 1000, 100000, true);
    let independent = summary::build(&page(vec![policy.clone(), m.clone()]), Canonical::default());
    assert_eq!(independent.exclusive_observed_us["parallel"], 100000);
    m.parent_trace_id = Some(policy.trace_id.clone());
    m.parent_span_id = Some(policy.span_id.clone());
    let nested = summary::build(&page(vec![policy.clone(), m.clone()]), Canonical::default());
    assert_eq!(nested.exclusive_observed_us["model"], 100000);
    assert!(!nested.exclusive_observed_us.contains_key("parallel"));
    let missing = summary::build(&page(vec![m.clone()]), Canonical::default());
    assert!(missing.coverage.hierarchy_incomplete);
    let mut cycle = policy;
    cycle.parent_trace_id = Some(m.trace_id.clone());
    cycle.parent_span_id = Some(m.span_id.clone());
    let cycle = summary::build(&page(vec![cycle, m]), Canonical::default());
    assert!(cycle.coverage.hierarchy_incomplete);
}
