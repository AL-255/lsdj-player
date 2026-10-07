"""Small register-preserving startup delays for the native engine."""

def _short_delays() -> tuple[bytes, ...]:
    """Shortest register-preserving delays below one counted-loop period."""
    result = [b""]
    for units in range(1, 46):
        choices = [result[units - 1] + b"\x00"]
        if units >= 3:
            choices.append(result[units - 3] + b"\x18\x00")
        if units >= 7:
            choices.append(result[units - 7] + b"\xf5\xf1")
        result.append(min(choices, key=len))
    return tuple(result)


_SHORT_DELAYS = _short_delays()


def delay_code(cycles: int) -> bytes:
    """An exact delay preserving all registers, with a valid writable stack.

    Each 16-bit loop costs 28*n+64 cycles including saving/restoring AF/BC.
    The short remainder reuses compact NOP, JR and PUSH/POP sequences.
    The loop's branch offset is -5. Keep its counter below $fe00: on DMG,
    DEC BC with BC in $fe00..$feff corrupts OAM during mode 2, even though
    the instruction does not read or write sprite memory. Startup delays
    also run after the LCD and the standalone sprites have been enabled.
    """
    if cycles < 0 or cycles % 4:
        raise ValueError(f"cannot encode {cycles} CPU cycles of delay")
    result = bytearray()
    while cycles > 180:
        count = min(0xfdff, (cycles - 64) // 28)
        result += bytes((0xF5, 0xC5, 0x01, count & 255, count >> 8,
                         0x0B, 0x78, 0xB1, 0x20, 0xFB, 0xC1, 0xF1))
        cycles -= 28 * count + 64
    result += _SHORT_DELAYS[cycles // 4]
    return bytes(result)
