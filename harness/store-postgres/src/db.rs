use crate::{Config, Lease, Tls};
use anyhow::{bail, Context, Result};
use openworker_runtime::{
    provider::Failure,
    store::{terminal, Event, Session, Turn},
};
use postgres::{Client, Transaction};
use serde_json::{json, Value};
use std::{collections::BTreeMap, str::FromStr};

/// Managed databases publish CA rotation bundles, not necessarily a single root.
/// Parse every certificate with a maintained PEM parser; don't silently trust
/// only the first root or accept an empty/private-key/malformed trust file.
fn ca_certificates(bytes: &[u8]) -> Result<Vec<native_tls::Certificate>> {
    anyhow::ensure!(bytes.len() <= 262_144, "PostgreSQL CA bundle too large");
    let mut input = std::io::Cursor::new(bytes);
    let mut certificates = Vec::new();
    for item in rustls_pemfile::read_all(&mut input) {
        let item = item.context("Invalid PostgreSQL CA bundle")?;
        let rustls_pemfile::Item::X509Certificate(der) = item else {
            bail!("PostgreSQL CA bundle must contain certificates only");
        };
        anyhow::ensure!(
            certificates.len() < 32,
            "Too many PostgreSQL CA certificates"
        );
        certificates.push(
            native_tls::Certificate::from_der(der.as_ref())
                .context("Invalid PostgreSQL CA certificate")?,
        );
    }
    anyhow::ensure!(!certificates.is_empty(), "PostgreSQL CA bundle is empty");
    Ok(certificates)
}

