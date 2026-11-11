//! Independent PostgreSQL configuration/connection. Never borrows an execution
//! transaction and never falls back to another database after a failure.
use crate::{Conflict, Step, Store, StoredStep, Write};
use ::postgres::Client;
use anyhow::{ensure, Context, Result};
use std::{path::PathBuf, str::FromStr, time::Duration};
use zeroize::Zeroizing;

#[derive(Clone)]
pub enum Tls {
    Verified { ca_file: Option<PathBuf> },
    LoopbackPlaintext,
}
#[derive(Clone)]
pub struct Config {
    // No Debug implementation: connection strings contain credentials.
    pub connection: Zeroizing<String>,
    pub schema: String,
    pub tls: Tls,
    pub timeout: Duration,
}
impl Config {
    pub fn new(connection: String, schema: String) -> Self {
        Self {
            connection: Zeroizing::new(connection),
            schema,
            tls: Tls::Verified { ca_file: None },
            timeout: Duration::from_secs(1),
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.schema.is_empty()
                && self.schema.len() <= 48
                && self.schema.starts_with("ow_")
                && self
                    .schema
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
            "Invalid observation PostgreSQL schema"
        );
        ensure!(
            (Duration::from_millis(100)..=Duration::from_secs(5)).contains(&self.timeout),
            "Observation database timeout must be 100..=5000 ms"
        );
        Ok(())
    }
    fn connect(&self) -> Result<Client> {
        self.validate()?;
        let mut c = ::postgres::Config::from_str(&self.connection)
            .map_err(|_| anyhow::anyhow!("Invalid observation database configuration"))?;
        c.connect_timeout(self.timeout);
        let mut client = match &self.tls {
            Tls::LoopbackPlaintext => {
                ensure!(
                    c.get_hosts().len() == 1
                        && matches!(&c.get_hosts()[0],::postgres::config::Host::Tcp(h) if matches!(h.as_str(),"127.0.0.1"|"localhost"|"::1"))
                        && c.get_hostaddrs().is_empty(),
                    "Plaintext observation database requires one loopback host without hostaddr"
                );
                c.ssl_mode(::postgres::config::SslMode::Disable);
                c.connect(::postgres::NoTls)
                    .map_err(|_| anyhow::anyhow!("Observation database connection failed"))?
            }
            Tls::Verified { ca_file } => {
                c.ssl_mode(::postgres::config::SslMode::Require);
                let mut tls = native_tls::TlsConnector::builder();
                if let Some(path) = ca_file {
                    use std::io::Read;
                    let mut bytes = Vec::new();
                    std::fs::File::open(path)
                        .context("Cannot open observation database CA")?
                        .take(262145)
                        .read_to_end(&mut bytes)?;
                    ensure!(bytes.len() <= 262144, "Observation database CA too large");
                    let mut count = 0;
                    for item in rustls_pemfile::read_all(&mut std::io::Cursor::new(bytes)) {
                        let rustls_pemfile::Item::X509Certificate(der) =
                            item.context("Invalid observation database CA")?
                        else {
                            anyhow::bail!("Observation database CA must contain certificates only");
                        };
                        count += 1;
                        ensure!(count <= 32, "Too many observation database roots");
                        tls.add_root_certificate(native_tls::Certificate::from_der(der.as_ref())?);
                    }
                    ensure!(count > 0, "Observation database CA is empty");
                }
                c.connect(postgres_native_tls::MakeTlsConnector::new(tls.build()?))
                    .map_err(|_| anyhow::anyhow!("Observation database TLS connection failed"))?
            }
        };
        let ms = (self.timeout.as_millis() * 3 / 4).max(1);
        client.batch_execute(&format!("SET statement_timeout={ms}; SET lock_timeout={ms}; SET idle_in_transaction_session_timeout={ms}; SET search_path={},pg_catalog",self.schema)).context("Observation database setup failed")?;
        Ok(client)
    }
}
pub struct Postgres {
    client: Client,
}
impl Postgres {
    /// Explicit deployment operation only. Serializes initialization, validates
    /// version and refuses adoption of an existing unrelated schema.
    pub fn migrate(config: &Config) -> Result<()> {
        let mut c = config.connect()?;
        let mut tx = c.transaction()?;
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            &[&format!("observability:{}", config.schema)],
        )?;
        tx.batch_execute(&format!("CREATE SCHEMA IF NOT EXISTS {}", config.schema))?;
        let exists: bool = tx
            .query_one("SELECT to_regclass('observation_version') IS NOT NULL", &[])?
            .get(0);
        if exists {
            let rows = tx.query(
                "SELECT version FROM observation_version WHERE singleton",
                &[],
            )?;
            ensure!(
                rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
                "Unsupported PostgreSQL observation schema"
            );
        } else {
            let n: i64 = tx
                .query_one(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema=$1",
                    &[&config.schema],
                )?
                .get(0);
            ensure!(n == 0, "Refusing nonempty unversioned observation schema");
            tx.batch_execute("CREATE TABLE observation_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton),version INTEGER NOT NULL);
              CREATE TABLE observation_steps(cursor BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,deployment TEXT NOT NULL,span_id TEXT NOT NULL,session_hash TEXT,ended_ms BIGINT NOT NULL,data JSONB NOT NULL,UNIQUE(deployment,span_id));
              CREATE INDEX observation_session ON observation_steps(deployment,session_hash,cursor);
              CREATE INDEX observation_expiry ON observation_steps(deployment,ended_ms,cursor);
              INSERT INTO observation_version VALUES(TRUE,1);")?;
        }
        tx.batch_execute("CREATE INDEX IF NOT EXISTS observation_turn ON observation_steps(deployment,session_hash,(data->>'turn_hash'),cursor)")?;
        #[cfg(feature="evaluations")]
        tx.batch_execute("CREATE TABLE IF NOT EXISTS observation_evaluation_delivery(deployment TEXT NOT NULL,sink TEXT NOT NULL,data JSONB NOT NULL,PRIMARY KEY(deployment,sink)); CREATE TABLE IF NOT EXISTS observation_evaluations(cursor BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,deployment TEXT NOT NULL,id TEXT NOT NULL,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,data JSONB NOT NULL,recorded_ms BIGINT NOT NULL DEFAULT ((extract(epoch FROM clock_timestamp())*1000)::bigint),UNIQUE(deployment,id)); CREATE INDEX IF NOT EXISTS observation_evaluation_scope ON observation_evaluations(deployment,session_hash,turn_hash,cursor)")?;
        tx.commit()?;
        Ok(())
    }
    pub fn open(config: &Config) -> Result<Self> {
        let mut client = config.connect()?;
        let rows = client
            .query(
                "SELECT version FROM observation_version WHERE singleton",
                &[],
            )
            .context("Observation schema unavailable; run explicit migration")?;
        ensure!(
            rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
            "Unsupported PostgreSQL observation schema"
        );
        client.query("SELECT cursor,deployment,span_id,session_hash,ended_ms,data FROM observation_steps LIMIT 0",&[]).context("Observation tables unavailable; restore the configured schema")?;
        Ok(Self { client })
    }
}
impl Store for Postgres {
    #[cfg(feature = "evaluations")]
    fn register_evaluation_sink(
        &mut self,
        deployment: &str,
        sink: &str,
        binding: &str,
    ) -> Result<()> {
        crate::evaluations::delivery_key(deployment, sink, binding, "registration")?;
        let mut tx = self.client.transaction()?;
        tx.execute(
            "INSERT INTO observation_evaluation_delivery VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            &[
                &deployment,
                &sink,
                &serde_json::to_value(openworker_compliance::delivery::Checkpoint::new(
                    binding.into(),
                ))?,
            ],
        )?;
        let row=tx.query_one("SELECT data FROM observation_evaluation_delivery WHERE deployment=$1 AND sink=$2 FOR UPDATE",&[&deployment,&sink])?;
        let c: openworker_compliance::delivery::Checkpoint = serde_json::from_value(row.get(0))?;
        ensure!(
            c.binding == binding,
            "Evaluation destination changed; choose a new sink name"
        );
        tx.commit()?;
        Ok(())
    }

    #[cfg(feature = "evaluations")]
    fn prune_evaluations(&mut self, deployment: &str, before_ms: u64, limit: u16) -> Result<u64> {
        crate::query(deployment, None, 0, limit)?;
        ensure!(before_ms <= i64::MAX as u64, "Invalid cutoff");
        Ok(self.client.execute("DELETE FROM observation_evaluations WHERE cursor IN(SELECT e.cursor FROM observation_evaluations e WHERE e.deployment=$1 AND e.recorded_ms<$2 AND NOT EXISTS(SELECT 1 FROM observation_evaluation_delivery d WHERE d.deployment=e.deployment AND (d.data->>'cursor')::bigint<e.cursor) ORDER BY e.cursor LIMIT $3)",&[&deployment,&(before_ms as i64),&i64::from(limit)])?)
    }

    #[cfg(feature = "evaluations")]
    fn claim_evaluation_export(
        &mut self,
        deployment: &str,
        sink: &str,
        binding: &str,
        token: &str,
        now: u64,
    ) -> Result<Option<crate::evaluations::Delivery>> {
        use openworker_compliance::delivery::Checkpoint;
        crate::evaluations::delivery_key(deployment, sink, binding, token)?;
        let mut tx = self.client.transaction()?;
        tx.execute(
            "INSERT INTO observation_evaluation_delivery VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            &[
                &deployment,
                &sink,
                &serde_json::to_value(Checkpoint::new(binding.into()))?,
            ],
        )?;
        let raw=tx.query_one("SELECT data FROM observation_evaluation_delivery WHERE deployment=$1 AND sink=$2 FOR UPDATE",&[&deployment,&sink])?;
        let mut checkpoint: Checkpoint = serde_json::from_value(raw.get(0))?;
        ensure!(
            checkpoint.binding == binding,
            "Export destination changed; choose a new sink name"
        );
        let row=tx.query_opt("SELECT cursor,data FROM observation_evaluations WHERE deployment=$1 AND cursor>$2 ORDER BY cursor LIMIT 1",&[&deployment,&checkpoint.cursor])?;
        let Some(row) = row else {
            tx.commit()?;
            return Ok(None);
        };
        let cursor: i64 = row.get(0);
        let claimed = checkpoint.claim(binding, token, now, cursor)?;
        let evaluation: crate::evaluations::Evaluation = serde_json::from_value(row.get(1))?;
        evaluation.validate()?;
        ensure!(
            evaluation.deployment == deployment,
            "Evaluation export scope mismatch"
        );
        tx.execute(
            "UPDATE observation_evaluation_delivery SET data=$1 WHERE deployment=$2 AND sink=$3",
            &[&serde_json::to_value(checkpoint)?, &deployment, &sink],
        )?;
        tx.commit()?;
        Ok(claimed.then(|| crate::evaluations::Delivery {
            token: token.into(),
            row: crate::evaluations::Row {
                cursor: cursor as u64,
                evaluation,
            },
        }))
    }
    #[cfg(feature = "evaluations")]
    fn settle_evaluation_export(
        &mut self,
        deployment: &str,
        sink: &str,
        token: &str,
        now: u64,
        success: bool,
    ) -> Result<()> {
        let mut tx = self.client.transaction()?;
        let raw=tx.query_one("SELECT data FROM observation_evaluation_delivery WHERE deployment=$1 AND sink=$2 FOR UPDATE",&[&deployment,&sink])?;
        let mut c: openworker_compliance::delivery::Checkpoint =
            serde_json::from_value(raw.get(0))?;
        c.settle(token, now, success)?;
        tx.execute(
            "UPDATE observation_evaluation_delivery SET data=$1 WHERE deployment=$2 AND sink=$3",
            &[&serde_json::to_value(c)?, &deployment, &sink],
        )?;
        tx.commit()?;
        Ok(())
    }

    #[cfg(feature = "evaluations")]
    fn evaluation(
        &mut self,
        deployment: &str,
        id: &str,
    ) -> Result<Option<crate::evaluations::Evaluation>> {
        ensure!(
            crate::identifier(deployment) && crate::label(id),
            "Invalid evaluation identity"
        );
        self.client
            .query_opt(
                "SELECT data FROM observation_evaluations WHERE deployment=$1 AND id=$2",
                &[&deployment, &id],
            )?
            .map(|r| {
                let e: crate::evaluations::Evaluation = serde_json::from_value(r.get(0))?;
                e.validate()?;
                ensure!(
                    e.deployment == deployment && e.result.id == id,
                    "Evaluation identity mismatch"
                );
                Ok(e)
            })
            .transpose()
    }
    #[cfg(feature = "evaluations")]
    fn record_evaluation_bounded(
        &mut self,
        value: &crate::evaluations::Evaluation,
        max_pending: u32,
    ) -> Result<Write> {
        ensure!(
            (1..=1_000_000).contains(&max_pending),
            "Invalid evaluation export backlog limit"
        );
        value.validate()?;
        let value = value.clone().redacted();
        value.validate()?;
        let data = serde_json::to_value(&value)?;
        if let Some(old) = self.evaluation(&value.deployment, &value.result.id)? {
            ensure!(old == value, "Evaluation identity conflict");
            return Ok(Write::Duplicate);
        }
        let mut tx = self.client.transaction()?;
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            &[&format!("eval-capacity:{}", value.deployment)],
        )?;
        let retained: i64 = tx
            .query_one(
                "SELECT count(*) FROM observation_evaluations e WHERE deployment=$1 AND EXISTS(SELECT 1 FROM observation_evaluation_delivery d WHERE d.deployment=e.deployment AND (d.data->>'cursor')::bigint<e.cursor)",
                &[&value.deployment],
            )?
            .get(0);
        let exists:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM observation_evaluations WHERE deployment=$1 AND id=$2)",&[&value.deployment,&value.result.id])?.get(0);
        ensure!(
            exists || retained < i64::from(max_pending),
            "Pending evaluation export limit reached; restore delivery or raise the configured limit"
        );
        if let Some(id) = &value.result.supersedes {
            let prior = tx
                .query_opt(
                    "SELECT data FROM observation_evaluations WHERE deployment=$1 AND id=$2",
                    &[&value.deployment, id],
                )?
                .context("Superseded evaluation missing")?;
            let prior: crate::evaluations::Evaluation = serde_json::from_value(prior.get(0))?;
            ensure!(
                prior.target == value.target,
                "Correction must retain its target"
            );
        }
        let inserted=tx.execute("INSERT INTO observation_evaluations(deployment,id,session_hash,turn_hash,data) VALUES($1,$2,$3,$4,$5) ON CONFLICT(deployment,id) DO NOTHING",&[&value.deployment,&value.result.id,&value.target.session_hash,&value.target.turn_hash,&data])?;
        if inserted == 0 {
            let old = tx.query_one(
                "SELECT data FROM observation_evaluations WHERE deployment=$1 AND id=$2",
                &[&value.deployment, &value.result.id],
            )?;
            ensure!(
                serde_json::from_value::<crate::evaluations::Evaluation>(old.get(0))? == value,
                "Evaluation identity conflict"
            );
        }
        tx.commit()?;
        Ok(if inserted == 0 {
            Write::Duplicate
        } else {
            Write::Inserted
        })
    }
    #[cfg(feature = "evaluations")]
    fn evaluations(&mut self, q: &crate::evaluations::Query) -> Result<crate::evaluations::Page> {
        q.validate()?;
        let rows=self.client.query("SELECT cursor,data FROM observation_evaluations WHERE deployment=$1 AND session_hash=$2 AND turn_hash=$3 AND cursor>$4 ORDER BY cursor LIMIT $5",&[&q.deployment,&q.session_hash,&q.turn_hash,&(q.after as i64),&(i64::from(q.limit)+1)])?;
        let rows = rows
            .into_iter()
            .map(|r| {
                Ok(crate::evaluations::Row {
                    cursor: r.get::<_, i64>(0) as u64,
                    evaluation: serde_json::from_value(r.get(1))?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        crate::evaluations::page(rows, q)
    }

    fn timeline(&mut self, q: &crate::query::Query) -> Result<crate::query::Page> {
        q.validate()?;
        let sessions: Vec<_> = q.scopes.iter().map(|s| s.session_hash.as_str()).collect();
        let turns: Vec<_> = q.scopes.iter().map(|s| s.turn_hash.as_str()).collect();
        let mut tx = self
            .client
            .build_transaction()
            .isolation_level(::postgres::IsolationLevel::RepeatableRead)
            .read_only(true)
            .start()?;
        let max:i64=tx.query_one("SELECT COALESCE(max(cursor),0) FROM observation_steps WHERE deployment=$1 AND (session_hash,data->>'turn_hash') IN(SELECT * FROM unnest($2::text[],$3::text[]))",&[&q.deployment,&sessions,&turns])?.get(0);
        let through = q.through.unwrap_or(max as u64);
        let rows=tx.query("SELECT cursor,data FROM observation_steps WHERE deployment=$1 AND (session_hash,data->>'turn_hash') IN(SELECT * FROM unnest($2::text[],$3::text[])) AND cursor>$4 AND cursor<=$5 ORDER BY cursor LIMIT $6",&[&q.deployment,&sessions,&turns,&(q.after as i64),&(through as i64),&(i64::from(q.limit)+1)])?.into_iter().map(|r|Ok((r.get(0),serde_json::from_value(r.get(1))?))).collect::<Result<Vec<_>>>()?;
        tx.commit()?;
        crate::query::page(rows, through, q)
    }
    fn put(&mut self, step: &Step) -> Result<Write> {
        step.validate()?;
        let value = serde_json::to_value(step)?;
        let mut tx = self.client.transaction()?;
        let inserted=tx.execute("INSERT INTO observation_steps(deployment,span_id,session_hash,ended_ms,data) VALUES($1,$2,$3,$4,$5) ON CONFLICT(deployment,span_id) DO NOTHING",&[&step.deployment,&step.span_id,&step.session_hash,&(step.ended_ms as i64),&value])?;
        if inserted == 0 {
            let old: serde_json::Value = tx
                .query_one(
                    "SELECT data FROM observation_steps WHERE deployment=$1 AND span_id=$2",
                    &[&step.deployment, &step.span_id],
                )?
                .get(0);
            if serde_json::from_value::<Step>(old)? != *step {
                return Err(Conflict.into());
            }
        }
        tx.commit()?;
        Ok(if inserted == 1 {
            Write::Inserted
        } else {
            Write::Duplicate
        })
    }
    fn get(&mut self, deployment: &str, span_id: &str) -> Result<Option<Step>> {
        crate::query(deployment, None, 0, 1)?;
        ensure!(crate::hex_id(span_id, 16), "Invalid observation identity");
        self.client
            .query_opt(
                "SELECT data FROM observation_steps WHERE deployment=$1 AND span_id=$2",
                &[&deployment, &span_id],
            )?
            .map(|r| serde_json::from_value(r.get(0)).map_err(Into::into))
            .transpose()
    }
    fn list(
        &mut self,
        deployment: &str,
        session: Option<&str>,
        after: u64,
        limit: u16,
    ) -> Result<Vec<StoredStep>> {
        crate::query(deployment, session, after, limit)?;
        self.client.query("SELECT cursor,data FROM observation_steps WHERE deployment=$1 AND ($2::text IS NULL OR session_hash=$2) AND cursor>$3 ORDER BY cursor LIMIT $4",&[&deployment,&session,&(after as i64),&i64::from(limit)])?.into_iter().map(|r|Ok(StoredStep{cursor:r.get::<_,i64>(0) as u64,step:serde_json::from_value(r.get(1))?})).collect()
    }
    fn prune(&mut self, deployment: &str, cutoff: u64, limit: u16) -> Result<u64> {
        crate::query(deployment, None, 0, limit)?;
        ensure!(
            cutoff <= i64::MAX as u64,
            "Invalid observation retention bound"
        );
        Ok(self.client.execute("DELETE FROM observation_steps WHERE cursor IN (SELECT cursor FROM observation_steps WHERE deployment=$1 AND ended_ms<$2 ORDER BY ended_ms,cursor LIMIT $3)",&[&deployment,&(cutoff as i64),&i64::from(limit)])?)
    }
}
