import unittest

from svc_edit.services import (
    canonical_account,
    classify_account,
    failure_from_raw,
    failure_to_scm,
    join_image_path,
    normalize_environments,
    parse_environment,
    split_image_path,
)


class ImagePathTests(unittest.TestCase):
    def test_roundtrip_quoted_path(self):
        raw = r'"C:\Program Files\App\svc.exe" --port 80'
        exe, args = split_image_path(raw)
        self.assertEqual(exe, r"C:\Program Files\App\svc.exe")
        self.assertEqual(args, "--port 80")
        self.assertEqual(join_image_path(exe, args), raw)

    def test_unquoted_system_path_keeps_variables(self):
        raw = r"%SystemRoot%\System32\spoolsv.exe"
        exe, args = split_image_path(raw)
        self.assertEqual((exe, args), (raw, ""))
        self.assertEqual(join_image_path(exe, args), raw)

    def test_svchost_arguments(self):
        raw = r"C:\WINDOWS\System32\svchost.exe -k netsvcs -p"
        exe, args = split_image_path(raw)
        self.assertEqual(exe, r"C:\WINDOWS\System32\svchost.exe")
        self.assertEqual(args, "-k netsvcs -p")
        self.assertEqual(join_image_path(exe, args), raw)


class AccountTests(unittest.TestCase):
    def test_builtin_names(self):
        self.assertEqual(classify_account("LocalSystem")[0], "local")
        self.assertEqual(classify_account(r"NT AUTHORITY\LocalService")[0], "service")
        self.assertEqual(classify_account(r"NT AUTHORITY\NetworkService")[0], "network")
        self.assertEqual(classify_account("")[0], "local")

    def test_custom_account_gets_local_prefix(self):
        self.assertEqual(canonical_account("custom", "worker"), r".\worker")
        self.assertEqual(canonical_account("custom", r"DOMAIN\worker"), r"DOMAIN\worker")
        self.assertEqual(canonical_account("local"), "LocalSystem")


class EnvironmentTests(unittest.TestCase):
    def test_parse_and_normalize(self):
        self.assertEqual(
            parse_environment(["PATH=C:\\bin", "EMPTY="]),
            [{"name": "PATH", "value": "C:\\bin"}, {"name": "EMPTY", "value": ""}],
        )
        self.assertEqual(
            normalize_environments([{"name": "FOO", "value": "bar"}]),
            ["FOO=bar"],
        )

    def test_reject_duplicate_names(self):
        with self.assertRaises(ValueError):
            normalize_environments(
                [{"name": "Foo", "value": "1"}, {"name": "foo", "value": "2"}]
            )


class FailureTests(unittest.TestCase):
    def test_from_raw_spooler_shape(self):
        parsed = failure_from_raw(
            {"ResetPeriod": 3600, "RebootMsg": None, "Command": None, "Actions": ((1, 5000), (1, 5000), (0, 0))}
        )
        self.assertTrue(parsed["known"])
        self.assertFalse(parsed["never_reset"])
        self.assertEqual(parsed["reset_seconds"], 3600)
        self.assertEqual(parsed["actions"][0], {"type": "restart", "delay_sec": 5})
        self.assertEqual(parsed["actions"][2]["type"], "none")

    def test_never_reset_roundtrip(self):
        payload = failure_to_scm(
            {
                "never_reset": True,
                "reset_seconds": 86400,
                "actions": [
                    {"type": "restart", "delay_sec": 60},
                    {"type": "none", "delay_sec": 0},
                    {"type": "reboot", "delay_sec": 0},
                ],
            },
            {"RebootMsg": "重启", "Command": ""},
        )
        self.assertEqual(payload["ResetPeriod"], -1)
        self.assertEqual(payload["RebootMsg"], "重启")
        self.assertEqual(payload["Actions"][0], (1, 60000))
        self.assertEqual(payload["Actions"][2][0], 2)


if __name__ == "__main__":
    unittest.main()
