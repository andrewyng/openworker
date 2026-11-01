//! One contract exercised against real SQLite and PostgreSQL adapters.
use openworker_runtime::{
    agent_team::Membership,
    store::{Session, StateStore},
};
use serde_json::{json, Value};
pub fn session(id: &str, kind: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":kind,"bundle_digest":"bundle","model":"fixture","execution_backend":"off","scripted":true,"schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
pub fn create(db: &mut dyn StateStore) -> Value {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let args = json!({"agent_id":"lead","agent_types":["builder","verifier"],"policy":{"communication":{"board":true,"directMessages":true},"max_instances":32}});
    let doc = db.agent_team("lead", "create", &args, None).unwrap();
    assert_eq!(
        db.agent_team("lead", "create", &args, None).unwrap()["id"],
        doc["id"]
    );
    doc
}
pub fn exercise_workspace_defaults(db: &mut dyn StateStore) {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let defaults = json!({"repository":"source","base":"a".repeat(40),"agent_types":["builder"]});
    let args = json!({"agent_id":"lead","agent_types":["builder","verifier"],"workspace_selection":true,"workspace_defaults":defaults});
    let doc = db.agent_team("lead", "create", &args, None).unwrap();
    let mut responses = vec![];
    for id in ["one", "two"] {
        let request = json!({"agent_type":"builder","agent_id":id,"message":"Build"});
        let result = db.agent_team("lead", "team_spawn", &request, None).unwrap();
        assert_eq!(result["workspace"]["mode"], "worktree");
        assert_eq!(result["workspace"]["base"], "a".repeat(40));
        assert!(result["workspace"]["path"]
            .as_str()
            .unwrap()
            .contains(doc["id"].as_str().unwrap()));
        assert_eq!(
            result["instance"],
            db.agent_team("lead", "team_spawn", &request, None).unwrap()["instance"]
        );
        responses.push(result);
    }
    assert_ne!(
        responses[0]["workspace"]["path"],
        responses[1]["workspace"]["path"]
    );
    assert_ne!(
        responses[0]["workspace"]["branch"],
        responses[1]["workspace"]["branch"]
    );
    let explicit = json!({"agent_type":"verifier","agent_id":"verify","message":"Inspect exact submission","workspace":{"mode":"directory","path":responses[0]["workspace"]["path"]}});
    assert_eq!(
        db.agent_team("lead", "team_spawn", &explicit, None)
            .unwrap()["workspace"],
        explicit["workspace"]
    );
    let mut changed = args.clone();
    changed["workspace_defaults"]["base"] = json!("b".repeat(40));
    assert!(db.agent_team("lead", "create", &changed, None).is_err());
    let mut invalid = args;
    invalid["workspace_defaults"]["base"] = json!("HEAD");
    assert!(db.agent_team("lead", "create", &invalid, None).is_err());
    let snapshot = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(snapshot["workspace_defaults"], defaults);
    assert_eq!(
        snapshot["roster"]["requests"]["one"]["workspace"],
        responses[0]["workspace"]
    );
}
/// Pure policy/reservation contract. No provider or sandbox is created here.
pub fn exercise_sandbox_selection(db: &mut dyn StateStore) {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let args = json!({"agent_id":"lead","agent_types":["builder","verifier"],"placement_selection":true,
        "delegation":{"tools":["coding.read_file"],"placements":["team","member","new"],"profiles":{"isolated":{"files":[],"network":[],"credentials":[],"cpu":2,"memory_mb":1024}}}});
    db.agent_team("lead", "create", &args, None).unwrap();
    let request = json!({"agent_type":"builder","agent_id":"isolated-one","message":"Work independently","sandbox":{"mode":"new","profile":"isolated","cpu":1,"memory_mb":512}});
    let first = db.agent_team("lead", "team_spawn", &request, None).unwrap();
    assert_eq!(first["sandbox"]["mode"], "new");
    assert!(db
        .get(first["instance"]["session_id"].as_str().unwrap())
        .is_err());
    let same = db.agent_team("lead", "team_spawn", &request, None).unwrap();
    assert_eq!(same["instance"], first["instance"]);
    assert_eq!(same["sandbox"], first["sandbox"]);
    for choice in [
        json!({"mode":"new","profile":"unknown"}),
        json!({"mode":"new","profile":"isolated","cpu":3}),
        json!({"mode":"new","profile":"isolated","credentials":["github"]}),
        json!({"mode":"member","agent_id":"missing"}),
    ] {
        assert!(db.agent_team("lead","team_spawn",&json!({"agent_type":"builder","agent_id":"denied","message":"No authority enlargement","sandbox":choice}),None).is_err());
    }
    let member=db.agent_team("lead","team_spawn",&json!({"agent_type":"verifier","agent_id":"verifier-one","message":"Inspect the builder environment","sandbox":{"mode":"member","agent_id":"isolated-one"}}),None).unwrap();
    assert_eq!(member["sandbox"]["agent_id"], "isolated-one");
    let mut changed = request;
    changed["sandbox"]["cpu"] = json!(2);
    assert!(db.agent_team("lead", "team_spawn", &changed, None).is_err());
    let doc = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(doc["sandbox_choices"]["isolated-one"], first["sandbox"]);
    assert_eq!(doc["roster"]["instances"].as_object().unwrap().len(), 3);
}
pub fn child(db: &mut dyn StateStore, id: &str) -> String {
    let reserved = db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":id,"message":"Build one part"}),
            None,
        )
        .unwrap();
    let sid = reserved["instance"]["session_id"].as_str().unwrap();
    let mut s = session(sid, "builder");
    s.agent_team = Some(Membership {
        team_id: db.get("lead").unwrap().agent_team.unwrap().team_id,
        agent_id: id.into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    db.save(&s, "child", json!({})).unwrap();
    db.agent_team("lead", "ready", &json!({"agent_id":id}), None)
        .unwrap();
    sid.into()
}
pub fn exercise(db: &mut dyn StateStore) {
    let doc = create(db);
    let a = child(db, "builder-ui");
    let b = child(db, "builder-api");
    assert!(db
        .agent_team(
            &a,
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"illegal","message":"bad"}),
            None
        )
        .is_err());
    let reservation = db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-ui","message":"Build one part"}),
            None,
        )
        .unwrap();
    assert_eq!(reservation["instance"]["session_id"], a);
    assert_eq!(reservation["created"], false);
    assert!(db
        .agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-ui","message":"Different"}),
            None
        )
        .is_err());
    let passive = db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"Progress for everyone to read"}),
            None,
        )
        .unwrap();
    assert_eq!(passive["recipients"], json!([]));
    let command = json!({"request_id":"post-1","operation":"team_post_board","arguments":{"message":"@builder-ui @builder-ui check this"}});
    let first = db.agent_team("lead", "command", &command, None).unwrap();
    assert_eq!(
        first,
        db.agent_team("lead", "command", &command, None).unwrap()
    );
    assert!(db.agent_team("lead","command",&json!({"request_id":"post-1","operation":"team_post_board","arguments":{"message":"changed"}}),None).is_err());
    assert_eq!(first["recipients"], json!(["builder-ui"]));
    assert_eq!(
        db.agent_team(&b, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    let inbox = db.agent_team(&a, "deliver", &json!({}), None).unwrap();
    assert_eq!(inbox["messages"].as_array().unwrap().len(), 1);
    let history = db.get(&a).unwrap().history;
    assert!(history.last().unwrap()["content"]
        .as_str()
        .unwrap()
        .contains("not human authorization"));
    assert_eq!(
        db.agent_team(&a, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(db.get(&a).unwrap().history, history);
    db.agent_team(
        "lead",
        "team_send_message",
        &json!({"agent_id":"builder-api","message":"Private request"}),
        None,
    )
    .unwrap();
    let page = db
        .agent_team(
            &b,
            "team_read_board",
            &json!({"message_id":null,"limit":50}),
            None,
        )
        .unwrap();
    assert_eq!(page["messages"].as_array().unwrap().len(), 2);
    let page = db
        .agent_team(
            &b,
            "team_read_board",
            &json!({"message_id":first["id"],"direction":"before","limit":1}),
            None,
        )
        .unwrap();
    assert_eq!(page["messages"][0]["id"], passive["id"]);
    assert!(db
        .agent_team(
            &a,
            "team_read_board",
            &json!({"message_id":"foreign","limit":20}),
            None
        )
        .is_err());
    // Post-time @all excludes future children, including its sender.
    let all = db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"@all verify"}),
            None,
        )
        .unwrap();
    assert_eq!(all["recipients"].as_array().unwrap().len(), 2);
    let c = child(db, "builder-later");
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(
        db.agent_team(&b, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    // Bound batches retain overflow. Canonical cursor advances only with input.
    for n in 0..25 {
        db.agent_team(
            "lead",
            "team_post_board",
            &json!({"message":format!("@builder-later message {n}")}),
            None,
        )
        .unwrap();
    }
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        20
    );
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"]
            .as_array()
            .unwrap()
            .len(),
        5
    );
    assert_eq!(
        db.agent_team(&c, "deliver", &json!({}), None).unwrap()["messages"],
        json!([])
    );
    assert_eq!(
        db.agent_team("lead", "snapshot", &json!({}), None).unwrap()["id"],
        doc["id"]
    );
}

