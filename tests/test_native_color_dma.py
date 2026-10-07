"""Check incoming BG columns and CGB clear DMA with audio IRQs active."""
import unittest

import test_native_colors as colors
from test_native_colors import AVAILABLE, program, store


def select_column(physical, logical):
    address = 0x8280 + physical * 288
    return (store(0xcc40, address & 255) + store(0xcc41, address >> 8)
            + store(0xcc49, logical))


def prepare_ring():
    return "".join(select_column(logical % 12, logical) + "    call PrepareAllRows\n"
                   for logical in range(32))


def finish():
    # Put reference colors outside the columns used for PPU comparisons.
    result = r'''
    ldh a,[$ff40]
    bit 7,a
    jr z,.off
.wait_blank
    ldh a,[$ff44]
    cp 144
    jr c,.wait_blank
.off
    di
    xor a
    ldh [$ff40],a
    ldh [$ff07],a
    ldh [$ffff],a
'''
    for bank in range(2):
        result += f"    ld a,{bank}\n    ldh [$ff4f],a\n"
        for base in (0x9800, 0x9c00):
            for channel in range(4):
                value = (1, 1, 1, 2)[channel] if bank else 20 + channel
                result += store(base + 16 + channel, value)
    result += r'''
    xor a
    ldh [$ff4f],a
    ld hl,$9800
    ld de,$c300
    ld bc,$800
    call CopyBytes
'''
    return result


def assembly(body, interrupt="    reti\n", *, complete_early=False):
    result = program(
        "    call InitializeFixtures\n" + body
        + ("    jp NativeDMATestDone\n" if complete_early else finish()),
        interrupt=interrupt,
    )
    result += r'''
NativeDMATestDone:
    ld a,$ac
    ld [$c1ff],a
.forever
    jr .forever

PrepareAllRows:
    xor a
.row
    call NativeColorPrepareRow
    inc a
    cp 18
    jr nz,.row
    ret

ClearColumn:
    xor a
.stage
    call NativeColorClearSlice
    inc a
    cp 12
    jr nz,.stage
    ret

InitializeFixtures:
    ld hl,Patterns
    ld de,$8280
    ld bc,216*16
    call CopyBytes
    ld hl,$9800
    call FillMaps
    ld a,1
    ldh [$ff4f],a
    ld hl,$9800
    call FillMaps
    ld hl,Masks
    ld de,$9000
    ld bc,216
    call CopyBytes
    xor a
    ldh [$ff4f],a
    ret

FillMaps:
    ld bc,$800
.byte
    ld a,$aa
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.byte
    ret

CopyBytes:
    ld a,[hl+]
    ld [de],a
    inc de
    dec bc
    ld a,b
    or c
    jr nz,CopyBytes
    ret

SECTION "DMA fixtures", ROMX[$4000], BANK[1]
Patterns:
'''
    for tile in range(216):
        code = tile % 3 + 1
        result += "    db " + ",".join(str(value) for value in
                                       ([255 if code & 1 else 0, 255 if code & 2 else 0] * 8)) + "\n"
    result += "Masks:\n    db " + ",".join(str(tile % 16) for tile in range(216)) + "\n"
    return result


