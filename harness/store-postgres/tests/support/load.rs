//! Opt-in bounded synthetic load qualification; results are not provider SLAs.
use super::concurrency::{check_effects, effects, evidence, remove_owned_schema, sessions, submit};
use super::*;
use std::{collections::BTreeMap, time::Instant};

fn resource(pid: u32) -> (u64, u64) {
    let rss = std::fs::read_to_string(format!("/proc/{pid}/status"))
        .unwrap()
        .lines()
        .find(|l| l.starts_with("VmRSS:"))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap();
    let fields: Vec<_> = stat[stat.rfind(')').unwrap() + 1..]
        .split_whitespace()
        .collect();
    (
        rss,
        fields[11].parse::<u64>().unwrap() + fields[12].parse::<u64>().unwrap(),
    )
}
fn distribution(mut values: Vec<f64>) -> Value {
    values.sort_by(f64::total_cmp);
    let rank = |p: f64| values[((values.len() as f64 * p).ceil() as usize).saturating_sub(1)];
    json!({"count":values.len(),"p50_seconds":rank(0.5),"p95_seconds":rank(0.95),"max_seconds":values.last().unwrap()})
}
async fn sample(
    workers: usize,
    slots: usize,
    count: usize,
    delay_ms: u64,
    repetition: usize,
    hz: f64,
) {
    let (root, c, api) = service_fixture();
    let mut seed = Worker::start(root.path(), &c.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seed, count, true).await;
    seed.kill().await;
    // The direct-protocol fixture, unlike HTTP ingress, creates owned sessions.
    // Let its 3s seed leases expire OUTSIDE the measured workload interval.
    tokio::time::sleep(Duration::from_secs(4)).await;
    let wall = Instant::now();
    for sid in &ids {
        submit(&api, sid, "load");
    }
    let admission_seconds = wall.elapsed().as_secs_f64();
    let mut children: Vec<_> = (0..workers)
        .map(|n| {
            super::draining::pool_command(
                root.path(),
                &c.schema,
                &format!("load{n}"),
                &ids[0],
                slots,
                20,
            )
            .env("OW_FIXTURE_LOAD_PROFILE", "1")
            .spawn()
            .unwrap()
        })
        .collect();
    let mut starts = BTreeMap::new();
    let mut peak_rss = 0;
    let mut peak_active = 0;
    let mut cpu_ticks = 0;
    let mut first_effect = None;
    tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let mut rss = 0;
            let mut ticks = 0;
            for child in &mut children {
                assert!(child.try_wait().unwrap().is_none(), "load worker failed");
                let (r, t) = resource(child.id().unwrap());
                rss += r;
                ticks += t;
            }
            peak_rss = peak_rss.max(rss);
            cpu_ticks = cpu_ticks.max(ticks);
            for n in 0..count {
                if !effects(root.path(), n).is_empty() {
                    first_effect.get_or_insert_with(|| wall.elapsed().as_secs_f64());
                    let started = starts.entry(n).or_insert_with(Instant::now);
                    let gate = root.path().join(format!("work{n}/release"));
                    if started.elapsed() >= Duration::from_millis(delay_ms) && !gate.exists() {
                        std::fs::write(gate, "").unwrap();
                    }
                }
            }
            let state = api.operational_snapshot(30, 30).unwrap();
            peak_active = peak_active.max(state.active);
            if state.queued == 0 && state.active == 0 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    let wall_seconds = wall.elapsed().as_secs_f64();
    for sid in &ids {
        assert_eq!(
            api.submission(sid, "load").unwrap().status,
            SubmissionStatus::Completed
        );
    }
    assert!(peak_active <= (workers * slots) as u64);
    let drain = Instant::now();
    for child in &mut children {
        stop_service(child).await;
    }
    let drain_seconds = drain.elapsed().as_secs_f64();
    check_effects(&api, root.path(), &ids, &vec![1; count]);
    let (queue, end_to_end) = tokio::task::block_in_place(|| {
        let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
        db.batch_execute(&format!(
            "SET ow.runtime_schema='6'; SET search_path={},pg_catalog",
            c.schema
        ))
        .unwrap();
        let rows=db.query("SELECT extract(epoch FROM min(a.created) FILTER (WHERE a.kind='turn_started')-s.created)::float8, extract(epoch FROM max(a.created) FILTER (WHERE a.kind='turn_completed')-s.created)::float8 FROM submissions s JOIN audit a ON a.session_id=s.session_id AND a.turn_id=s.turn_id WHERE s.id='load' GROUP BY s.session_id,s.created",&[]).unwrap();
        assert_eq!(rows.len(), count);
        (
            distribution(rows.iter().map(|r| r.get(0)).collect()),
            distribution(rows.iter().map(|r| r.get(1)).collect()),
        )
    });
    let report = json!({"workload":"one gated native fsync effect per fresh session; scripted model; sandbox off", "workers":workers,"slots_per_worker":slots,"sessions":count,"minimum_tool_wait_ms":delay_ms,"repetition":repetition,"architecture":std::env::consts::ARCH,"visible_parallelism":std::thread::available_parallelism().unwrap().get(),"wall_seconds_including_admission":wall_seconds,"admission_seconds":admission_seconds,"completed_per_second":count as f64/wall_seconds,"first_effect_seconds":first_effect,"queue_wait":queue,"end_to_end":end_to_end,"peak_active_observed":peak_active,"peak_sum_worker_rss_kib":peak_rss,"worker_cpu_seconds":cpu_ticks as f64/hz,"drain_seconds":drain_seconds,"effects":count,"duplicates":0,"failures":0,"observer_interval_ms":100});
    let mut report = report;
    report["build_profile"] = json!(if cfg!(debug_assertions) {
        "development"
    } else {
        "release"
    });
    report["worker_poll_ms"] = json!(250);
    report["lease_seconds"] = json!(30);
    report["database_timeout_seconds"] = json!(5);
    println!("LOAD {}", report);
    evidence(
        &c,
        &format!("load-w{workers}-s{slots}-d{delay_ms}-r{repetition}"),
        report,
    );
    remove_owned_schema(&c);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "explicit OW_RUN_LOAD=1, isolated PostgreSQL and Linux required"]
async fn bounded_load_matrix() {
    if std::env::var("OW_RUN_LOAD").as_deref() != Ok("1") {
        return;
    }
    let output = Command::new("getconf")
        .arg("CLK_TCK")
        .output()
        .await
        .unwrap();
    assert!(output.status.success());
    let hz = String::from_utf8(output.stdout)
        .unwrap()
        .trim()
        .parse::<f64>()
        .unwrap();
    for repetition in 0..2 {
        for (workers, slots) in [(1, 1), (1, 4), (2, 2), (2, 4)] {
            sample(workers, slots, 24, 250, repetition, hz).await;
        }
        sample(2, 2, 16, 1000, repetition, hz).await;
    }
}
