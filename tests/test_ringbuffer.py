import unittest

from testguard.monitor.ringbuffer import RingBuffer


class TestRingBuffer(unittest.TestCase):
    def test_ringbuffer_evicts_old_items(self) -> None:
        ring_buffer = RingBuffer[int](capacity=3)
        ring_buffer.append(1)
        ring_buffer.append(2)
        ring_buffer.append(3)
        ring_buffer.append(4)

        self.assertEqual(ring_buffer.snapshot(), [2, 3, 4])
        self.assertEqual(len(ring_buffer), 3)
