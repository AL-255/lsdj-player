"""Native startup delays must preserve timing without triggering DMG OAM bugs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from native_delay import delay_code


def execute_delay(code):
    """Evaluate the emitted CPU subset and inspect each 16-bit decrement."""
    a, flags, b, c = initial = (0x39, 0xb0, 0x53, 0x7f)
    stack, pc, cycles = [], 0, 0
    while pc < len(code):
        op = code[pc]
        pc += 1
        if op == 0:
            cycles += 4
        elif op in (0xf5, 0xc5):
            stack.append((a, flags) if op == 0xf5 else (b, c))
            cycles += 16
        elif op == 0xf1:
            a, flags = stack.pop()
            cycles += 12
        elif op == 0xc1:
            b, c = stack.pop()
            cycles += 12
        elif op == 1:
            c, b = code[pc:pc + 2]
            pc += 2
            cycles += 12
        elif op == 0x0b:
            counter = b << 8 | c
            if 0xfe00 <= counter <= 0xfeff:
                raise AssertionError(f"DEC BC at ${counter:04x} triggers DMG OAM corruption")
            counter = (counter - 1) & 65535
            b, c = counter >> 8, counter & 255
            cycles += 8
        elif op == 0x78:
            a = b
            cycles += 4
        elif op == 0xb1:
            a |= c
            flags = 0x80 if a == 0 else 0
            cycles += 4
        elif op in (0x18, 0x20):
            relative = code[pc]
            pc += 1
            taken = op == 0x18 or not (flags & 0x80)
            if taken:
                pc += relative - 256 if relative >= 128 else relative
            cycles += 12 if taken else 8
        else:
            raise AssertionError(f"Unexpected delay opcode ${op:02x}")
    if pc != len(code) or stack or (a, flags, b, c) != initial:
        raise AssertionError("Delay failed to preserve registers, flags, or stack")
    return cycles


class NativeDelayTests(unittest.TestCase):
    def test_short_delays_preserve_state_and_timing(self):
        for cycles in range(0, 188, 4):
            with self.subTest(cycles=cycles):
                self.assertEqual(execute_delay(delay_code(cycles)), cycles)

    def test_oam_boundary_and_native_startup_delays(self):
        boundary = 0xfe00 * 28 + 64
        for cycles in (boundary - 4, boundary, boundary + 4,
                       210908 - 80, 211872 - 84, 8220996, 8547120):
            with self.subTest(cycles=cycles):
                self.assertEqual(execute_delay(delay_code(cycles)), cycles)

    def test_rejects_unrepresentable_delays(self):
        for cycles in (-4, 1, 3, 7):
            with self.subTest(cycles=cycles):
                with self.assertRaises(ValueError):
                    delay_code(cycles)


if __name__ == "__main__":
    unittest.main()
