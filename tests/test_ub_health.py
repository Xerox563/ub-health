"""Tests for ub-health using fake system conditions. Run with: python3 -m unittest"""

import datetime
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ub_health as uh  # noqa: E402

NOW = datetime.datetime(2026, 11, 25, tzinfo=datetime.timezone.utc)


class UpdateTests(unittest.TestCase):
    SAMPLE = (
        "Listing...\n"
        "bash/noble-updates 5.2 amd64 [upgradable from: 5.1]\n"
        "curl/noble-security 8.5 amd64 [upgradable from: 8.4]\n"
    )

    def test_parses_package_names(self):
        self.assertEqual(uh.parse_upgradable(self.SAMPLE), ["bash", "curl"])

    def test_finds_security_updates(self):
        self.assertEqual(uh.parse_security(self.SAMPLE), ["curl"])

    def test_security_is_fail(self):
        self.assertEqual(uh.check_updates(self.SAMPLE).status, uh.FAIL)

    def test_normal_updates_are_warn(self):
        text = "Listing...\nbash/noble-updates 5.2 amd64 [upgradable from: 5.1]\n"
        self.assertEqual(uh.check_updates(text).status, uh.WARN)

    def test_no_updates_is_ok(self):
        self.assertEqual(uh.check_updates("Listing...\n").status, uh.OK)


class RebootTests(unittest.TestCase):
    def test_no_flag_is_ok(self):
        self.assertEqual(uh.check_reboot(flag_path="/nonexistent/flag").status, uh.OK)

    def test_flag_is_fail_and_lists_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            flag = os.path.join(tmp, "flag")
            pkgs = os.path.join(tmp, "pkgs")
            open(flag, "w").close()
            with open(pkgs, "w") as handle:
                handle.write("libc6\nlinux-image\n")
            result = uh.check_reboot(flag_path=flag, packages_path=pkgs)
            self.assertEqual(result.status, uh.FAIL)
            self.assertIn("libc6", result.message)


class ServiceTests(unittest.TestCase):
    def test_parses_failed_units(self):
        text = "cups.service loaded failed failed CUPS\n"
        self.assertEqual(uh.parse_failed_services(text), ["cups.service"])

    def test_no_failed_is_ok(self):
        self.assertEqual(uh.check_services("").status, uh.OK)

    def test_failed_is_fail(self):
        result = uh.check_services("cups.service loaded failed failed CUPS\n")
        self.assertEqual(result.status, uh.FAIL)


class DiskTests(unittest.TestCase):
    MOUNTS = "/dev/sda1 / ext4 rw 0 0\nproc /proc proc rw 0 0\n"

    def test_ignores_virtual_filesystems(self):
        self.assertEqual(uh.parse_real_mounts(self.MOUNTS), ["/"])

    def test_nearly_full_is_fail(self):
        result = uh.check_disk(self.MOUNTS, usage=lambda p: (100, 95, 5_000_000_000))
        self.assertEqual(result.status, uh.FAIL)

    def test_half_full_is_ok(self):
        result = uh.check_disk(self.MOUNTS, usage=lambda p: (100, 50, 50_000_000_000))
        self.assertEqual(result.status, uh.OK)


class CertificateTests(unittest.TestCase):
    def expiring_in(self, days):
        return lambda path: NOW + datetime.timedelta(days=days)

    def test_expired_is_fail(self):
        result = uh.check_certificates(["/c.pem"], expiry=self.expiring_in(-1), now=NOW)
        self.assertEqual(result.status, uh.FAIL)

    def test_three_days_is_fail(self):
        result = uh.check_certificates(["/c.pem"], expiry=self.expiring_in(3), now=NOW)
        self.assertEqual(result.status, uh.FAIL)

    def test_twenty_days_is_warn(self):
        result = uh.check_certificates(["/c.pem"], expiry=self.expiring_in(20), now=NOW)
        self.assertEqual(result.status, uh.WARN)

    def test_long_validity_is_ok(self):
        result = uh.check_certificates(["/c.pem"], expiry=self.expiring_in(200), now=NOW)
        self.assertEqual(result.status, uh.OK)

    def test_unreadable_cert_asks_for_sudo(self):
        def denied(path):
            raise subprocess.CalledProcessError(1, "openssl")
        result = uh.check_certificates(["/c.pem"], expiry=denied, now=NOW)
        self.assertEqual(result.status, uh.WARN)
        self.assertIn("sudo", result.message)

    def test_no_certificates_is_ok(self):
        self.assertEqual(uh.check_certificates([], now=NOW).status, uh.OK)


class ConfigAndOsTests(unittest.TestCase):
    def test_extra_paths_skip_comments_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "certs.txt")
            with open(path, "w") as handle:
                handle.write("# note\n/srv/a.pem\n\n  /opt/b.pem  \n")
            self.assertEqual(uh.load_extra_cert_paths(path), ["/srv/a.pem", "/opt/b.pem"])

    def test_detects_ubuntu(self):
        self.assertTrue(uh.is_ubuntu('NAME="Ubuntu"\nID=ubuntu\n'))

    def test_rejects_other_distros(self):
        self.assertFalse(uh.is_ubuntu("ID=debian\n"))


class ReportTests(unittest.TestCase):
    def test_exit_codes(self):
        ok = uh.Result("A", uh.OK, "fine")
        warn = uh.Result("B", uh.WARN, "hmm")
        fail = uh.Result("C", uh.FAIL, "bad")
        self.assertEqual(uh.exit_code([ok]), 0)
        self.assertEqual(uh.exit_code([ok, warn]), 1)
        self.assertEqual(uh.exit_code([warn, fail]), 2)

    def test_fix_line_only_for_problems(self):
        text = uh.render_report([
            uh.Result("A", uh.OK, "fine", fix="never shown"),
            uh.Result("B", uh.WARN, "hmm", fix="sudo do-thing"),
        ], today=NOW.date())
        self.assertNotIn("never shown", text)
        self.assertIn("Fix: sudo do-thing", text)

    def test_fix_mode_skips_when_no_answer(self):
        calls = []

        def no_tty(prompt):
            raise EOFError

        uh.run_fixes([uh.Result("A", uh.WARN, "x", fix="echo hi")],
                     ask=no_tty, run=lambda c, **k: calls.append(c))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