fn connect(config: &Config) -> Result<Client> {
    let mut c = postgres::Config::from_str(&config.connection)
        .map_err(|_| anyhow::anyhow!("Invalid PostgreSQL connection configuration"))?;
    c.connect_timeout(config.timeout);
    // Require encryption by default, regardless of a weaker mode in the URL.
    let mut client = match &config.tls {
        Tls::Verified { ca_file } => {
            c.ssl_mode(postgres::config::SslMode::Require);
            let mut tls = native_tls::TlsConnector::builder();
            if let Some(path) = ca_file {
                use std::io::Read;
                let mut bytes = Vec::new();
                std::fs::File::open(path)
                    .context("Cannot read configured PostgreSQL CA file")?
                    .take(262_145)
                    .read_to_end(&mut bytes)
                    .context("Cannot read configured PostgreSQL CA file")?;
                for certificate in ca_certificates(&bytes)? {
                    tls.add_root_certificate(certificate);
                }
            }
            c.connect(postgres_native_tls::MakeTlsConnector::new(
                tls.build()
                    .context("PostgreSQL TLS initialization failed")?,
            ))?
        }
        Tls::LoopbackPlaintext => {
            if c.get_hosts().len() != 1
                || !matches!(&c.get_hosts()[0],postgres::config::Host::Tcp(h) if matches!(h.as_str(),"127.0.0.1"|"localhost"|"::1"))
                || !c.get_hostaddrs().is_empty()
            {
                bail!("Plaintext PostgreSQL is restricted to one loopback host with no hostaddr override");
            }
            c.ssl_mode(postgres::config::SslMode::Disable);
            c.connect(postgres::NoTls)?
        }
    };
    let millis = config.timeout.as_millis().saturating_mul(3) / 4;
    client.batch_execute(&format!("SET statement_timeout={millis}; SET lock_timeout={millis}; SET idle_in_transaction_session_timeout={millis}; SET search_path={},pg_catalog",config.schema))?;
    Ok(client)
}
pub(crate) fn migrate(config: &Config) -> Result<()> {
    let mut c = connect(config)?;
    let mut tx = c.transaction()?;
    tx.query_one(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        &[&config.schema],
    )?;
    tx.batch_execute(&format!("CREATE SCHEMA IF NOT EXISTS {}", config.schema))?;
    let exists: bool = tx
        .query_one("SELECT to_regclass('version') IS NOT NULL", &[])?
        .get(0);
    if exists {
        let rows = tx.query("SELECT version FROM version WHERE singleton", &[])?;
        if rows.len() != 1 || rows[0].get::<_, i32>(0) != 5 {
            bail!("Unsupported PostgreSQL schema version; migration required");
        }
    } else {
        // Do not adopt/reset an unrelated partially populated schema.
        let count: i64 = tx
            .query_one(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema=$1",
                &[&config.schema],
            )?
            .get(0);
        if count != 0 {
            bail!("Refusing to initialize a nonempty unversioned PostgreSQL schema");
        }
        tx.batch_execute(include_str!("schema.sql"))?;
        tx.execute("INSERT INTO version VALUES(TRUE,5)", &[])?;
    }
    crate::artifacts::migrate(&mut tx)?;
    crate::auth::migrate(&mut tx)?;
    crate::pools::migrate(&mut tx)?;
    crate::teams::migrate(&mut tx)?;
    crate::assistant::migrate(&mut tx)?;
    crate::service::migrate(&mut tx)?;
    tx.batch_execute("CREATE TABLE IF NOT EXISTS sdk_host_mode(singleton BOOLEAN PRIMARY KEY CHECK(singleton), mode TEXT NOT NULL CHECK(mode='sdk'))")?;
    tx.commit()?;
    Ok(())
}
pub(crate) struct Database {
    pub(crate) auth: Option<crate::auth::Context>,
    telemetry: openworker_runtime::telemetry::Telemetry,
    pub(crate) client: Client,
    pub(crate) owner: String,
    pub(crate) leases: BTreeMap<String, i64>,
    lease_ms: i64,
    pub(crate) max_queued_turns: u16,
}
pub(crate) fn metadata(row: &postgres::Row) -> Result<Session> {
    let mut s: Session = serde_json::from_value(row.get("data"))?;
    s.storage_revision = row.get("revision");
    Ok(s)
}
pub(crate) fn locked(
    tx: &mut Transaction<'_>,
    sid: &str,
    owner: Option<(&str, i64)>,
) -> Result<Session> {
    let row = tx
        .query_opt(
            "SELECT *,expires>clock_timestamp() AS valid FROM sessions WHERE id=$1 FOR UPDATE",
            &[&sid],
        )?
        .ok_or(crate::SubmissionError::NotFound)?;
    if let Some((name, epoch)) = owner {
        if row.get::<_, String>("owner") != name
            || row.get::<_, i64>("epoch") != epoch
            || !row.get::<_, bool>("valid")
        {
            bail!("Session ownership lost or expired");
        }
    }
    metadata(&row)
}
pub(crate) fn read_history(tx: &mut Transaction<'_>, s: &mut Session) -> Result<()> {
    s.history.clear();
    for row in tx.query(
        "SELECT seq,data FROM history WHERE session_id=$1 ORDER BY seq",
        &[&s.id],
    )? {
        if row.get::<_, i64>(0) != s.history.len() as i64 {
            bail!("Canonical history gap; stored data incomplete");
        }
        s.history.push(row.get(1));
    }
    verify_history_count(tx, &s.id)?;
    Ok(())
}
pub(crate) fn verify_history_count(tx: &mut Transaction<'_>, sid: &str) -> Result<()> {
    let ok:bool=tx.query_one("SELECT history_count=(SELECT count(*) FROM history WHERE session_id=$1) FROM sessions WHERE id=$1", &[&sid])?.get(0);
    if !ok {
        bail!("Canonical history missing; stored data incomplete");
    }
    Ok(())
}
pub(crate) fn append_history(tx: &mut Transaction<'_>, s: &Session) -> Result<()> {
    verify_history_count(tx, &s.id)?;
    if s.history.len() > 200_000 {
        bail!("Session history item quota reached");
    }
    let mut count = 0usize;
    let mut bytes = 0usize;
    for row in tx.query(
        "SELECT seq,data FROM history WHERE session_id=$1 ORDER BY seq",
        &[&s.id],
    )? {
        let data: Value = row.get(1);
        if row.get::<_, i64>(0) != count as i64 || s.history.get(count) != Some(&data) {
            bail!("Canonical history is append-only; stale or modified prefix refused");
        }
        bytes += data.to_string().len();
        count += 1;
    }
    for (seq, item) in s.history.iter().enumerate().skip(count) {
        let size = item.to_string().len();
        bytes += size;
        if size > 512000 || bytes > 64 * 1024 * 1024 {
            bail!("Session history storage quota reached; existing history retained");
        }
        tx.execute(
            "INSERT INTO history VALUES($1,$2,$3)",
            &[&s.id, &(seq as i64), item],
        )?;
    }
    tx.execute(
        "UPDATE sessions SET history_count=$2 WHERE id=$1",
        &[&s.id, &(s.history.len() as i64)],
    )?;
    Ok(())
}
pub(crate) fn write_metadata(tx: &mut Transaction<'_>, s: &Session) -> Result<()> {
    crate::environments::validate_session(tx, s)?;
    let mut v = serde_json::to_value(s)?;
    v.as_object_mut().unwrap().remove("storage_revision");
    if v.to_string().len() > 1024 * 1024 {
        bail!("Session metadata exceeds storage limit");
    }
    tx.execute(
        "UPDATE sessions SET data=$2,environment_id=$3 WHERE id=$1",
        &[
            &s.id,
            &v,
            &s.environment.as_ref().map(|e| e.environment_id.as_str()),
        ],
    )?;
    crate::submissions::finish(tx, s)?;
    Ok(())
}
pub(crate) fn event(
    tx: &mut Transaction<'_>,
    s: &Session,
    owner: &str,
    epoch: i64,
    kind: &str,
    data: Value,
) -> Result<Event> {
    if kind.len() > 100 || data.to_string().len() > 1024 * 1024 {
        bail!("Event exceeds storage limit");
    }
    crate::controls::before_event(tx, s, kind, &data)?;
    crate::team_execution::check(tx, s, kind)?;
    // The row lock serializes cursor allocation and ownership transfer. A final
    // database-time check guards each commit, never a cached local lease alone.
    let row=tx.query_opt("UPDATE sessions SET cursor=cursor+1,revision=revision+1 WHERE id=$1 AND owner=$2 AND epoch=$3 AND expires>clock_timestamp() AND revision=$4 RETURNING cursor", &[&s.id,&owner,&epoch,&s.storage_revision])?.context("Session ownership or revision changed before commit")?;
    let cursor: i64 = row.get(0);
    let tid = s.turn.as_ref().map(|t| t.id.clone());
    tx.execute(
        "INSERT INTO events VALUES($1,$2,$3,$4,$5)",
        &[&s.id, &cursor, &tid, &kind, &data],
    )?;
    // Metadata audit is committed with the event. No raw arguments/content.
    tx.execute(
        "INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch,actor) VALUES($1,$2,$3,$4,$5,$6)",
        &[&s.id, &cursor, &tid, &kind, &epoch, &data["actor"].as_str()],
    )?;
    Ok(Event {
        event_id: cursor,
        session_id: s.id.clone(),
        turn_id: tid,
        kind: kind.into(),
        data,
    })
}
impl Database {
    pub fn open(config: Config) -> Result<Self> {
        let mut client = connect(&config)?;
        let rows = client.query("SELECT version FROM version WHERE singleton", &[])?;
        if rows.len() != 1 || rows[0].get::<_, i32>(0) != 5 {
            bail!("Unsupported PostgreSQL schema; run explicit migration");
        }
        let owner = uuid::Uuid::new_v4().to_string();
        client.query_one(
            "SELECT set_config('application_name',$1,false)",
            &[&format!("ow-store-{owner}")],
        )?;
        Ok(Self {
            auth: None,
            telemetry: config.telemetry.clone(),
            client,
            owner,
            leases: Default::default(),
            lease_ms: config.lease_duration.as_millis() as i64,
            max_queued_turns: config.max_queued_turns,
        })
    }
    pub(crate) fn epoch(&self, sid: &str) -> Result<i64> {
        self.leases
            .get(sid)
            .copied()
            .context("Session not owned by this store; explicitly claim it first")
    }
    pub fn inspect(&mut self, sid: &str) -> Result<Session> {
        let mut tx = self.client.transaction()?;
        let mut s = locked(&mut tx, sid, None)?;
        read_history(&mut tx, &mut s)?;
        tx.commit()?;
        Ok(s)
    }
    pub fn get(&mut self, sid: &str, history: bool) -> Result<Session> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let mut s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        if history {
            read_history(&mut tx, &mut s)?;
        }
        tx.commit()?;
        Ok(s)
    }
    pub fn list(&mut self, history: bool) -> Result<Vec<Session>> {
        let ids: Vec<_> = self.leases.keys().cloned().collect();
        ids.into_iter().map(|sid| self.get(&sid, history)).collect()
    }
    pub fn claim(&mut self, sid: &str) -> Result<Lease> {
        if self.leases.contains_key(sid) {
            bail!("Store already has a claim; renew or release explicitly");
        }
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, None)?;
        let row=tx.query_opt("UPDATE sessions SET owner=$2,epoch=epoch+1,expires=clock_timestamp()+$3::bigint*interval '1 millisecond' WHERE id=$1 AND expires<=clock_timestamp() RETURNING epoch", &[&sid,&self.owner,&self.lease_ms])?.context("Session already has a live owner")?;
        let epoch: i64 = row.get(0);
        event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "owner_claimed",
            json!({"epoch":epoch}),
        )?;
        tx.commit()?;
        self.leases.insert(sid.into(), epoch);
        Ok(Lease {
            session_id: sid.into(),
            owner: self.owner.clone(),
            epoch,
        })
    }
    pub fn claim_next(&mut self, selector: &crate::worker::Selector) -> Result<Option<Lease>> {
        if self
            .client
            .query_one("SELECT to_regclass('sdk_host_mode') IS NOT NULL", &[])?
            .get::<_, bool>(0)
            && self
                .client
                .query_one("SELECT EXISTS(SELECT 1 FROM sdk_host_mode)", &[])?
                .get::<_, bool>(0)
        {
            bail!("This schema is owned by the SDK host, not a distributed worker pool");
        }
        if !self.leases.is_empty() {
            bail!("Scheduled slot must not already own a session");
        }
        self.dispatch_team_completion()?;
        let mut tx = self.client.transaction()?;
        let row = tx.query_opt(
            "SELECT id FROM sessions s WHERE expires<=clock_timestamp() AND NOT (data->>'closed')::boolean
             AND data->>'coworker'=$1 AND data->>'bundle_digest'=$2
             AND data->>'execution_backend'=$3 AND (data->>'scripted')::boolean=$4
             AND COALESCE(data->'environment'->'spec'->>'provider','')=$5
             AND ((NOT (data ? 'model_selection') AND data->>'model'=$6 AND COALESCE(data->>'model_provider','openai')=$9)
                  OR ($10::jsonb->>(data->'model_selection'->>'connection'))=data->'model_selection'->>'identity')
             AND (NOT (data ? 'reviewer_selection') OR ($10::jsonb->>(data->'reviewer_selection'->>'connection'))=data->'reviewer_selection'->>'identity')
             AND COALESCE(data->'environment'->'spec'->>'provider_identity','')=$7
             AND COALESCE(data->'environment'->'spec'->>'profile_digest','')=$8
             AND (EXISTS(SELECT 1 FROM controls c WHERE c.session_id=s.id AND c.status='pending')
               OR (COALESCE(data->'turn'->>'status','idle') IN ('idle','completed','failed','cancelled','interrupted','truncated')
                   AND NOT COALESCE((data->>'provider_blocked')::boolean,FALSE)
                   AND EXISTS(SELECT 1 FROM submissions q WHERE q.session_id=s.id AND q.status='queued'))
               OR COALESCE(data->'turn'->>'status','idle') IN ('accepted','running','executing','cancelling'))
             ORDER BY expires,created LIMIT 1 FOR UPDATE SKIP LOCKED",
            &[&selector.coworker,&selector.bundle_digest,&selector.backend.name(),&selector.scripted,&selector.environment_provider.as_deref().unwrap_or(""),&selector.fallback_model,&selector.environment_identity.as_deref().unwrap_or(""),&selector.environment_profile.as_deref().unwrap_or(""),&selector.model_provider.name(),&json!(selector.model_connections)])?;
        let Some(row) = row else {
            tx.commit()?;
            return Ok(None);
        };
        let sid: String = row.get(0);
        let s = locked(&mut tx, &sid, None)?;
        let epoch:i64=tx.query_one("UPDATE sessions SET owner=$2,epoch=epoch+1,expires=clock_timestamp()+$3::bigint*interval '1 millisecond' WHERE id=$1 RETURNING epoch",&[&sid,&self.owner,&self.lease_ms])?.get(0);
        event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "owner_claimed",
            json!({"epoch":epoch,"scheduled":true}),
        )?;
        tx.commit()?;
        self.leases.insert(sid.clone(), epoch);
        Ok(Some(Lease {
            session_id: sid,
            owner: self.owner.clone(),
            epoch,
        }))
    }
    pub fn renew(&mut self, sid: &str) -> Result<Lease> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        if tx.execute("UPDATE sessions SET expires=clock_timestamp()+$4::bigint*interval '1 millisecond' WHERE id=$1 AND owner=$2 AND epoch=$3 AND expires>clock_timestamp()", &[&sid,&self.owner,&epoch,&self.lease_ms])?!=1 {bail!("Session ownership lost or expired");}
        tx.commit()?;
        Ok(Lease {
            session_id: sid.into(),
            owner: self.owner.clone(),
            epoch,
        })
    }
    pub fn release(&mut self, sid: &str) -> Result<()> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "owner_released",
            json!({"epoch":epoch}),
        )?;
        tx.execute(
            "UPDATE sessions SET expires=clock_timestamp() WHERE id=$1",
            &[&sid],
        )?;
        tx.commit()?;
        self.leases.remove(sid);
        Ok(())
    }
    pub fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event> {
        let existing = self.leases.get(&s.id).copied();
        let mut tx = self.client.transaction()?;
        let epoch = if let Some(epoch) = existing {
            let old = locked(&mut tx, &s.id, Some((&self.owner, epoch)))?;
            if old.storage_revision != s.storage_revision {
                bail!("Stale session revision refused");
            }
            if old.environment != s.environment {
                bail!("Environment binding is immutable; create a new session");
            }
            epoch
        } else {
            if s.storage_revision != 0 {
                bail!("Existing session requires an explicit claim");
            }
            if tx.execute("INSERT INTO sessions(id,data,owner,epoch,expires) VALUES($1,'{}',$2,1,clock_timestamp()+$3::bigint*interval '1 millisecond') ON CONFLICT DO NOTHING", &[&s.id,&self.owner,&self.lease_ms])?!=1 {bail!("Session already exists; claim it explicitly");}
            1
        };
        append_history(&mut tx, s)?;
        write_metadata(&mut tx, s)?;
        let e = event(&mut tx, s, &self.owner, epoch, kind, data)?;
        tx.commit()?;
        self.leases.insert(s.id.clone(), epoch);
        Ok(e)
    }
    pub fn append(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let e = event(&mut tx, &s, &self.owner, epoch, kind, data)?;
        tx.commit()?;
        Ok(e)
    }
    pub fn cursor(&mut self, sid: &str) -> Result<i64> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let cursor = tx
            .query_one("SELECT cursor FROM sessions WHERE id=$1", &[&sid])?
            .get(0);
        tx.commit()?;
        Ok(cursor)
    }
    pub fn events(&mut self, sid: &str, after: i64) -> Result<Vec<Event>> {
        let epoch = self.epoch(sid)?;
        self.events_inner(sid, after, Some(epoch))
    }
    pub fn agent_run(&mut self, sid: &str, submission: Option<&str>) -> Result<Option<Value>> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let record = tx.query_opt(
            "SELECT data FROM events WHERE session_id=$1 AND kind='agent_run_state' AND ($2::text IS NULL OR data->>'submission_id'=$2) ORDER BY cursor DESC LIMIT 1",
            &[&sid, &submission],
        )?.map(|r| r.get(0));
        tx.commit()?;
        Ok(record)
    }
    pub fn events_for_api(&mut self, sid: &str, after: i64) -> Result<Vec<Event>> {
        self.events_inner(sid, after, None)
    }
    fn events_inner(&mut self, sid: &str, after: i64, epoch: Option<i64>) -> Result<Vec<Event>> {
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, epoch.map(|e| (self.owner.as_str(), e)))?;
        let cursor: i64 = tx
            .query_one("SELECT cursor FROM sessions WHERE id=$1", &[&sid])?
            .get(0);
        if after < 0 || after > cursor {
            return Err(crate::SubmissionError::InvalidRequest.into());
        }
        let mut events = vec![];
        let mut bytes = 0;
        for row in tx.query("SELECT cursor,turn_id,kind,data FROM events WHERE session_id=$1 AND cursor>$2 ORDER BY cursor LIMIT 500", &[&sid,&after])? {
            let data:Value=row.get(3);let size=data.to_string().len();
            if !events.is_empty() && bytes+size>512000 {break;}
            let id:i64=row.get(0);if id!=after+events.len() as i64+1 {bail!("Event cursor gap; stored data incomplete");}
            events.push(Event {event_id:id,session_id:sid.into(),turn_id:row.get(1),kind:row.get(2),data});bytes+=size;
        }
        if events.is_empty() && after < cursor {
            bail!("Event cursor gap; stored data incomplete");
        }
        tx.commit()?;
        Ok(events)
    }
    pub fn audit(&mut self, sid: &str, after: i64) -> Result<Vec<Value>> {
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, None)?;
        let cursor: i64 = tx
            .query_one("SELECT cursor FROM sessions WHERE id=$1", &[&sid])?
            .get(0);
        if after < 0 || after > cursor {
            bail!("Invalid audit cursor");
        }
        let rows=tx.query("SELECT cursor,turn_id,kind,owner_epoch,actor FROM audit WHERE session_id=$1 AND cursor>$2 ORDER BY cursor LIMIT 500", &[&sid,&after])?;
        for (index, row) in rows.iter().enumerate() {
            if row.get::<_, i64>(0) != after + index as i64 + 1 {
                bail!("Audit cursor gap; stored data incomplete");
            }
        }
        if rows.is_empty() && after < cursor {
            bail!("Audit cursor gap; stored data incomplete");
        }
        let result=rows.iter().map(|r|json!({"session_id":sid,"cursor":r.get::<_,i64>(0),"turn_id":r.get::<_,Option<String>>(1),"kind":r.get::<_,String>(2),"owner_epoch":r.get::<_,i64>(3),"actor":r.get::<_,Option<String>>(4)})).collect();
        tx.commit()?;
        Ok(result)
    }
    pub fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let tid = &s.turn.as_ref().context("No current turn")?.id;
        tx.execute(
            "INSERT INTO attempts(session_id,id,turn_id,status) VALUES($1,$2,$3,'running')",
            &[&sid, &id, tid],
        )?;
        let e = event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "model_attempt_started",
            json!({"attempt_id":id,"attempt":number}),
        )?;
        tx.commit()?;
        Ok(e)
    }
    pub fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        if tx.execute("UPDATE attempts SET truncated=truncated OR char_length(text||$3)>16000,text=left(text||$3,16000) WHERE session_id=$1 AND id=$2 AND status='running'", &[&sid,&id,&text])?!=1 {bail!("No running model attempt");}
        let e = event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "assistant_delta",
            json!({"attempt_id":id,"text":text}),
        )?;
        tx.commit()?;
        Ok(e)
    }
    pub fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<Failure>,
    ) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        if tx.execute(
            "UPDATE attempts SET status=$3 WHERE session_id=$1 AND id=$2",
            &[&sid, &id, &status],
        )? != 1
        {
            bail!("Model attempt not found");
        }
        let e = event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "model_attempt_finished",
            json!({"attempt_id":id,"status":status,"kind":kind}),
        )?;
        tx.commit()?;
        Ok(e)
    }
    pub fn interrupt_attempts(&mut self, sid: &str) -> Result<()> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let count = tx.execute(
            "UPDATE attempts SET status='interrupted' WHERE session_id=$1 AND status='running'",
            &[&sid],
        )?;
        if count > 0 {
            event(
                &mut tx,
                &s,
                &self.owner,
                epoch,
                "attempts_interrupted",
                json!({"count":count}),
            )?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn attempts(&mut self, sid: &str) -> Result<Vec<Value>> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let result=tx.query("SELECT id,turn_id,status,text,truncated FROM attempts WHERE session_id=$1 ORDER BY ordinal DESC LIMIT 3", &[&sid])?.iter().map(|r|json!({"id":r.get::<_,String>(0),"turn_id":r.get::<_,String>(1),"status":r.get::<_,String>(2),"text":r.get::<_,String>(3),"truncated":r.get::<_,bool>(4)})).collect();
        tx.commit()?;
        Ok(result)
    }
    pub fn recent(&mut self, sid: &str) -> Result<(Vec<Value>, bool)> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        verify_history_count(&mut tx, sid)?;
        let count:i64=tx.query_one("SELECT count(*) FROM history WHERE session_id=$1 AND (data->>'role'='user' OR data->>'type'='message')", &[&sid])?.get(0);
        let mut items = vec![];
        let mut bytes = 0;
        for row in tx.query("SELECT data FROM history WHERE session_id=$1 AND (data->>'role'='user' OR data->>'type'='message') ORDER BY seq DESC LIMIT 100", &[&sid])? {
            let v:Value=row.get(0);bytes+=v.to_string().len();if bytes>262144 {break;}items.push(v);
        }
        items.reverse();
        let truncated = (items.len() as i64) < count;
        tx.commit()?;
        Ok((items, truncated))
    }
    pub fn history_page(&mut self, sid: &str, after: usize) -> Result<Value> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        verify_history_count(&mut tx, sid)?;
        let total: i64 = tx
            .query_one("SELECT count(*) FROM history WHERE session_id=$1", &[&sid])?
            .get(0);
        if after > total as usize {
            bail!("Invalid history cursor");
        }
        let mut items = vec![];
        let mut bytes = 0;
        let mut cursor = after as i64;
        for row in tx.query(
            "SELECT seq,data FROM history WHERE session_id=$1 AND seq>=$2 ORDER BY seq LIMIT 100",
            &[&sid, &(after as i64)],
        )? {
            let seq: i64 = row.get(0);
            let v: Value = row.get(1);
            if seq != cursor {
                bail!("Canonical history gap; stored data incomplete");
            }
            if let Some(display) = display_item(&v) {
                let size = display.to_string().len();
                if !items.is_empty() && bytes + size > 512000 {
                    break;
                }
                items.push(json!({"index":seq,"item":display}));
                bytes += size;
            }
            cursor = seq + 1;
        }
        tx.commit()?;
        Ok(json!({"items":items,"cursor":cursor,"has_more":cursor<total,"total_items":total}))
    }
    pub fn has_submission(&mut self, sid: &str, key: &str) -> Result<bool> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        let result = tx
            .query_one(
                "SELECT EXISTS(SELECT 1 FROM submissions WHERE session_id=$1 AND id=$2 AND status<>'queued')",
                &[&sid, &key],
            )?
            .get(0);
        tx.commit()?;
        Ok(result)
    }
    pub fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let mut s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        crate::submissions::verify(&mut tx, sid)?;
        read_history(&mut tx, &mut s)?;
        let existing = tx.query_opt(
            "SELECT text,turn_id,status FROM submissions WHERE session_id=$1 AND id=$2",
            &[&sid, &key],
        )?;
        if let Some(row) = &existing {
            let old: String = row.get(0);
            let tid: String = row.get(1);
            if old != text {
                return Err(crate::SubmissionError::Conflict.into());
            }
            if row.get::<_, String>(2) != "queued" {
                if s.turn.as_ref().is_none_or(|t| t.id != tid) {
                    s.turn = Some(Turn {
                        id: tid,
                        submission_id: key.into(),
                        text: old,
                        status: row.get(2),
                        iterations: 0,
                        queue: vec![],
                        provider_attempts: 0,
                        compactions: 0,
                        continuations: 0,
                        reviewer_denials: 0,
                    });
                }
                tx.commit()?;
                return Ok((s, false, None));
            }
        }
        if s.closed {
            bail!("Session is closed");
        }
        if crate::controls::pending(&mut tx, sid)? {
            return Err(crate::SubmissionError::ControlsPending.into());
        }
        if s.provider_blocked {
            bail!("Provider safety block requires operator review");
        }
        if s.turn.as_ref().is_some_and(|t| !terminal(&t.status)) {
            bail!("Session busy; respond or cancel first");
        }
        // The row lock covers peek, promotion, history and receipt: no pop/dispatch gap.
        if let Some(first) = tx.query_opt("SELECT id FROM submissions WHERE session_id=$1 AND status='queued' ORDER BY sequence LIMIT 1", &[&sid])? {
            if first.get::<_, String>(0) != key {
                return Err(crate::SubmissionError::OutOfOrder.into());
            }
        }
        let turn_id = if let Some(row) = &existing {
            row.get::<_, String>(1)
        } else {
            crate::submissions::insert(
                &mut tx,
                &crate::TurnSubmission {
                    session_id: sid.into(),
                    submission_id: key.into(),
                    text: text.into(),
                },
                "started",
                Some(epoch),
            )?
            .turn_id
        };
        if let Some(t) = &s.turn {
            for c in &t.queue {
                s.history.push(json!({"type":"function_call_output","call_id":c["call_id"],"output":"Previous turn interrupted; action outcome unknown, do not assume success."}));
            }
        }
        let mut input = json!({"role":"user","content":text});
        if openworker_runtime::assistant_tools::admit(
            &mut s,
            key,
            text,
            openworker_runtime::assistant_tools::now(),
        )? {
            input["_runtime_notice"] = json!("scheduled_task");
        }
        if let Some(delivery) = crate::team_execution::notice(&mut tx, sid, key)? {
            input["_runtime_notice"] = json!("team_delivery");
            input["_team_delivery"] = delivery;
        }
        s.history.push(input);
        s.compaction_action = None;
        s.turn = Some(Turn {
            id: turn_id,
            submission_id: key.into(),
            text: text.into(),
            status: "accepted".into(),
            iterations: 0,
            queue: vec![],
            provider_attempts: 0,
            compactions: 0,
            continuations: 0,
            reviewer_denials: 0,
        });
        append_history(&mut tx, &s)?;
        write_metadata(&mut tx, &s)?;
        tx.execute(
            "UPDATE submissions SET status='started',owner_epoch=$3 WHERE session_id=$1 AND id=$2",
            &[&sid, &key, &epoch],
        )?;
        let e = event(&mut tx, &s, &self.owner, epoch, "turn_accepted", json!({}))?;
        tx.commit()?;
        s.storage_revision += 1;
        Ok((s, true, Some(e)))
    }
    pub fn recover(&mut self) -> Result<()> {
        // Only the explicit claims of this incarnation, never all shared sessions.
        let ids: Vec<_> = self.leases.keys().cloned().collect();
        for sid in ids {
            let epoch = self.epoch(&sid)?;
            let mut tx = self.client.transaction()?;
            let mut s = locked(&mut tx, &sid, Some((&self.owner, epoch)))?;
            read_history(&mut tx, &mut s)?;
            let interrupted = tx.execute(
                "UPDATE attempts SET status='interrupted' WHERE session_id=$1 AND status='running'",
                &[&sid],
            )?;
            let mut changed = false;
            let mut unknown = 0;
            if let Some(t) = &mut s.turn {
                if !terminal(&t.status) {
                    let pending = s
                        .interactions
                        .iter()
                        .any(|i| i.turn_id == t.id && i.response.is_none());
                    let next = if matches!(t.status.as_str(), "waiting" | "recoverable") && pending
                    {
                        "recoverable"
                    } else {
                        "interrupted"
                    };
                    changed = t.status != next;
                    t.status = next.into();
                    let mut remaining = vec![];
                    for call in &t.queue {
                        let has_result = s.history.iter().any(|v| {
                            v["type"] == "function_call_output" && v["call_id"] == call["call_id"]
                        });
                        if has_result {
                            continue;
                        }
                        let started:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM events WHERE session_id=$1 AND turn_id=$2 AND kind='tool_started' AND data->>'call_id'=$3)", &[&sid,&t.id,&call["call_id"].as_str().context("Invalid saved call ID")?])?.get(0);
                        if started {
                            let output = json!({"type":"tool_outcome_unknown","tool_call_id":call["call_id"],"tool":call["name"],"source":"harness_recovery","may_still_be_running":true,"message":"No durable result was recorded. The operation may have completed, failed, or still be running. Inspect the original environment before deciding how to continue. This is not permission to retry."});
                            s.history.push(json!({"type":"function_call_output","call_id":call["call_id"],"output":output.to_string()}));
                            unknown += 1;
                        } else {
                            remaining.push(call.clone());
                        }
                    }
                    t.queue = remaining;
                }
            }
            if changed || unknown > 0 || interrupted > 0 {
                append_history(&mut tx, &s)?;
                write_metadata(&mut tx, &s)?;
                let data = json!({"status":s.turn.as_ref().map(|t|t.status.as_str()).unwrap_or("idle"),"outcome_unknown_count":unknown,"automatic_replay":false});
                event(&mut tx, &s, &self.owner, epoch, "turn_recovered", data)?;
            }
            tx.commit()?;
            if changed || unknown > 0 || interrupted > 0 {
                self.telemetry.event(
                    openworker_runtime::telemetry::Operation::Recovery,
                    openworker_runtime::telemetry::Outcome::Interrupted,
                    Some(&sid),
                    s.turn.as_ref().map(|t| t.id.as_str()),
                );
                if unknown > 0 {
                    self.telemetry.event(
                        openworker_runtime::telemetry::Operation::UnknownOutcome,
                        openworker_runtime::telemetry::Outcome::Unknown,
                        Some(&sid),
                        s.turn.as_ref().map(|t| t.id.as_str()),
                    );
                }
            }
        }
        Ok(())
    }
}

