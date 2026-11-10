//! App-owned agent definitions. No callbacks or credentials are serialized here.
use crate::agents::Agent;
use anyhow::{ensure, Context, Result};
use rusqlite::{Connection, OptionalExtension};
use std::{path::Path, sync::Mutex};

pub(super) struct Catalog(Mutex<Connection>);
impl Catalog {
    pub fn open(root: &Path) -> Result<Self> {
        let path = root.join("sdk-agents.sqlite");
        if let Ok(meta) = path.symlink_metadata() {
            ensure!(
                !openworker_runtime::platform::linked(&meta),
                "Agent catalog cannot be a link"
            );
        }
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, definition TEXT NOT NULL);")?;
        db.execute_batch("CREATE TABLE IF NOT EXISTS application_tools(name TEXT PRIMARY KEY,descriptor TEXT NOT NULL)")?;
        db.execute_batch("CREATE TABLE IF NOT EXISTS mcp_servers(name TEXT PRIMARY KEY,definition TEXT NOT NULL)")?;
        Ok(Self(Mutex::new(db)))
    }
    pub fn load(&self) -> Result<Vec<Agent>> {
        let db = self.0.lock().unwrap();
        let mut stmt = db.prepare("SELECT definition FROM agents ORDER BY id")?;
        let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
        let mut agents = Vec::new();
        for row in rows {
            let raw = row?;
            ensure!(
                raw.len() <= 4 * 1024 * 1024,
                "Stored agent definition exceeds limit"
            );
            agents.push(serde_json::from_str(&raw).context("Invalid stored agent definition")?);
            ensure!(agents.len() <= 16, "Agent catalog exceeds capacity");
        }
        Ok(agents)
    }
    pub fn servers(&self) -> Result<Vec<crate::McpServer>> {
        let db = self.0.lock().unwrap();
        let mut q = db.prepare("SELECT definition FROM mcp_servers ORDER BY name")?;
        let mut servers = vec![];
        for row in q.query_map([], |r| r.get::<_, String>(0))? {
            let raw = row?;
            ensure!(
                raw.len() <= 64000 && servers.len() < 16,
                "MCP catalog exceeds limits"
            );
            servers.push(serde_json::from_str(&raw)?);
        }
        Ok(servers)
    }
    pub fn insert_server(&self, server: &crate::McpServer) -> Result<()> {
        let db = self.0.lock().unwrap();
        db.execute(
            "INSERT INTO mcp_servers(name,definition) VALUES(?1,?2)",
            [server.name(), &serde_json::to_string(server)?],
        )?;
        Ok(())
    }
    pub fn tools(&self) -> Result<Vec<openworker_tools::ToolDescriptor>> {
        let db = self.0.lock().unwrap();
        let mut q = db.prepare("SELECT descriptor FROM application_tools ORDER BY name")?;
        let mut tools = vec![];
        for row in q.query_map([], |r| r.get::<_, String>(0))? {
            let value = row?;
            ensure!(
                value.len() <= 64000 && tools.len() < 128,
                "Application tool catalog exceeds bounds"
            );
            tools.push(serde_json::from_str(&value)?);
        }
        Ok(tools)
    }
    pub fn insert_tools(&self, tools: &[openworker_tools::ToolDescriptor]) -> Result<()> {
        let mut db = self.0.lock().unwrap();
        let tx = db.transaction()?;
        for descriptor in tools {
            let raw = serde_json::to_string(descriptor)?;
            let previous: Option<String> = tx
                .query_row(
                    "SELECT descriptor FROM application_tools WHERE name=?1",
                    [&descriptor.qualified_name],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(previous) = previous {
                ensure!(
                    serde_json::from_str::<openworker_tools::ToolDescriptor>(&previous)?
                        == *descriptor,
                    "Tool definition changed; use a new qualified name"
                );
            } else {
                tx.execute(
                    "INSERT INTO application_tools(name,descriptor) VALUES(?1,?2)",
                    [&descriptor.qualified_name, &raw],
                )?;
            }
        }
        let count: i64 =
            tx.query_row("SELECT COUNT(*) FROM application_tools", [], |r| r.get(0))?;
        ensure!(count <= 128, "Application tool capacity exceeded");
        tx.commit()?;
        Ok(())
    }
    pub fn insert(&self, agent: &Agent) -> Result<()> {
        let mut db = self.0.lock().unwrap();
        let tx = db.transaction()?;
        let encoded = serde_json::to_string(agent)?;
        let previous: Option<String> = tx
            .query_row(
                "SELECT definition FROM agents WHERE id=?1",
                [&agent.id],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(previous) = previous {
            ensure!(
                previous == encoded,
                "Agent identity already has a different definition"
            );
        } else {
            tx.execute(
                "INSERT INTO agents(id,definition) VALUES(?1,?2)",
                [&agent.id, &encoded],
            )?;
        }
        tx.commit()?;
        Ok(())
    }
}

pub(super) fn merge(startup: &[Agent], saved: Vec<Agent>) -> Result<Vec<Agent>> {
    let mut result = startup.to_vec();
    for agent in saved {
        if let Some(existing) = result.iter().find(|a| a.id == agent.id) {
            ensure!(
                serde_json::to_value(existing)? == serde_json::to_value(&agent)?,
                "Startup agent conflicts with persisted registration: {}",
                agent.id
            );
        } else {
            result.push(agent);
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn agent() -> Agent {
        Agent::new("builder")
            .instructions("Build.")
            .tool("game.preview")
    }
    #[test]
    fn durable_idempotent_and_conflicts_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let c = Catalog::open(dir.path()).unwrap();
        c.insert(&agent()).unwrap();
        c.insert(&agent()).unwrap();
        assert!(c.insert(&agent().instructions("Different")).is_err());
        drop(c);
        let c = Catalog::open(dir.path()).unwrap();
        assert_eq!(c.load().unwrap().len(), 1);
        assert_eq!(merge(&[agent()], c.load().unwrap()).unwrap().len(), 1);
        assert!(merge(&[agent().instructions("Different")], c.load().unwrap()).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn catalog_symlink_rejected() {
        let dir = tempfile::tempdir().unwrap();
        std::os::unix::fs::symlink(
            dir.path().join("outside"),
            dir.path().join("sdk-agents.sqlite"),
        )
        .unwrap();
        assert!(Catalog::open(dir.path()).is_err());
    }
}
