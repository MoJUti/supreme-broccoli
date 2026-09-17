import unittest

from remote.child_agent import execute


class ChildAgentTests(unittest.TestCase):
    def test_read_only_identity(self) -> None:
        response = execute({"version": 1, "action": "identity", "params": {}})
        self.assertEqual(response["status"], "ok")
        self.assertIn("hostname", response["data"])

    def test_arbitrary_command_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "action_not_allowed"):
            execute({"version": 1, "action": "shell", "params": {"command": "whoami"}})

    def test_service_name_cannot_be_shell_option(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_service_name"):
            execute({"version": 1, "action": "service_status", "params": {"name": "--system"}})


if __name__ == "__main__":
    unittest.main()
