import unittest

from testguard.monitor.collectors.cgroupv2 import (
    _parse_cpu_stat_seconds,
    _parse_io_stat_totals,
    _parse_memory_max_bytes,
    _parse_proc_cgroup,
)


class TestCgroupV2Parsing(unittest.TestCase):
    def test_parse_proc_cgroup(self) -> None:
        text = "12:cpuset:/\n0::/user.slice/user-1000.slice/session-2.scope\n"
        self.assertEqual(_parse_proc_cgroup(text), "/user.slice/user-1000.slice/session-2.scope")

    def test_parse_memory_max_bytes(self) -> None:
        self.assertEqual(_parse_memory_max_bytes("1024"), 1024)
        self.assertIsNone(_parse_memory_max_bytes("max"))

    def test_parse_cpu_stat_seconds(self) -> None:
        cpu_stat = "usage_usec 999\nuser_usec 2000000\nsystem_usec 500000\n"
        user_s, sys_s = _parse_cpu_stat_seconds(cpu_stat)
        self.assertEqual(user_s, 2.0)
        self.assertEqual(sys_s, 0.5)

    def test_parse_io_stat_totals(self) -> None:
        io_stat = "8:0 rbytes=10 wbytes=20 rios=1 wios=2\n259:0 rbytes=5 wbytes=7\n"
        read_bytes, write_bytes = _parse_io_stat_totals(io_stat)
        self.assertEqual(read_bytes, 15)
        self.assertEqual(write_bytes, 27)
