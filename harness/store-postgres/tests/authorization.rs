use openworker_runtime::{
    control::{Action as Control, Command},
    store::{Session, StateStore},
};
use openworker_store_postgres::{
    auth::{Action, Error, Role, Scope, Spec},
    Config, PostgresStore, SessionCreation, Tls, TurnSubmission,
};
use serde_json::json;
use std::{sync::mpsc, time::Duration};
const ADMIN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ALICE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const BOB: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
const OBSERVER: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
fn fixture() -> (Config, PostgresStore, PostgresStore) {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_auth_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let store = PostgresStore::open(c.clone()).unwrap();
    store.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = store.authorized(ADMIN).unwrap();
    let mut db = sql(&c);
    db.execute("INSERT INTO environments(id,request_id,data) VALUES('env_one','env-one','{}'),('env_two','env-two','{}')",&[]).unwrap();
    (c, store, admin)
}
fn sql(c: &Config) -> postgres::Client {
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    db
}
fn issue(admin: &PostgresStore, key: &str, principal: &str, role: Role, scope: Scope) -> String {
    admin
        .auth_issue(
            principal,
            Spec {
                expires_at: None,
                principal: principal.into(),
                role,
                scope,
                environments: if role == Role::Operator {
                    vec!["env_one".into()]
                } else {
                    vec![]
                },
            },
            key,
        )
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .into()
}
fn session(id: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":id,"workspace":"/work","coworker":"test","bundle_digest":"test","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
fn request(actor: &str, id: &str, env: &str) -> SessionCreation {
    SessionCreation {
        request_id: format!("create-{id}"),
        actor: actor.into(),
        parameters: json!({"environment_id":env}),
    }
}
fn submit(sid: &str, id: &str) -> TurnSubmission {
    TurnSubmission {
        limits: None,
        session_id: sid.into(),
        submission_id: id.into(),
        text: "private prompt marker".into(),
    }
}
fn denied<T>(r: anyhow::Result<T>, e: Error) {
    assert_eq!(
        r.err().expect("unexpected grant").downcast_ref::<Error>(),
        Some(&e)
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn role_owned_session_environment_and_actor_matrix() {
    let (c, mut root, admin) = fixture();
    issue(&admin, ALICE, "alice", Role::Operator, Scope::Owned);
    issue(&admin, BOB, "bob", Role::Operator, Scope::Owned);
    issue(&admin, OBSERVER, "observer", Role::Observer, Scope::All);
    let alice = root.authorized(ALICE).unwrap();
    let bob = root.authorized(BOB).unwrap();
    let observer = root.authorized(OBSERVER).unwrap();
    denied(
        observer.auth_issue(
            "escalate",
            Spec {
                expires_at: None,
                principal: "evil".into(),
                role: Role::Administrator,
                scope: Scope::All,
                environments: vec![],
            },
            "e".repeat(64).as_str(),
        ),
        Error::Forbidden,
    );
    denied(alice.auth_list(""), Error::Forbidden);
    denied(
        alice.create_session(request("bob", "s_spoof", "env_one"), session("s_spoof")),
        Error::Forbidden,
    );
    denied(
        alice.create_session(request("alice", "s_wrong", "env_two"), session("s_wrong")),
        Error::Forbidden,
    );
    denied(
        observer.create_session(
            request("observer", "s_readonly", "env_one"),
            session("s_readonly"),
        ),
        Error::Forbidden,
    );
    alice
        .create_session(request("alice", "s_alice", "env_one"), session("s_alice"))
        .unwrap();
    bob.create_session(request("bob", "s_bob", "env_one"), session("s_bob"))
        .unwrap();
    assert_eq!(
        alice.session_page(None, 1).unwrap()["sessions"][0]["id"],
        "s_alice"
    );
    assert_eq!(
        alice.session_page(None, 1).unwrap()["next_after"],
        serde_json::Value::Null
    );
    denied(
        alice.session_page(Some("s_bob".into()), 1),
        Error::Forbidden,
    );
    denied(bob.read_snapshot("s_alice"), Error::Forbidden);
    denied(bob.read_events("s_alice", 0), Error::Forbidden);
    denied(bob.artifacts("s_alice"), Error::Forbidden);
    assert_eq!(
        observer.session_page(None, 100).unwrap()["sessions"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    denied(
        observer.submit_turn(submit("s_alice", "one")),
        Error::Forbidden,
    );
    alice.submit_turn(submit("s_alice", "one")).unwrap();
    denied(bob.submission("s_alice", "one"), Error::Forbidden);
    root.claim("s_alice").unwrap();
    let (s, _, _) = root
        .accept("s_alice", "one", "private prompt marker")
        .unwrap();
    let command = Command {
        session_id: s.id.clone(),
        turn_id: s.turn.unwrap().id,
        request_id: "cancel".into(),
        actor: "alice".into(),
        action: Control::Cancel,
    };
    denied(observer.submit_control(command.clone()), Error::Forbidden);
    let mut spoof = command.clone();
    spoof.actor = "bob".into();
    denied(alice.submit_control(spoof), Error::Forbidden);
    alice.submit_control(command).unwrap();
    let log = admin.auth_audit(0).unwrap();
    assert!(log["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["action"] == "turn.submit" && e["principal"] == "alice"));
    assert!(!log.to_string().contains("private prompt marker"));
    assert!(!log.to_string().contains(ALICE));
    // Ownership follows the principal, while role and revocation stay key-specific.
    let rotated = "e".repeat(64);
    admin
        .auth_issue(
            "alice-rotated",
            Spec {
                expires_at: None,
                principal: "alice".into(),
                role: Role::Observer,
                scope: Scope::Owned,
                environments: vec![],
            },
            &rotated,
        )
        .unwrap();
    let reader = root.authorized(&rotated).unwrap();
    reader.read_snapshot("s_alice").unwrap();
    denied(reader.read_snapshot("s_bob"), Error::Forbidden);
    denied(
        reader.submit_turn(submit("s_alice", "rotated-write")),
        Error::Forbidden,
    );
    let mut db = sql(&c);
    assert_eq!(
        db.query_one(
            "SELECT actor FROM audit WHERE session_id='s_alice' AND kind='command_accepted'",
            &[]
        )
        .unwrap()
        .get::<_, String>(0),
        "alice"
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn plans_inherit_session_read_authorization_for_snapshot_and_replay() {
    let (_c, mut root, admin) = fixture();
    issue(&admin, ALICE, "alice", Role::Operator, Scope::Owned);
    issue(&admin, BOB, "bob", Role::Operator, Scope::Owned);
    let alice = root.authorized(ALICE).unwrap();
    let bob = root.authorized(BOB).unwrap();
    let mut s = session("s_plan");
    s.package.planning = true;
    alice
        .create_session(request("alice", "s_plan", "env_one"), s)
        .unwrap();
    root.claim("s_plan").unwrap();
    let mut s = root.get("s_plan").unwrap();
    let plan = openworker_runtime::planning::next(
        None,
        "turn",
        "call",
        &json!({"expected_revision":0,"explanation":"ALICE_PRIVATE_PLAN","steps":[]}),
    )
    .unwrap();
    s.plan = Some(plan.clone());
    root.save(&s, "plan_updated", json!({"plan":plan})).unwrap();
    assert_eq!(alice.read_snapshot("s_plan").unwrap()["plan"], json!(plan));
    assert!(alice
        .read_events("s_plan", 0)
        .unwrap()
        .iter()
        .any(|e| e.kind == "plan_updated"));
    denied(bob.read_snapshot("s_plan"), Error::Forbidden);
    denied(bob.read_events("s_plan", 0), Error::Forbidden);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn team_read_write_and_member_admission_remain_session_scoped() {
    use openworker_runtime::teams::{Member, Role as TeamRole};
    use openworker_store_postgres::teams::{Command, Create};
    let (_, root, admin) = fixture();
    issue(&admin, ALICE, "alice", Role::Operator, Scope::Owned);
    issue(&admin, BOB, "bob", Role::Operator, Scope::Owned);
    issue(&admin, OBSERVER, "observer", Role::Observer, Scope::All);
    let alice = root.authorized(ALICE).unwrap();
    let bob = root.authorized(BOB).unwrap();
    let observer = root.authorized(OBSERVER).unwrap();
    for (id, role) in [("s_lead", TeamRole::Lead), ("s_worker", TeamRole::Worker)] {
        let mut s = session(id);
        s.package.team_role = Some(role);
        alice
            .create_session(request("alice", id, "env_one"), s)
            .unwrap();
    }
    let create = Create {
        request_id: "create-team".into(),
        name: "Private team".into(),
        members: vec![
            Member {
                session_id: "s_lead".into(),
                name: "Lead".into(),
                role: TeamRole::Lead,
            },
            Member {
                session_id: "s_worker".into(),
                name: "Worker".into(),
                role: TeamRole::Worker,
            },
        ],
    };
    denied(bob.create_team(create.clone()), Error::Forbidden);
    denied(observer.create_team(create.clone()), Error::Forbidden);
    let team = alice.create_team(create).unwrap()["team_id"]
        .as_str()
        .unwrap()
        .to_owned();
    alice.team_snapshot(&team).unwrap();
    observer.team_snapshot(&team).unwrap();
    denied(bob.team_snapshot(&team), Error::Forbidden);
    denied(bob.team_events(&team, 0), Error::Forbidden);
    let command = Command {
        request_id: "msg".into(),
        operation: "team_post_message".into(),
        arguments: json!({"body":"PRIVATE_MESSAGE","item_id":"","recipient":""}),
    };
    denied(
        observer.team_command(&team, command.clone()),
        Error::Forbidden,
    );
    denied(bob.team_command(&team, command.clone()), Error::Forbidden);
    alice.team_command(&team, command).unwrap();
    assert!(alice
        .team_events(&team, 0)
        .unwrap()
        .to_string()
        .contains("PRIVATE_MESSAGE"));
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn explicit_scopes_revocation_restarts_and_bootstrap_do_not_resurrect_keys() {
    let (c, root, admin) = fixture();
    admin
        .create_session(request("admin", "s_one", "env_one"), session("s_one"))
        .unwrap();
    admin
        .create_session(request("admin", "s_two", "env_one"), session("s_two"))
        .unwrap();
    let key = issue(
        &admin,
        OBSERVER,
        "observer",
        Role::Observer,
        Scope::Sessions {
            ids: vec!["s_one".into()],
        },
    );
    let observed = root.authorized(OBSERVER).unwrap();
    let other = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(OBSERVER)
        .unwrap();
    assert_eq!(
        observed.session_page(None, 1).unwrap()["sessions"][0]["id"],
        "s_one"
    );
    denied(observed.read_snapshot("s_two"), Error::Forbidden);
    other.read_snapshot("s_one").unwrap();
    let revoked = admin.auth_revoke(&key).unwrap();
    assert_eq!(revoked["revoked"], true);
    denied(observed.read_snapshot("s_one"), Error::Unauthorized);
    denied(other.read_snapshot("s_one"), Error::Unauthorized);
    assert_eq!(admin.auth_revoke(&key).unwrap(), revoked);
    assert_eq!(
        admin
            .auth_issue(
                "observer",
                Spec {
                    expires_at: None,
                    principal: "observer".into(),
                    role: Role::Observer,
                    scope: Scope::Sessions {
                        ids: vec!["s_one".into()]
                    },
                    environments: vec![]
                },
                OBSERVER
            )
            .unwrap()["revoked"],
        true
    );
    let fresh = PostgresStore::open(c).unwrap();
    denied(
        fresh
            .authorized(OBSERVER)
            .unwrap()
            .authorize(Action::Read, None, None),
        Error::Unauthorized,
    );
    let bootstrap = root.auth_bootstrap(ADMIN, "admin").unwrap();
    denied(
        admin.auth_revoke(bootstrap["id"].as_str().unwrap()),
        Error::Conflict,
    );
    issue(
        &admin,
        ALICE,
        "second-admin",
        Role::Administrator,
        Scope::All,
    );
    admin
        .auth_revoke(bootstrap["id"].as_str().unwrap())
        .unwrap();
    assert_eq!(
        root.auth_bootstrap(ADMIN, "admin").unwrap()["revoked"],
        true
    );
    denied(admin.auth_list(""), Error::Unauthorized);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn admitted_write_serializes_before_revoke_and_audit_failure_rolls_back() {
    let (c, root, admin) = fixture();
    let key = issue(&admin, ALICE, "alice", Role::Operator, Scope::Owned);
    let alice = root.authorized(ALICE).unwrap();
    alice
        .create_session(request("alice", "s_one", "env_one"), session("s_one"))
        .unwrap();
    let mut db = sql(&c);
    let mut held = db.transaction().unwrap();
    held.query_one("SELECT id FROM sessions WHERE id='s_one' FOR UPDATE", &[])
        .unwrap();
    let writer = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(ALICE)
        .unwrap();
    let writing = std::thread::spawn(move || writer.submit_turn(submit("s_one", "in-flight")));
    let mut inspect = sql(&c);
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    loop {
        let locked:bool=inspect.query_one("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE relation='api_credentials'::regclass AND mode='RowShareLock' AND granted)",&[]).unwrap().get(0);
        if locked {
            break;
        }
        assert!(std::time::Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(10));
    }
    let revoker = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(ADMIN)
        .unwrap();
    let (tx, rx) = mpsc::channel();
    let revoking = std::thread::spawn(move || {
        let result = revoker.auth_revoke(&key);
        tx.send(()).unwrap();
        result
    });
    assert!(rx.recv_timeout(Duration::from_millis(100)).is_err());
    held.commit().unwrap();
    writing.join().unwrap().unwrap();
    revoking.join().unwrap().unwrap();
    denied(
        alice.submit_turn(submit("s_one", "late")),
        Error::Unauthorized,
    );
    inspect.batch_execute("CREATE FUNCTION reject_security() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit fault'; END $$; CREATE TRIGGER reject_security BEFORE INSERT ON api_security_audit FOR EACH ROW EXECUTE FUNCTION reject_security()").unwrap();
    assert!(admin.submit_turn(submit("s_one", "rollback")).is_err());
    assert_eq!(
        inspect
            .query_one("SELECT count(*) FROM submissions WHERE id='rollback'", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn audit_cursor_cannot_skip_a_delayed_commit() {
    let (c, _, admin) = fixture();
    let before = admin.auth_audit(0).unwrap()["next_after"].as_i64().unwrap();
    let mut db = sql(&c);
    let mut first = db.transaction().unwrap();
    first.query_one("SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('security-audit-append-v1'))",&[]).unwrap();
    first
        .execute(
            "INSERT INTO api_security_audit(action,outcome) VALUES('delayed','admitted')",
            &[],
        )
        .unwrap();
    let other = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(ADMIN)
        .unwrap();
    let (tx, rx) = mpsc::channel();
    let write = std::thread::spawn(move || {
        let r = other.auth_denied(Action::Read);
        tx.send(()).unwrap();
        r
    });
    assert!(rx.recv_timeout(Duration::from_millis(100)).is_err());
    let page = admin.auth_audit(before).unwrap();
    assert!(page["events"].as_array().unwrap().is_empty());
    assert_eq!(page["next_after"], before);
    first.commit().unwrap();
    write.join().unwrap().unwrap();
    let page = admin.auth_audit(before).unwrap();
    assert_eq!(page["events"].as_array().unwrap().len(), 2);
    assert_eq!(page["events"][0]["action"], "delayed");
    assert_eq!(page["events"][1]["outcome"], "denied");
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn expiry_and_deployment_roles_are_independent_of_tool_control() {
    let (c, store, admin) = fixture();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let spec = Spec {
        principal: "publisher".into(),
        role: Role::Publisher,
        scope: Scope::All,
        environments: vec![],
        expires_at: Some(now + 600),
    };
    let issued = admin
        .auth_issue("publisher-1", spec.clone(), ALICE)
        .unwrap();
    let publisher = store.authorized(ALICE).unwrap();
    assert!(publisher.authorize(Action::Publish, None, None).is_ok());
    for action in [
        Action::Activate,
        Action::Submit,
        Action::Control,
        Action::Admin,
    ] {
        assert!(publisher.authorize(action, None, None).is_err());
    }
    let profile = json!({"id":"dev@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","allowedPrincipals":["runner"]});
    admin
        .catalog_put("profile", "profile", profile, "install")
        .unwrap();
    admin
        .catalog_activate("profile-active", "profile", "dev@1", None)
        .unwrap();
    assert!(publisher.catalog_get("profile", "dev@1").is_ok());
    let release = json!({"id":"agent@1","instructions":"Test","tools":["read_file"],"runtimeProfiles":["dev@1"],"defaultRuntimeProfile":"dev@1"});
    publisher
        .catalog_put("release", "release", release, "install")
        .unwrap();
    assert!(publisher
        .catalog_activate("activate", "release", "agent@1", None)
        .is_err());
    let reviewer = Spec {
        principal: "releaser".into(),
        role: Role::ReleaseAdministrator,
        scope: Scope::All,
        environments: vec![],
        expires_at: Some(now + 600),
    };
    admin.auth_issue("releaser", reviewer, BOB).unwrap();
    let reviewer = store.authorized(BOB).unwrap();
    reviewer
        .catalog_activate("activate", "release", "agent@1", None)
        .unwrap();
    let audit = admin.auth_audit(0).unwrap();
    assert!(audit["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["action"] == "release.publish" && e["target"] == "agent@1"));
    assert!(reviewer.authorize(Action::Control, None, None).is_err());
    assert!(reviewer.authorize(Action::Admin, None, None).is_err());
    // Database time is authoritative; an already-open authorized handle must expire too.
    let mut pg = sql(&c);
    pg.execute("UPDATE api_credentials SET spec=jsonb_set(spec,'{expires_at}',to_jsonb(1::bigint)) WHERE id=$1",&[&issued["id"].as_str().unwrap()]).unwrap();
    assert!(matches!(
        publisher
            .authorize(Action::Read, None, None)
            .unwrap_err()
            .downcast_ref::<Error>(),
        Some(Error::Unauthorized)
    ));
    let mut changed = spec;
    changed.expires_at = Some(now + 1200);
    assert!(admin.auth_issue("publisher-1", changed, ALICE).is_err());
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn expired_bootstrap_cannot_reinitialize_or_extend_its_lifetime() {
    let (c, store, admin) = fixture();
    let mut pg = sql(&c);
    pg.batch_execute("UPDATE api_credentials SET spec=jsonb_set(spec,'{expires_at}',to_jsonb(1::bigint)) WHERE request_id='bootstrap'").unwrap();
    assert!(admin.authorize(Action::Admin, None, None).is_err());
    assert_eq!(
        store
            .auth_bootstrap_expiring(ADMIN, "admin", Some(2_000_000_000))
            .unwrap()["spec"]["expires_at"],
        1
    );
    assert!(store
        .auth_bootstrap_expiring(BOB, "admin", Some(2_000_000_000))
        .is_err());
    assert!(admin.authorize(Action::Admin, None, None).is_err());
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    assert!(admin
        .auth_recover("denied", BOB, "recovered", now + 600)
        .is_err());
    let recovered = store
        .auth_recover("recover", BOB, "recovered", now + 600)
        .unwrap();
    assert_eq!(
        store
            .auth_recover("recover", BOB, "recovered", now + 1200)
            .unwrap(),
        recovered
    );
    let recovered = store.authorized(BOB).unwrap();
    assert!(recovered.authorize(Action::Admin, None, None).is_ok());
    assert!(recovered.authorize(Action::Control, None, None).is_err());
    assert!(admin.authorize(Action::Admin, None, None).is_err());
    pg.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
