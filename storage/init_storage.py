"""Create the local task store without touching the Pi source checkout."""

import os
from pathlib import Path
import sqlite3
from contextlib import closing


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DEPLOY_AGENT_DATA_DIR", ROOT / "deploy-agent-data")).resolve()
SCHEMA = Path(__file__).with_name("schema.sql")
SUBDIRS = ("packages", "evidence", "reports", "backups", "pi-sessions")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRS:
        (DATA_DIR / name).mkdir(exist_ok=True)

    database = DATA_DIR / "state.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        elif version != 1:
            raise RuntimeError(f"Unsupported task-store schema version: {version}")
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")

    print(f"Task store ready: {database}")


if __name__ == "__main__":
    main()