@unittest.skipUnless(AVAILABLE, "Build the SameBoy harness and install RGBDS first")
class NativeColorDMATests(unittest.TestCase):
    run_program = colors.NativeColorTests.run_program
    pixel = colors.NativeColorTests.pixel

    def check_maps(self, memory, assignments):
        for bank in range(2):
            for is_window, base in enumerate((0xc300, 0xc700) if bank == 0 else (0xd800, 0xdc00)):
                for row in range(32):
                    expected = bytearray([0xaa] * 32)
                    if row < 18 and not is_window:
                        for logical, physical in assignments.items():
                            expected[logical] = 40 + physical * 18 + row if bank == 0 else 1
                    if row == 0:
                        expected[16:20] = bytes((20, 21, 22, 23) if bank == 0 else (1, 1, 1, 2))
                    self.assertEqual(memory[base + row * 32:base + (row + 1) * 32], expected,
                                     f"VRAM bank {bank}, map {base:04x}, row {row}")
        self.assertEqual(memory[0xd000:0xd0d8], bytes(tile % 16 for tile in range(216)),
                         "Preparing map IDs must not change physical-tile history")

    def test_incoming_columns_cover_all_physical_heads_and_logical_wrap(self):
        # Fill all 32 logical columns, then reuse columns 0 and 1 after the
        # map wraps. Twelve physical columns need not divide the map width.
        body = prepare_ring()
        assignments = {logical: logical % 12 for logical in range(32)}
        for logical, physical in ((0, 11), (1, 0)):
            body += select_column(physical, logical) + "    call PrepareAllRows\n"
            assignments[logical] = physical
        memory, pixels, _ = self.run_program(assembly(body))
        self.check_maps(memory, assignments)
        swatches = [self.pixel(pixels, (16 + channel) * 8, 0) for channel in range(4)]
        self.assertEqual(len(set(swatches)), 4)
        for row in range(18):
            for column in range(16):
                tile = assignments[column] * 18 + row
                for y_offset in (0, 7):
                    self.assertEqual(self.pixel(pixels, column * 8 + 3, row * 8 + y_offset),
                                     swatches[tile % 3], f"column {column}, row {row}")

    def test_clear_slices_touch_exact_bytes_in_every_physical_column(self):
        patterns = b"".join(bytes([255 if code & 1 else 0, 255 if code & 2 else 0] * 8)
                            for tile in range(216) for code in (tile % 3 + 1,))
        for column in range(12):
            with self.subTest(column=column):
                base = 0x8280 + column * 288
                body = select_column(column, 0)
                for stage in range(12):
                    body += (f"    ld a,{stage}\n    call NativeColorClearSlice\n"
                             f"    ld hl,${base:04x}\n    ld de,${0xd000 + stage * 288:04x}\n"
                             "    ld bc,288\n    call CopyBytes\n")
                for index, address in enumerate((base - 16, base + 288)):
                    body += (f"    ld hl,${address:04x}\n    ld de,${0xc300 + index * 16:04x}\n"
                             "    ld bc,16\n    call CopyBytes\n")
                body += r'''
    ld a,1
    ldh [$ff4f],a
    ld hl,$9000
    ld de,$c400
    ld bc,216
    call CopyBytes
    xor a
    ldh [$ff4f],a
'''
                memory, _, _ = self.run_program(assembly(body, complete_early=True))
                original = patterns[column * 288:(column + 1) * 288]
                for stage in range(12):
                    count = stage // 2 * 48 + (48 if stage & 1 else 32)
                    self.assertEqual(memory[0xd000 + stage * 288:0xd000 + (stage + 1) * 288],
                                     bytes(count) + original[count:], f"column {column}, stage {stage}")
                before = patterns[column * 288 - 16:column * 288] if column else bytes(16)
                after = patterns[(column + 1) * 288:(column + 1) * 288 + 16] if column < 11 else bytes(16)
                self.assertEqual(memory[0xc300:0xc320], before + after)
                self.assertEqual(memory[0xc400:0xc4d8], bytes(tile % 16 for tile in range(216)))
                self.assertEqual(memory[0xcc70:0xcc80], bytes(16))

    def test_live_lcd_column_preparation_and_clear_dma_keep_audio_running(self):
        body = r'''
    xor a
    ld [$c100],a
    ld [$c101],a
    ldh [$ff04],a
    ldh [$ff0f],a
    ld a,$80
    ldh [$ff26],a
    ld a,$91
    ldh [$ff40],a
    ld a,$fc
    ldh [$ff06],a
    ldh [$ff05],a
    ld a,4
    ldh [$ffff],a
    ldh [$ff07],a
    ei
    ld a,$31
    ldh [$ff03],a
    ld b,4
.round
    push bc
''' + prepare_ring() + select_column(11, 0) + "    call ClearColumn\n" + r'''
    pop bc
    dec b
    jp nz,.round
    ld a,$32
    ldh [$ff03],a
    di
    xor a
    ldh [$ff07],a
    ldh [$ffff],a
'''
        interrupt = r'''
    push af
    ldh a,[$ff4f]
    and 1
    jr z,.bank_ok
    ld [$c101],a
.bank_ok
    ; Most of each period is music, forcing frequent PPU-mode changes
    ; between foreground checks and short access windows.
    push bc
    ld b,180
.busy
    dec b
    jr nz,.busy
    pop bc
    ld a,[$c100]
    inc a
    ld [$c100],a
    and 1
    swap a
    or $e0
    ldh [$ff12],a
    pop af
    reti
'''
        memory, _, writes = self.run_program(assembly(body, interrupt))
        self.check_maps(memory, {logical: logical % 12 for logical in range(32)})
        markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                   for row in writes if row["address"] == "ff03"}
        audio = [int(row["ticks_8mhz"]) for row in writes
                 if row["address"] == "ff12"
                 and markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
        self.assertGreater(len(audio), 100)
        self.assertEqual(memory[0xc100], len(audio) & 255)
        self.assertEqual(memory[0xc101], 0, "music interrupt observed VRAM bank 1")
        self.assertLessEqual(max(b - a for a, b in zip(audio, audio[1:])), 4300)


if __name__ == "__main__":
    unittest.main()
