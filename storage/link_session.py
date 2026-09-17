"""Associate a Pi session with an existing task without exposing connection data."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DEPLOY_AGENT_DATA_DIR", ROOT / "deploy-agent-data")).resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    with closing(sqlite3.connect(DATA_DIR / "state.sqlite")) as db:
        with db:
            result = db.execute(
                "UPDATE tasks SET pi_session_id = ?, updated_at = ? WHERE id = ?",
                (args.session, datetime.now(timezone.utc).isoformat(timespec="seconds"), args.task),
            )
            if result.rowcount != 1:
                raise RuntimeError("Task does not exist")


if __name__ == "__main__":
    main()