fn display_item(v: &Value) -> Option<Value> {
    match v["type"].as_str() {
        Some("reasoning") => None,
        Some("function_call") => Some(
            json!({"type":"function_call","call_id":v["call_id"],"name":v["name"],"arguments":v["arguments"]}),
        ),
        Some("function_call_output") => {
            Some(json!({"type":"function_call_output","call_id":v["call_id"],"output":v["output"]}))
        }
        _ if v["role"] == "user" || v["type"] == "message" => Some(
            json!({"role":v["role"],"type":v["type"],"content":v["content"],"runtime_notice":v["_runtime_notice"]}),
        ),
        _ => None,
    }
}
pub(crate) fn excerpt(page: Value, offset: usize) -> Result<Value> {
    let Some(first) = page["items"].as_array().and_then(|a| a.first()) else {
        return Ok(
            json!({"next_index":page["cursor"],"next_offset":0,"has_more":page["has_more"],"text":""}),
        );
    };
    let raw = serde_json::to_string(&first["item"])?;
    let count = raw.chars().count();
    if offset > count {
        bail!("Invalid transcript character offset");
    }
    let text = raw.chars().skip(offset).take(8000).collect::<String>();
    let next = offset + text.chars().count();
    let index = first["index"].as_u64().unwrap();
    Ok(
        json!({"index":index,"offset":offset,"text":text,"item_complete":next==count,"next_index":if next==count{index+1}else{index},"next_offset":if next==count{0}else{next},"has_more":next<count||index+1<page["total_items"].as_u64().unwrap()}),
    )
}

#[cfg(test)]
mod ca_tests {
    use super::*;
    // Public certificate only. Parsing tests don't depend on its expiry date.
    const CA: &str = include_str!("../tests/fixtures/public-ca.pem");
    #[test]
    fn reads_every_root_in_a_bundle() {
        assert_eq!(ca_certificates(CA.as_bytes()).unwrap().len(), 1);
        assert_eq!(
            ca_certificates(format!("{CA}\n{CA}").as_bytes())
                .unwrap()
                .len(),
            2
        );
    }
    #[test]
    fn rejects_empty_malformed_noncertificate_and_oversized_bundles() {
        for bad in [
            "",
            "not a certificate",
            "-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----",
            "-----BEGIN PRIVATE KEY-----\nAA==\n-----END PRIVATE KEY-----",
        ] {
            assert!(ca_certificates(bad.as_bytes()).is_err());
        }
        assert!(ca_certificates(&vec![b' '; 262_145]).is_err());
        assert!(ca_certificates(CA.repeat(33).as_bytes()).is_err());
        assert!(ca_certificates(
            format!("{CA}\n-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----").as_bytes()
        )
        .is_err());
    }
}
