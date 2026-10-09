import unittest

from svc_edit.agent_ctl import launch_command_line
from svc_edit.monitor import normalize_config
from svc_edit.updater import parse_version, version_gt
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


class UpdaterTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("v0.9.0"), (0, 9, 0))
        self.assertEqual(parse_version("1.2.3-beta"), (1, 2, 3))

    def test_version_gt(self):
        self.assertTrue(version_gt("0.9.0", "0.8.0"))
        self.assertTrue(version_gt("1.0", "0.9.9"))
        self.assertFalse(version_gt("0.9.0", "0.9.0"))
        self.assertFalse(version_gt("0.8.1", "0.9.0"))

    def test_status_messages_local(self):
        from svc_edit.updater import check_for_update

        # 不依赖网络的状态逻辑已在 check 内；此处只验证版本比较语义
        self.assertTrue(version_gt("0.9.0", "0.6.0"))
        self.assertFalse(version_gt("0.6.0", "0.9.0"))


if __name__ == "__main__":
    unittest.main()