pub fn exercise_briefing(db: &mut dyn StateStore) {
    create(db);
    let args = json!({"agent_type":"builder","agent_id":"briefed-builder","message":"Build the report.","title":"Report builder","background":"Private project facts for the assigned task.","context":{"mode":"fresh"}});
    let first = db.agent_team("lead", "team_spawn", &args, None).unwrap();
    assert_eq!(
        db.agent_team("lead", "team_spawn", &args, None).unwrap(),
        json!({"instance":first["instance"],"created":false})
    );
    let doc = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    let parsed: openworker_runtime::agent_team::Document =
        serde_json::from_value(doc.clone()).unwrap();
    assert_eq!(
        parsed
            .roster
            .request("briefed-builder")
            .unwrap()
            .title
            .as_deref(),
        Some("Report builder")
    );
    assert!(parsed.tasks["briefed-builder"].contains(openworker_runtime::team_briefing::PREFIX));
    assert!(parsed.tasks["briefed-builder"].contains("Private project facts"));
    let public = openworker_runtime::agent_team::public_snapshot(doc);
    assert!(!public.to_string().contains("Private project facts"));
    for (field, value) in [
        ("title", json!("Changed")),
        ("background", json!("Changed facts")),
        ("context", json!({"mode":"snapshot"})),
        ("cwd", json!("/host/escape")),
        ("initiation", json!("user_asked")),
    ] {
        let mut changed = args.clone();
        changed[field] = value;
        assert!(
            db.agent_team("lead", "team_spawn", &changed, None).is_err(),
            "{field}"
        );
    }
}

