import unittest

from svc_edit.agent_ctl import launch_command_line
from svc_edit.monitor import normalize_config
from svc_edit.wrapper import _quote, build_host_image_path


class WrapperPathTests(unittest.TestCase):
    def test_quote_spaces(self):
        self.assertEqual(_quote(r"C:\Program Files\a.exe"), '"C:\\Program Files\\a.exe"')
        self.assertEqual(_quote("plain"), "plain")

    def test_build_host_image_path_contains_service_flag(self):
        image = build_host_image_path("MyHostSvc")
        self.assertIn("--service", image)
        self.assertIn("MyHostSvc", image)


class MonitorConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = normalize_config(None)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["interval_sec"], 5)
        self.assertEqual(cfg["watched"], [])

    def test_clamp_interval_and_dedupe(self):
        cfg = normalize_config(
            {
                "enabled": True,
                "interval_sec": 1,
                "watched": [
                    {"name": "Spooler", "auto_restart": True},
                    {"name": "spooler", "auto_restart": False},
                    {"name": ""},
                ],
            }
        )
        self.assertEqual(cfg["interval_sec"], 2)
        self.assertEqual(len(cfg["watched"]), 1)
        self.assertEqual(cfg["watched"][0]["name"], "Spooler")
        self.assertTrue(cfg["watched"][0]["auto_restart"])


class AgentCtlTests(unittest.TestCase):
    def test_launch_command_includes_tray(self):
        cmd = launch_command_line()
        self.assertIn("--tray", cmd)
        self.assertIn("main.py", cmd.replace("\\", "/").lower())


if __name__ == "__main__":
    unittest.main()
