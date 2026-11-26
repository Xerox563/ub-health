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

    def test_same_drive_is_listed_once(self):
        text = (
            "/dev/nvme0n1p3 / ext4 rw 0 0\n"
            "/dev/nvme0n1p3 /var/snap/firefox/common/host-hunspell ext4 rw 0 0\n"
            "/dev/nvme0n1p1 /boot/efi vfat rw 0 0\n"
        )
        self.assertEqual(uh.parse_real_mounts(text), ["/", "/boot/efi"])

    def test_nearly_full_is_fail(self):
        result = uh.check_disk(self.MOUNTS, usage=lambda p: (100, 95, 5_000_000_000))
        self.assertEqual(result.status, uh.FAIL)

    def test_half_full_is_ok(self):
        result = uh.check_disk(self.MOUNTS, usage=lambda p: (100, 50, 50_000_000_000))
        self.assertEqual(result.status, uh.OK)


class MemoryTests(unittest.TestCase):
    PLENTY = ("MemTotal:       16000000 kB\nMemAvailable:    8000000 kB\n"
              "SwapTotal:       2000000 kB\nSwapFree:        2000000 kB\n")
    TIGHT = "MemTotal:       16000000 kB\nMemAvailable:     400000 kB\n"

    def test_parses_values_in_kb(self):
        self.assertEqual(uh.parse_meminfo(self.PLENTY)["MemTotal"], 16000000)

    def test_plenty_of_memory_is_ok(self):
        self.assertEqual(uh.check_memory(self.PLENTY).status, uh.OK)

    def test_very_little_free_is_fail(self):
        self.assertEqual(uh.check_memory(self.TIGHT).status, uh.FAIL)

    def test_swap_is_charted_only_when_present(self):
        with_swap = uh.check_memory(self.PLENTY)
        without = uh.check_memory(self.TIGHT)
        self.assertEqual([d[0] for d in with_swap.details], ["Memory used", "Swap used"])
        self.assertEqual([d[0] for d in without.details], ["Memory used"])


class FirewallTests(unittest.TestCase):
    def test_active_firewall_is_ok(self):
        self.assertEqual(uh.check_firewall("Status: active\n").status, uh.OK)

    def test_inactive_firewall_is_warn(self):
        result = uh.check_firewall("Status: inactive\n")
        self.assertEqual(result.status, uh.WARN)
        self.assertEqual(result.fix, "sudo ufw enable")

    def test_needs_root_but_service_running_is_ok(self):
        text = "ERROR: You need to be root to run this script\n"
        self.assertEqual(uh.check_firewall(text, service_state="active").status, uh.OK)

    def test_needs_root_and_service_stopped_is_warn(self):
        text = "ERROR: You need to be root to run this script\n"
        self.assertEqual(uh.check_firewall(text, service_state="inactive").status, uh.WARN)


class AutoUpdateTests(unittest.TestCase):
    ON = 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n'
    OFF = 'APT::Periodic::Unattended-Upgrade "0";\n'

    def test_enabled_is_ok(self):
        self.assertEqual(uh.check_auto_updates(self.ON).status, uh.OK)

    def test_disabled_is_warn(self):
        self.assertEqual(uh.check_auto_updates(self.OFF).status, uh.WARN)

    def test_missing_file_is_warn(self):
        self.assertEqual(uh.check_auto_updates("").status, uh.WARN)


class JournalTests(unittest.TestCase):
    LINES = (
        "Nov 25 10:00:00 host cups[123]: printer offline\n"
        "Nov 25 10:01:00 host cups[123]: printer offline\n"
        "Nov 25 10:02:00 host snapd[9]: failed to refresh\n"
    )

    def test_counts_and_names_noisiest_program(self):
        self.assertEqual(uh.parse_journal_errors(self.LINES), (3, "cups"))

    def test_no_errors_is_ok(self):
        self.assertEqual(uh.check_journal("").status, uh.OK)

    def test_few_errors_is_ok(self):
        self.assertEqual(uh.check_journal(self.LINES).status, uh.OK)

    def test_many_errors_is_warn(self):
        self.assertEqual(uh.check_journal(self.LINES * 20).status, uh.WARN)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "nested", "history.jsonl")
        self.results = [uh.Result("A", uh.OK, "x"), uh.Result("B", uh.FAIL, "y")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_creates_folder_and_appends(self):
        uh.save_history(self.results, self.path, NOW)
        uh.save_history(self.results, self.path, NOW)
        self.assertEqual(len(uh.load_history(self.path)), 2)

    def test_entry_contents(self):
        entry = uh.history_entry(self.results, NOW.replace(tzinfo=None))
        self.assertEqual((entry["health"], entry["ok"], entry["fail"]), (50, 1, 1))

    def test_damaged_lines_are_skipped(self):
        uh.save_history(self.results, self.path, NOW)
        with open(self.path, "a") as handle:
            handle.write("this is not json\n")
        self.assertEqual(len(uh.load_history(self.path)), 1)

    def test_limit_keeps_latest(self):
        for _ in range(5):
            uh.save_history(self.results, self.path, NOW)
        self.assertEqual(len(uh.load_history(self.path, limit=3)), 3)

    def test_missing_file_is_empty(self):
        self.assertEqual(uh.load_history(os.path.join(self.tmp.name, "none")), [])


class TrendTests(unittest.TestCase):
    def test_sparkline_low_and_high(self):
        self.assertEqual(uh.sparkline([0, 100]), "▁█")

    def test_sparkline_length_matches_values(self):
        self.assertEqual(len(uh.sparkline([10, 50, 90])), 3)

    def test_trend_shown_only_with_two_runs(self):
        one = uh.render_report([uh.Result("A", uh.OK, "x")], history=[{"health": 50}])
        two = uh.render_report([uh.Result("A", uh.OK, "x")],
                               history=[{"health": 50}, {"health": 100}])
        self.assertNotIn("Trend", one)
        self.assertIn("Trend", two)


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
