import unittest

from testguard import cli


class TestCliSmoke(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as context:
            cli.build_arg_parser().parse_args(["-h"])
        self.assertEqual(context.exception.code, 0)

    def test_doctor_runs(self) -> None:
        return_code = cli.main(["doctor"])
        self.assertEqual(return_code, 0)
