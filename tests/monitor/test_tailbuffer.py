import unittest
from testguard.tail_buffer import TailBuffer


class TestTailBuffer(unittest.TestCase):
    def test_tail_keeps_last_bytes(self) -> None:
        tail = TailBuffer(max_bytes=5)
        tail.append(b"hello")
        self.assertEqual(tail.get_bytes(), b"hello")

        tail.append(b"world")
        self.assertEqual(tail.get_bytes(), b"world")