pub fn exercise_workspace_intent(db: &mut dyn StateStore) {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let config = json!({"agent_id":"lead","agent_types":["builder"],"workspace_selection":true});
    db.agent_team("lead", "create", &config, None).unwrap();
    assert!(db
        .agent_team(
            "lead",
            "create",
            &json!({"agent_id":"lead","agent_types":["builder"]}),
            None
        )
        .is_err());
    let spawn = json!({"agent_id":"builder-one","agent_type":"builder","message":"Build","workspace":{"mode":"directory","path":"ui"}});
    let reservation = db.agent_team("lead", "team_spawn", &spawn, None).unwrap();
    let args = json!({"agent_id":"builder-one"});
    let first = db
        .agent_team("lead", "workspace_dispatch", &args, None)
        .unwrap();
    assert_eq!(first["dispatch"], true);
    let second = db
        .agent_team("lead", "workspace_dispatch", &args, None)
        .unwrap();
    assert_eq!(second["dispatch"], false);
    assert_eq!(first["intent"], second["intent"]);
    // Even a persisted child session cannot become ready before preparation is acknowledged.
    let sid = reservation["instance"]["session_id"].as_str().unwrap();
    let mut child = session(sid, "builder");
    child.agent_team = Some(Membership {
        team_id: db.get("lead").unwrap().agent_team.unwrap().team_id,
        agent_id: "builder-one".into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    db.save(&child, "created", json!({})).unwrap();
    assert!(db.agent_team("lead", "ready", &args, None).is_err());
    assert!(db
        .agent_team(sid, "workspace_dispatch", &args, None)
        .is_err());
    let mut wrong = first["intent"]["request"].clone();
    wrong["operation_id"] = json!("another");
    assert!(db
        .agent_team(
            "lead",
            "workspace_result",
            &json!({"agent_id":"builder-one","request":wrong,"error":"outcome unknown"}),
            None
        )
        .is_err());
    db.agent_team("lead","workspace_result",&json!({"agent_id":"builder-one","request":first["intent"]["request"],"error":"outcome unknown; inspect before retry"}),None).unwrap();
    let snapshot = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(
        snapshot["roster"]["instances"]["builder-one"]["phase"],
        "closed"
    );
    assert!(snapshot["workspaces"]["builder-one"]["receipt"].is_null());
    assert_eq!(
        db.agent_team("lead", "team_spawn", &spawn, None).unwrap()["created"],
        false
    );
    assert!(db
        .agent_team("lead", "workspace_dispatch", &args, None)
        .is_err());
    assert!(openworker_runtime::agent_team::public_snapshot(snapshot)
        .get("workspaces")
        .is_none());
}

pub fn exercise_retirement(db: &mut dyn StateStore) {
    create(db);
    let sid = child(db, "builder-one");
    db.accept(&sid, "original-task", "Original work").unwrap();
    db.agent_team(
        "lead",
        "team_post_board",
        &json!({"message":"@builder-one old inbox"}),
        None,
    )
    .unwrap();
    let retire = json!({"request_id":"retire-one","operation":"team_manage","arguments":{"action":"retire","agent_id":"builder-one","mode":"finish_current"}});
    let first = db.agent_team("lead", "command", &retire, None).unwrap();
    assert_eq!(first["instance"]["phase"], "retiring");
    assert_eq!(
        first,
        db.agent_team("lead", "command", &retire, None).unwrap()
    );
    assert!(db
        .agent_team(
            "lead",
            "team_send_message",
            &json!({"agent_id":"builder-one","message":"new task"}),
            None
        )
        .is_err());
    assert!(db
        .agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"@builder-one new task"}),
            None
        )
        .is_err());
    assert_eq!(
        db.agent_team(
            "lead",
            "team_post_board",
            &json!({"message":"@all later task"}),
            None
        )
        .unwrap()["recipients"],
        json!([])
    );
    assert_eq!(
        db.agent_team(&sid, "wake", &json!({}), None).unwrap()["wake"],
        false
    );
    assert!(db
        .agent_team(
            &sid,
            "team_manage",
            &json!({"action":"retire","agent_id":"lead"}),
            None
        )
        .is_err());
    assert!(db.accept(&sid, "late", "Must not run").is_err());
    assert!(db
        .agent_team(
            "lead",
            "retirement_settle",
            &json!({"agent_id":"builder-one"}),
            None
        )
        .is_err());
    assert!(db
        .agent_team(
            "lead",
            "retirement_error",
            &json!({"agent_id":"builder-one","reason":"arbitrary"}),
            None
        )
        .is_err());
    db.agent_team(
        "lead",
        "retirement_error",
        &json!({"agent_id":"builder-one","reason":"cleanup_not_confirmed"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team("lead", "snapshot", &json!({}), None).unwrap()["retirements"]["builder-one"]
            ["pending_reason"],
        "cleanup_not_confirmed"
    );
    let mut s = db.get(&sid).unwrap();
    s.turn.as_mut().unwrap().status = "interrupted".into();
    s.closed = true;
    db.save(&s, "closed", json!({})).unwrap();
    db.agent_team(
        &sid,
        "retirement_settle",
        &json!({"agent_id":"builder-one"}),
        None,
    )
    .unwrap();
    let list = db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"list","limit":1}),
            None,
        )
        .unwrap();
    assert_eq!(list["members"].as_array().unwrap().len(), 1);
    let next = list["next"].as_str().unwrap();
    let rest = db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"list","limit":1,"after":next}),
            None,
        )
        .unwrap();
    assert_ne!(
        list["members"][0]["instance"]["agent_id"],
        rest["members"][0]["instance"]["agent_id"]
    );
    let snapshot = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(
        snapshot["roster"]["instances"]["builder-one"]["phase"],
        "closed"
    );
    assert!(snapshot["retirements"]["builder-one"]["settled_at_ms"].is_number());
    assert!(snapshot["retirements"]["builder-one"]["pending_reason"].is_null());
    db.agent_team(
        "lead",
        "team_wait_agent",
        &json!({"agent_ids":["builder-one"],"timeout_ms":5000}),
        None,
    )
    .unwrap();
    db.agent_team("lead", "wake", &json!({}), None).unwrap();
    assert_eq!(
        db.get("lead")
            .unwrap()
            .agent_team
            .unwrap()
            .notices
            .last()
            .unwrap()["statuses"]["builder-one"]["outcome_unknown"],
        true
    );
    let roster = db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"list","limit":50}),
            None,
        )
        .unwrap();
    assert_eq!(roster["members"][0]["outcome_unknown"], true);

    assert_eq!(
        first,
        db.agent_team("lead", "command", &retire, None).unwrap()
    );
    assert!(db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"retire","agent_id":"builder-one","mode":"cancel"}),
            None
        )
        .is_err());
    assert_eq!(
        db.agent_team(
            "lead",
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-one","message":"Build one part"}),
            None
        )
        .unwrap()["created"],
        false
    );
    assert!(db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"list","limit":0}),
            None
        )
        .is_err());
    assert!(db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"retire","agent_id":"lead"}),
            None
        )
        .is_err());
    db.agent_team("lead", "stop", &json!({}), None).unwrap();
    assert!(db
        .agent_team(
            "lead",
            "team_manage",
            &json!({"action":"list","limit":50}),
            None
        )
        .is_ok());
}

