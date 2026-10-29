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
            if old != value {
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
