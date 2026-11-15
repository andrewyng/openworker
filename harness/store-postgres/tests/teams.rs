use openworker_runtime::{
    provider::Kind,
    store::{Session, StateStore},
    teams::{Member, Role},
};
use openworker_store_postgres::{
    teams::{Command, Create},
    worker::Selector,
    Config, PostgresStore, Tls,
};
use serde_json::{json, Value};

fn fixture() -> (Config, PostgresStore, String) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_team_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    PostgresStore::migrate(&c).unwrap();
    let mut db = PostgresStore::open(c.clone()).unwrap();
    for (id, role, provider) in [
        ("lead", Role::Lead, Kind::Openai),
        ("worker", Role::Worker, Kind::Anthropic),
        ("other", Role::Worker, Kind::Openai),
    ] {
        let mut s:Session=serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":"test","bundle_digest":"bundle","model":"fixture","model_provider":provider,"execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        s.package.team_role = Some(role);
        db.save(&s, "session_created", json!({})).unwrap();
    }
    let create = Create {
        request_id: "create".into(),
        name: "Test team".into(),
        members: vec![
            Member {
                session_id: "lead".into(),
                name: "Lead".into(),
                role: Role::Lead,
            },
            Member {
                session_id: "worker".into(),
                name: "Worker".into(),
                role: Role::Worker,
            },
            Member {
                session_id: "other".into(),
                name: "Other".into(),
                role: Role::Worker,
            },
        ],
    };
    let made = db.create_team(create.clone()).unwrap();
    assert_eq!(db.create_team(create).unwrap()["duplicate"], true);
    (c, db, made["team_id"].as_str().unwrap().into())
}
fn cmd(db: &PostgresStore, team: &str, name: &str, args: Value) -> Value {
    db.team_command(
        team,
        Command {
            request_id: uuid::Uuid::new_v4().to_string(),
            operation: name.into(),
            arguments: args,
        },
    )
    .unwrap()
}
fn item(db: &PostgresStore, team: &str, deps: Vec<&str>) -> String {
    cmd(db,team,"team_create_item",json!({"title":"Inspect","description":"Read relevant evidence","criteria":"Report exact findings","dependencies":deps}))["data"]["item"]["id"].as_str().unwrap().into()
}
fn current(db: &PostgresStore, team: &str, id: &str) -> Value {
    db.team_snapshot(team).unwrap()["board"]["items"][id].clone()
}
fn assign(db: &PostgresStore, team: &str, id: &str, sid: &str) -> Value {
    cmd(
        db,
        team,
        "team_assign",
        json!({"item_id":id,"expected_revision":current(db,team,id)["revision"],"session_id":sid}),
    )
}
fn transition(db: &PostgresStore, team: &str, id: &str, state: &str) -> Value {
    cmd(
        db,
        team,
        "team_transition",
        json!({"item_id":id,"expected_revision":current(db,team,id)["revision"],"status":state}),
    )
}
fn begin(db: &mut PostgresStore, sid: &str) -> String {
    let request = db.next_turn(sid).unwrap().unwrap();
    let (mut s, _, _) = db
        .accept(sid, &request.submission_id, &request.text)
        .unwrap();
    assert_eq!(
        s.history.last().unwrap()["_runtime_notice"],
        "team_delivery"
    );
    let tid = s.turn.as_ref().unwrap().id.clone();
    s.turn.as_mut().unwrap().status = "running".into();
    db.save(&s, "turn_started", json!({})).unwrap();
    tid
}
fn tool(db: &mut PostgresStore, sid: &str, name: &str, args: Value) -> Value {
    let mut s = db.get(sid).unwrap();
    let tid = s.turn.as_ref().unwrap().id.clone();
    let call = json!({"type":"function_call","call_id":uuid::Uuid::new_v4().to_string(),"name":name,"arguments":args.to_string()});
    s.history.push(call.clone());
    s.turn.as_mut().unwrap().queue = vec![call.clone()];
    db.save(&s, "fixture_call", json!({})).unwrap();
    db.team_call(sid, &tid, &call, &args).unwrap().data["result"].clone()
}
fn finish(db: &mut PostgresStore, sid: &str, status: &str) {
    let mut s = db.get(sid).unwrap();
    s.turn.as_mut().unwrap().status = status.into();
    db.save(&s, "turn_completed", json!({"status":status}))
        .unwrap();
}
fn selector() -> Selector {
    Selector {
        coworker: "unused".into(),
        bundle_digest: "unused".into(),
        backend: openworker_runtime::execution::Backend::Off,
        scripted: true,
        fallback_model: "fixture".into(),
        model_provider: Kind::Openai,
        model_connections: Default::default(),
        environment_provider: None,
        environment_identity: None,
        environment_profile: None,
    }
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn delayed_completion_cannot_reopen_accepted_work() {
    let (c, mut db, team) = fixture();
    let id = item(&db, &team, vec![]);
    assign(&db, &team, &id, "worker");
    begin(&mut db, "worker");
    transition(&db, &team, &id, "in_progress");
    transition(&db, &team, &id, "review");
    finish(&mut db, "worker", "completed");
    transition(&db, &team, &id, "done");
    let scheduler = PostgresStore::open(c).unwrap();
    assert!(scheduler.claim_next(selector()).unwrap().is_none());
    assert_eq!(current(&db, &team, &id)["status"], "done");
    assert!(db.next_turn("lead").unwrap().is_none());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn dependencies_roles_full_detail_and_durable_lead_notification() {
    let (c, mut db, team) = fixture();
    let a = item(&db, &team, vec![]);
    let b = item(&db, &team, vec![&a]);
    assign(&db, &team, &a, "worker");
    assign(&db, &team, &b, "other");
    assert!(db.next_turn("other").unwrap().is_none());
    begin(&mut db, "worker");
    let context = db.team_context("worker").unwrap().unwrap();
    assert_eq!(context.active_item.as_deref(), Some(a.as_str()));
    assert_eq!(
        context.assignment,
        Some(current(&db, &team, &a)["assignment"].as_i64().unwrap())
    );
    let restarted = PostgresStore::open(c.clone()).unwrap();
    assert_eq!(
        restarted
            .team_context("worker")
            .unwrap()
            .unwrap()
            .active_item,
        Some(a.clone())
    );
    let detail = tool(&mut db, "worker", "team_get_item", json!({"item_id":a}));
    assert_eq!(detail["item"]["criteria"], "Report exact findings");
    let denied = tool(
        &mut db,
        "worker",
        "team_assign",
        json!({"item_id":b,"expected_revision":2,"session_id":"worker"}),
    );
    assert_eq!(denied["isError"], true);
    let args = json!({"item_id":a,"expected_revision":current(&db,&team,&a)["revision"],"status":"in_progress"});
    assert!(tool(&mut db, "worker", "team_transition", args)
        .get("isError")
        .is_none());
    let args = json!({"item_id":a,"expected_revision":current(&db,&team,&a)["revision"],"status":"review"});
    assert!(tool(&mut db, "worker", "team_transition", args)
        .get("isError")
        .is_none());
    let args =
        json!({"item_id":a,"expected_revision":current(&db,&team,&a)["revision"],"status":"done"});
    assert_eq!(
        tool(&mut db, "worker", "team_transition", args)["isError"],
        true
    );
    assert!(db.next_turn("lead").unwrap().is_none());
    finish(&mut db, "worker", "completed");
    // A replacement scheduler drains the durable completion outbox.
    let scheduler = PostgresStore::open(c.clone()).unwrap();
    assert!(scheduler.claim_next(selector()).unwrap().is_none());
    let lead = db.next_turn("lead").unwrap().unwrap();
    assert!(lead.text.contains(&a));
    assert!(scheduler.claim_next(selector()).unwrap().is_none());
    assert_eq!(
        db.next_turn("lead").unwrap().unwrap().submission_id,
        lead.submission_id
    );
    transition(&db, &team, &a, "done");
    assert!(db.next_turn("other").unwrap().is_some());
    let reopened = PostgresStore::open(c).unwrap();
    assert_eq!(current(&reopened, &team, &a)["status"], "done");
    assert_eq!(
        reopened.team_snapshot(&team).unwrap()["board"]["members"]["worker"]["session_id"],
        "worker"
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn duplicate_commands_messages_and_filtered_cursor_do_not_repeat_wakes_or_stall() {
    let (_, db, team) = fixture();
    let i = item(&db, &team, vec![]);
    for _ in 0..55 {
        cmd(
            &db,
            &team,
            "team_post_message",
            json!({"body":"Unrelated comment","item_id":"","recipient":""}),
        );
    }
    let message = Command {
        request_id: "message".into(),
        operation: "team_post_message".into(),
        arguments: json!({"body":"Inspect this finding","item_id":i,"recipient":"worker"}),
    };
    let result = db.team_command(&team, message.clone()).unwrap();
    assert_eq!(db.team_command(&team, message.clone()).unwrap(), result);
    let mut changed = message;
    changed.arguments["body"] = json!("Changed");
    assert!(db.team_command(&team, changed).is_err());
    let first = cmd(
        &db,
        &team,
        "team_read_messages",
        json!({"item_id":i,"after":0,"limit":50}),
    );
    assert_eq!(first["events"], json!([]));
    assert_eq!(first["has_more"], true);
    assert_eq!(first["next_after"], 50);
    let second = cmd(
        &db,
        &team,
        "team_read_messages",
        json!({"item_id":i,"after":first["next_after"],"limit":50}),
    );
    assert_eq!(second["events"].as_array().unwrap().len(), 1);
    let first = db.next_turn("worker").unwrap().unwrap();
    assert_eq!(
        db.next_turn("worker").unwrap().unwrap().submission_id,
        first.submission_id
    );
    let events = db.team_events(&team, 0).unwrap();
    assert_eq!(events["next_after"], 50);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn assignment_and_cancel_boundaries_do_not_overlap_or_approve() {
    let (_, mut db, team) = fixture();
    let i = item(&db, &team, vec![]);
    assign(&db, &team, &i, "worker");
    assert!(db
        .team_command(
            &team,
            Command {
                request_id: "reassign".into(),
                operation: "team_assign".into(),
                arguments: json!({"item_id":i,"expected_revision":2,"session_id":"other"})
            }
        )
        .is_err());
    begin(&mut db, "worker");
    let before = current(&db, &team, &i);
    assert!(db
        .team_command(
            &team,
            Command {
                request_id: "stale".into(),
                operation: "team_transition".into(),
                arguments: json!({"item_id":i,"expected_revision":0,"status":"in_progress"})
            }
        )
        .is_err());
    assert_eq!(current(&db, &team, &i), before);
    cmd(&db, &team, "team_cancel", json!({}));
    assert!(db.next_control("worker").unwrap().is_some());
    assert!(db.get("worker").unwrap().approval_grants.is_empty());
    let s = db.get("worker").unwrap();
    assert!(db.save(&s, "tool_started", json!({})).is_err());
    assert_eq!(db.team_snapshot(&team).unwrap()["board"]["cancelled"], true);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_assignment_has_one_winner_and_failed_commit_enqueues_nothing() {
    use std::sync::{Arc, Barrier};
    let (c, db, team) = fixture();
    let id = item(&db, &team, vec![]);
    let barrier = Arc::new(Barrier::new(3));
    let mut jobs = vec![];
    for sid in ["worker", "other"] {
        let store = PostgresStore::open(c.clone()).unwrap();
        let gate = barrier.clone();
        let team = team.clone();
        let id = id.clone();
        jobs.push(std::thread::spawn(move || {
            gate.wait();
            store
                .team_command(
                    &team,
                    Command {
                        request_id: format!("assign-{sid}"),
                        operation: "team_assign".into(),
                        arguments: json!({"item_id":id,"expected_revision":1,"session_id":sid}),
                    },
                )
                .is_ok()
        }));
    }
    barrier.wait();
    assert_eq!(
        jobs.into_iter()
            .filter(|j| j.thread().id() != std::thread::current().id())
            .map(|j| usize::from(j.join().unwrap()))
            .sum::<usize>(),
        1
    );
    assert_eq!(
        ["worker", "other"]
            .into_iter()
            .filter(|sid| db.next_turn(sid).unwrap().is_some())
            .count(),
        1
    );
    let fresh = item(&db, &team, vec![]);
    let before = db.team_snapshot(&team).unwrap();
    let mut sql = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    sql.batch_execute(&format!(
        "SET ow.runtime_schema='7'; SET search_path={},pg_catalog",
        c.schema
    ))
    .unwrap();
    sql.batch_execute("CREATE FUNCTION fail_team_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$; CREATE TRIGGER fail_team_event BEFORE INSERT ON team_events FOR EACH ROW EXECUTE FUNCTION fail_team_event();").unwrap();
    let failing = PostgresStore::open(c.clone()).unwrap();
    assert!(failing
        .team_command(
            &team,
            Command {
                request_id: "will-fail".into(),
                operation: "team_assign".into(),
                arguments: json!({"item_id":fresh,"expected_revision":1,"session_id":"worker"})
            }
        )
        .is_err());
    assert_eq!(db.team_snapshot(&team).unwrap(), before);
    let count: i64 = sql
        .query_one("SELECT count(*) FROM team_deliveries", &[])
        .unwrap()
        .get(0);
    assert_eq!(count, 1);
    sql.batch_execute("DROP TRIGGER fail_team_event ON team_events")
        .unwrap();
    let retry = PostgresStore::open(c).unwrap();
    assert!(retry
        .team_command(
            &team,
            Command {
                request_id: "will-fail".into(),
                operation: "team_assign".into(),
                arguments: json!({"item_id":fresh,"expected_revision":1,"session_id":"worker"})
            }
        )
        .is_ok());
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn review_changes_start_a_new_assignment_without_replaying_the_old_one() {
    let (_, mut db, team) = fixture();
    let id = item(&db, &team, vec![]);
    assign(&db, &team, &id, "worker");
    begin(&mut db, "worker");
    transition(&db, &team, &id, "in_progress");
    transition(&db, &team, &id, "review");
    finish(&mut db, "worker", "completed");
    let old = current(&db, &team, &id);
    transition(&db, &team, &id, "in_progress");
    let new = current(&db, &team, &id);
    assert_ne!(new["submission_id"], old["submission_id"]);
    assert_eq!(
        new["assignment"].as_u64().unwrap(),
        old["assignment"].as_u64().unwrap() + 1
    );
    assert_eq!(
        db.next_turn("worker").unwrap().unwrap().submission_id,
        new["submission_id"].as_str().unwrap()
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn adding_worker_keeps_existing_team_and_requires_fresh_pinned_role() {
    let (_, mut db, team) = fixture();
    let mut worker = db.get("worker").unwrap();
    worker.id = "added".into();
    worker.name = "Added".into();
    worker.storage_revision = 0;
    db.save(&worker, "session_created", json!({})).unwrap();
    let before = db.team_snapshot(&team).unwrap();
    let c = Command {
        request_id: "add".into(),
        operation: "team_add_member".into(),
        arguments: json!({"session_id":"added","name":"Added","role":"worker"}),
    };
    let added = db.team_command(&team, c.clone()).unwrap();
    assert_eq!(db.team_command(&team, c).unwrap(), added);
    let after = db.team_snapshot(&team).unwrap();
    assert_eq!(after["board"]["lead"], before["board"]["lead"]);
    assert_eq!(after["board"]["items"], before["board"]["items"]);
    assert_eq!(after["board"]["members"].as_object().unwrap().len(), 4);
    assert!(db
        .team_command(
            &team,
            Command {
                request_id: "wrong".into(),
                operation: "team_add_member".into(),
                arguments: json!({"session_id":"added","name":"Another lead","role":"lead"})
            }
        )
        .is_err());
}
