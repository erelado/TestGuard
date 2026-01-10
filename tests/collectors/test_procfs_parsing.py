import unittest

from testguard.monitor.collectors.linux import procfs


class TestProcfsParsing(unittest.TestCase):
    def test_parse_status_rss_bytes(self) -> None:
        status_text = "Name:\tpython\nVmRSS:\t 20480 kB\n"
        rss_bytes = procfs._parse_status_rss_bytes(status_text)
        self.assertEqual(rss_bytes, 20480 * 1024)

    def test_parse_io_totals(self) -> None:
        io_text = "\n".join(
            [
                "rchar: 100",
                "wchar: 200",
                "syscr: 1",
                "syscw: 2",
                "read_bytes: 4096",
                "write_bytes: 8192",
                "cancelled_write_bytes: 0",
                "",
            ]
        )
        read_bytes, write_bytes = procfs._parse_io_totals(io_text)
        self.assertEqual(read_bytes, 4096)
        self.assertEqual(write_bytes, 8192)

    def test_parse_stat_cpu_seconds(self) -> None:
        # After ')' tokens are: state, ppid, pgrp, session, tty_nr, tpgid, flags,
        # minflt, cminflt, majflt, cmajflt, utime, stime, ...
        stat_text = "1234 (python) R 1 2 3 4 5 6 7 8 9 10 130 70 0 0 0 0 0"
        cpu_user_s, cpu_sys_s = procfs._parse_stat_cpu_seconds(stat_text, clock_ticks_per_second=100)
        self.assertEqual(cpu_user_s, 1.3)
        self.assertEqual(cpu_sys_s, 0.7)

