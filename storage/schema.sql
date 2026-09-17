BEGIN;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS hosts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    ssh_target TEXT NOT NULL,
    vpn_profile_ref TEXT,
    credential_ref TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    original_name TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE CHECK (length(sha256) = 64),
    relative_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    received_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    host_id TEXT NOT NULL REFERENCES hosts(id),
    artifact_id TEXT REFERENCES artifacts(id),
    kind TEXT NOT NULL CHECK (kind IN ('deploy', 'rollback', 'inspection')),
    status TEXT NOT NULL CHECK (status IN (
        'draft', 'prechecking', 'awaiting_approval', 'running',
        'paused', 'failed', 'verifying', 'completed', 'cancelled'
    )),
    plan_sha256 TEXT CHECK (plan_sha256 IS NULL OR length(plan_sha256) = 64),
    pi_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    host_id TEXT NOT NULL REFERENCES hosts(id),
    plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256) = 64),
    action_sha256 TEXT NOT NULL CHECK (length(action_sha256) = 64),
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    revoked_at TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    redacted INTEGER NOT NULL CHECK (redacted IN (0, 1)),
    collected_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL REFERENCES hosts(id),
    task_id TEXT REFERENCES tasks(id),
    evidence_id TEXT REFERENCES evidence(id),
    kind TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    observed_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    step_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    occurred_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_tasks_project_created ON tasks(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_host_status ON tasks(host_id, status);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON task_events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_observations_host_time ON observations(host_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id, granted_at);

CREATE TRIGGER IF NOT EXISTS task_events_no_update
BEFORE UPDATE ON task_events BEGIN
    SELECT RAISE(ABORT, 'task events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS task_events_no_delete
BEFORE DELETE ON task_events BEGIN
    SELECT RAISE(ABORT, 'task events are append-only');
END;

PRAGMA user_version = 1;
COMMIT;