pub fn exercise_child_model_selection(db: &mut dyn StateStore) {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    db.agent_team("lead","create",&json!({"agent_id":"lead","agent_types":["builder"],"delegation":{"tools":["coding.read_file"],"models":[{"connection":"Local","model":"alpha"},{"connection":"Other","model":"beta"}],"placements":["team"]}}),None).unwrap();
    let args = json!({"agent_type":"builder","agent_id":"selected","message":"Work","model":{"connection":"Local","model":"alpha"}});
    let first = db.agent_team("lead", "team_spawn", &args, None).unwrap();
    assert_eq!(
        db.agent_team("lead", "team_spawn", &args, None).unwrap()["created"],
        false
    );
    let mut changed = args.clone();
    changed["model"] = json!({"connection":"Other","model":"beta"});
    assert!(db.agent_team("lead", "team_spawn", &changed, None).is_err());
    for model in [
        json!({"connection":"Local","model":"beta"}),
        json!({"connection":"Missing","model":"alpha"}),
        json!({"connection":"Local","model":"../invalid"}),
    ] {
        let mut bad = args.clone();
        bad["agent_id"] = json!("not-allocated");
        bad["model"] = model;
        assert!(db.agent_team("lead", "team_spawn", &bad, None).is_err());
    }
    let snapshot = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(
        snapshot["roster"]["requests"]["selected"]["model"],
        args["model"]
    );
    assert!(snapshot["roster"]["instances"]["not-allocated"].is_null());
    db.agent_team(
        "lead",
        "provision_failed",
        &json!({"agent_id":"selected","error":"Selected model unavailable; no fallback"}),
        None,
    )
    .unwrap();
    assert_eq!(
        db.agent_team("lead", "team_spawn", &args, None).unwrap()["instance"]["session_id"],
        first["instance"]["session_id"]
    );
    assert_eq!(
        db.agent_team("lead", "snapshot", &json!({}), None).unwrap()["roster"]["instances"]
            ["selected"]["phase"],
        "closed"
    );
    db.save(&session("without-delegation", "lead"), "created", json!({}))
        .unwrap();
    db.agent_team(
        "without-delegation",
        "create",
        &json!({"agent_id":"lead","agent_types":["builder"]}),
        None,
    )
    .unwrap();
    assert!(db
        .agent_team("without-delegation", "team_spawn", &args, None)
        .is_err());
}

