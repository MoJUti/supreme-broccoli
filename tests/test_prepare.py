import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing

from src import prepare


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = PROJECT_ROOT / "storage" / "schema.sql"


class PrepareTests(unittest.TestCase):
    def test_register_keeps_connection_passwords_out_of_task_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(PROJECT_ROOT.resolve()))
            data = root / "data"
            for folder in ("packages", "evidence", "reports", "backups"):
                (data / folder).mkdir(parents=True, exist_ok=True)
            database = data / "state.sqlite"
            with closing(sqlite3.connect(database)) as db:
                db.executescript(SCHEMA.read_text(encoding="utf-8"))

            package = root / "sample.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("compose.yaml", "services:\n  web:\n    image: example:1\n    ports:\n      - '3300:3000'\n    environment:\n      TOKEN: fake-package-secret\n")
            (root / "school.ovpn").write_text("client\n", encoding="utf-8")
            connection_file = root / "connection.json"
            connection_file.write_text(json.dumps({
                "project": "sample",
                "server": "192.0.2.10",
                "ssh_user": "deploy",
                "vpn_type": "openvpn",
                "vpn_password": "FAKE-VPN-SECRET-123",
                "ssh_password": "FAKE-SSH-SECRET-456",
            }), encoding="utf-8")

            old_data, old_database = prepare.DATA_DIR, prepare.DATABASE
            prepare.DATA_DIR, prepare.DATABASE = data, database
            try:
                result = prepare.register(package, connection_file)
            finally:
                prepare.DATA_DIR, prepare.DATABASE = old_data, old_database

            inventory = Path(result["inventory_path"]).read_text(encoding="utf-8")
            self.assertTrue(result["vpn_profile_found"])
            self.assertEqual(result["remote_check"], "not_started")
            self.assertIn("compose.yaml", inventory)
            parsed = json.loads(inventory)
            self.assertEqual(parsed["inventory"]["compose_services"][0]["service"], "web")
            self.assertEqual(parsed["inventory"]["compose_services"][0]["ports"], ["3300:3000"])
            self.assertNotIn("fake-package-secret", inventory)
            for secret in ("FAKE-VPN-SECRET-123", "FAKE-SSH-SECRET-456"):
                self.assertNotIn(secret, inventory)
                self.assertNotIn(secret.encode(), database.read_bytes())
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("SELECT status FROM tasks").fetchone()[0], "prechecking")
                self.assertEqual(db.execute("SELECT count(*) FROM task_events").fetchone()[0], 1)

    def test_unknown_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(PROJECT_ROOT.resolve()))
            connection_file = root / "connection.txt"
            connection_file.write_text("VPN账号: someone\nVPN密码: FAKE-SECRET\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "目标服务器"):
                prepare.parse_connection_file(connection_file)


if __name__ == "__main__":
    unittest.main()
