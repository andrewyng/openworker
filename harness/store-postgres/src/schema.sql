CREATE TABLE IF NOT EXISTS version (singleton BOOLEAN PRIMARY KEY CHECK(singleton), version INTEGER NOT NULL);
CREATE TABLE environments (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, data JSONB NOT NULL,
 revision BIGINT NOT NULL DEFAULT 1, delete_request_id TEXT, delete_actor TEXT
);
CREATE TABLE environment_events (
 environment_id TEXT NOT NULL REFERENCES environments(id), revision BIGINT NOT NULL,
 kind TEXT NOT NULL, data JSONB NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,revision)
);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, data JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 0,
 cursor BIGINT NOT NULL DEFAULT 0, history_count BIGINT NOT NULL DEFAULT 0,
 command_sequence BIGINT NOT NULL DEFAULT 0,
 control_count BIGINT NOT NULL DEFAULT 0,
 environment_id TEXT REFERENCES environments(id),
 creation_request_id TEXT UNIQUE,
 owner TEXT NOT NULL, epoch BIGINT NOT NULL,
 expires TIMESTAMPTZ NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE creations (
 request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, parameters JSONB NOT NULL,
 session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id)
);
CREATE INDEX sessions_schedule ON sessions(expires,created);
CREATE TABLE IF NOT EXISTS history (
 session_id TEXT NOT NULL REFERENCES sessions(id), seq BIGINT NOT NULL, data JSONB NOT NULL,
 PRIMARY KEY(session_id,seq)
);
CREATE TABLE IF NOT EXISTS events (
 session_id TEXT NOT NULL REFERENCES sessions(id), cursor BIGINT NOT NULL, turn_id TEXT,
 kind TEXT NOT NULL, data JSONB NOT NULL, PRIMARY KEY(session_id,cursor)
);
CREATE TABLE IF NOT EXISTS audit (
 session_id TEXT NOT NULL REFERENCES sessions(id), cursor BIGINT NOT NULL, turn_id TEXT,
 kind TEXT NOT NULL, owner_epoch BIGINT NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 actor TEXT,
 PRIMARY KEY(session_id,cursor), FOREIGN KEY(session_id,cursor) REFERENCES events(session_id,cursor)
);
CREATE TABLE IF NOT EXISTS attempts (
 session_id TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL, turn_id TEXT NOT NULL,
 status TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', truncated BOOLEAN NOT NULL DEFAULT FALSE,
 ordinal BIGINT GENERATED ALWAYS AS IDENTITY, PRIMARY KEY(session_id,id)
);
CREATE TABLE IF NOT EXISTS submissions (
 session_id TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL, text TEXT NOT NULL, turn_id TEXT NOT NULL,
 sequence BIGINT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','started','completed','failed','cancelled','interrupted','truncated')),
 owner_epoch BIGINT, created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(session_id,id), UNIQUE(session_id,sequence), UNIQUE(session_id,turn_id)
);
CREATE INDEX submissions_pending ON submissions(session_id,sequence) WHERE status='queued';
CREATE TABLE controls (
 session_id TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL, turn_id TEXT NOT NULL,
 target TEXT NOT NULL, command JSONB NOT NULL, sequence BIGINT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected')), reason TEXT,
 PRIMARY KEY(session_id,id), UNIQUE(session_id,target), UNIQUE(session_id,sequence)
);
CREATE INDEX controls_pending ON controls(session_id,sequence) WHERE status='pending';
CREATE TABLE attachments (
 id TEXT PRIMARY KEY, environment_id TEXT NOT NULL REFERENCES environments(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), server TEXT NOT NULL,
 owner_epoch BIGINT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','active','released')),
 outcome_unknown BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX attachments_live ON attachments(session_id,server,owner_epoch) WHERE state<>'released';
