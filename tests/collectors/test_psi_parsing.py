import unittest

from testguard.monitor.collectors.linux.psi import parse_psi_pressure_text


class TestPsiParsing(unittest.TestCase):
    def test_parses_avg10_for_some_and_full(self) -> None:
        text = (
            "some avg10=1.23 avg60=0.50 avg300=0.10 total=12345\n"
            "full avg10=0.04 avg60=0.02 avg300=0.01 total=999\n"
        )
        fragments = parse_psi_pressure_text(text)
        self.assertEqual(fragments["psi_memory_some_avg10"], 1.23)
        self.assertEqual(fragments["psi_memory_full_avg10"], 0.04)