/// Resolution/admission intents only: this mock-capable domain contract does not
/// provision provider resources, publish source data or acknowledge an import.
pub fn exercise_coding_defaults(db: &mut dyn StateStore) {
    db.save(&session("lead", "lead"), "created", json!({}))
        .unwrap();
    let args = json!({"agent_id":"lead","agent_types":["builder","verifier"],"workspace_selection":true,"placement_selection":true,"coding_selection":true,"delegation":{"tools":["coding.read_file"],"placements":["team","new"],"profiles":{"small":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}},"coding":{"source":{"repository":"org/game","directory":"source","object_format":"sha1","commit":"a".repeat(40)},"agent_types":{"builder":{"sandbox":{"mode":"new","profile":"small","cpu":1,"memory_mb":512},"source":"initial"},"verifier":{"sandbox":{"mode":"new","profile":"small","cpu":1,"memory_mb":512},"source":"require_submission"}}}});
    let mut unavailable = args.clone();
    unavailable["coding_selection"] = json!(false);
    assert!(db.agent_team("lead", "create", &unavailable, None).is_err());
    let doc = db.agent_team("lead", "create", &args, None).unwrap();
    assert_eq!(
        db.agent_team("lead", "create", &args, None).unwrap()["id"],
        doc["id"]
    );
    let request = json!({"agent_type":"builder","agent_id":"ui","message":"Build"});
    let result = db.agent_team("lead", "team_spawn", &request, None).unwrap();
    assert_eq!(result["source"], json!({"kind":"initial"}));
    assert_eq!(
        result["workspace"]["path"],
        format!("checkouts/{}/ui", doc["id"].as_str().unwrap())
    );
    assert_eq!(result["sandbox"]["mode"], "new");
    assert_eq!(
        db.agent_team("lead", "team_spawn", &request, None).unwrap()["instance"],
        result["instance"]
    );
    let verifier =
        json!({"agent_type":"verifier","agent_id":"check","message":"Verify exact submission"});
    assert!(db
        .agent_team("lead", "team_spawn", &verifier, None)
        .is_err());
    let mut wrong = verifier.clone();
    wrong["source"] = json!({"kind":"initial"});
    assert!(db.agent_team("lead", "team_spawn", &wrong, None).is_err());
    let mut selected = verifier;
    selected["source"] = json!({"kind":"submission","id":"handoff_original"});
    let v = db
        .agent_team("lead", "team_spawn", &selected, None)
        .unwrap();
    assert_eq!(v["instance"]["phase"], "reserved");
    assert_eq!(v["source"], selected["source"]);
    let snapshot = db.agent_team("lead", "snapshot", &json!({}), None).unwrap();
    assert_eq!(
        snapshot["roster"]["requests"]["ui"]["source"],
        json!({"kind":"initial"})
    );
    assert_eq!(
        snapshot["roster"]["requests"]["check"]["source"],
        selected["source"]
    );
    assert!(snapshot["workspaces"].as_object().unwrap().is_empty()); // Still needs real import/admission.
    let public = openworker_runtime::agent_team::public_snapshot(snapshot);
    assert!(public.get("coding").is_none());
    assert!(public.get("coding_selection").is_none());
    let shared=db.agent_team("lead","team_spawn",&json!({"agent_type":"builder","agent_id":"shared-source","message":"Build after import","sandbox":{"mode":"team"}}),None).unwrap();
    let dispatched = db
        .agent_team(
            "lead",
            "workspace_dispatch",
            &json!({"agent_id":"shared-source"}),
            None,
        )
        .unwrap();
    let path = format!("/work/{}", shared["workspace"]["path"].as_str().unwrap());
    let receipt = json!({"request":dispatched["intent"]["request"],"path":path,"project_root":"/work","identity":{"volume":1,"file":2},"repository":null,"git_dir":null,"git_identity":null,"branch":null,"initial_commit":"a".repeat(40),"independent_git":{"volume":1,"file":3}});
    let mut child = session(
        shared["instance"]["session_id"].as_str().unwrap(),
        "builder",
    );
    child.workspace = path;
    child.managed_workspace = Some(serde_json::from_value(receipt.clone()).unwrap());
    child.agent_team = Some(Membership {
        team_id: doc["id"].as_str().unwrap().into(),
        agent_id: "shared-source".into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    db.save(&child, "created", json!({})).unwrap();
    db.agent_team("lead","workspace_result",&json!({"agent_id":"shared-source","request":dispatched["intent"]["request"],"receipt":receipt}),None).unwrap();
    assert!(db
        .agent_team("lead", "ready", &json!({"agent_id":"shared-source"}), None)
        .unwrap_err()
        .to_string()
        .contains("source import"));
    let source = json!({"choice":{"kind":"initial"},"manifest":{"version":1,"kind":"source","repository":"org/game","producer":{"team_id":doc["id"],"agent_id":"lead","session_id":"lead"},"object_format":"sha1","base_commit":"a".repeat(40),"submitted_commit":"a".repeat(40),"artifact":{"session_id":"lead","artifact_id":"a_source","size":1,"sha256":"b".repeat(64)},"reports":[]}});
    let mut wrong = source.clone();
    wrong["manifest"]["producer"]["team_id"] = json!("at_other");
    assert!(db
        .agent_team(
            "lead",
            "source_result",
            &json!({"agent_id":"shared-source","source":wrong,"receipt":receipt}),
            None
        )
        .is_err());
    assert!(db.agent_team("lead","command",&json!({"request_id":"not-a-controller","operation":"source_result","arguments":{"agent_id":"shared-source","source":source,"receipt":receipt}}),None).is_err());
    db.agent_team(
        "lead",
        "source_result",
        &json!({"agent_id":"shared-source","source":source,"receipt":receipt}),
        None,
    )
    .unwrap();
    db.agent_team("lead", "ready", &json!({"agent_id":"shared-source"}), None)
        .unwrap();
    let mut changed = args.clone();
    changed["coding"]["source"]["commit"] = json!("b".repeat(40));
    assert!(db.agent_team("lead", "create", &changed, None).is_err());
    let mut both = args;
    both["workspace_defaults"] =
        json!({"repository":"source","base":"a".repeat(40),"agent_types":["builder"]});
    assert!(db.agent_team("lead", "create", &both, None).is_err());
}
