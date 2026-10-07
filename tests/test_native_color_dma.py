"""Check CGB map DMA against real VRAM and the PPU with audio IRQs active."""
import unittest

import test_native_colors as colors
from test_native_colors import AVAILABLE, program, store


MASK_PALETTES = (1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4, 1)
PALETTE_CHANNELS = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))


def row_table(head, row):
    return [40 + ((head + column) % 12) * 18 + row for column in range(11)] + [
        (0xd0 + head + row + column) & 255 for column in range(5)
    ]


def select_head(head):
    return (f"    ld a,LOW(MapHead{head})\n    ld [WaterfallMapSource],a\n"
            f"    ld a,HIGH(MapHead{head})\n    ld [WaterfallMapSource+1],a\n")


def prepare_maps():
    return r'''
    ld a,$98
    ld [WaterfallMapDestHigh],a
    call PrepareAllRows
    ld a,$9c
    ld [WaterfallMapDestHigh],a
    call PrepareAllRows
'''


def finish():
    # The four reference swatches are outside the 16-column DMA region.
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
    result = "DEF WaterfallMapSource EQU $cc42\n" + program(
        "    call InitializeFixtures\n" + body
        + ("    jp NativeDMATestDone\n" if complete_early else finish()),
        interrupt=interrupt, refresh_maps=False,
    )
    result += r'''
INCLUDE "src/native/color_map.asm"

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
    result += "    ds (-@) & 15,0\n"
    for head in range(12):
        result += f"MapHead{head}:\n"
        for row in range(18):
            result += "    db " + ",".join(map(str, row_table(head, row))) + "\n"
    return result


@unittest.skipUnless(AVAILABLE, "Build the SameBoy harness and install RGBDS first")
class NativeColorDMATests(unittest.TestCase):
    run_program = colors.NativeColorTests.run_program
    pixel = colors.NativeColorTests.pixel

    def check_maps_and_screen(self, memory, pixels, head):
        for bank in range(2):
            for base in ((0xc300, 0xc700) if bank == 0 else (0xd800, 0xdc00)):
                for row in range(32):
                    expected = bytearray([0xaa] * 32)
                    if row < 18:
                        ids = row_table(head, row)
                        expected[:16] = bytes(ids if bank == 0 else [
                            MASK_PALETTES[(tile - 40) % 16] for tile in ids[:11]
                        ] + [0] * 5)
                    if row == 0:
                        expected[16:20] = bytes((20, 21, 22, 23) if bank == 0 else (1, 1, 1, 2))
                    self.assertEqual(memory[base + row * 32:base + (row + 1) * 32], expected,
                                     f"head {head}, VRAM bank {bank}, map {base:04x}, row {row}")
        colors = [self.pixel(pixels, (16 + channel) * 8, 0) for channel in range(4)]
        self.assertEqual(len(set(colors)), 4)
        # Verify real displayed pixels throughout both screen halves, not
        # merely attempted writes that a busy PPU might have discarded.
        for row in range(18):
            for column in range(11):
                tile = ((head + column) % 12) * 18 + row
                palette = MASK_PALETTES[tile % 16]
                channel = PALETTE_CHANNELS[palette - 1][tile % 3]
                for y_offset in (0, 7):
                    self.assertEqual(self.pixel(pixels, column * 8 + 3, row * 8 + y_offset), colors[channel],
                                     f"head {head}, displayed column {column}, row {row}")

    def test_all_ring_heads_prepare_both_maps_and_all_rows(self):
        for head in range(12):
            with self.subTest(head=head):
                body = select_head(head) + prepare_maps()
                memory, pixels, _ = self.run_program(assembly(body))
                self.check_maps_and_screen(memory, pixels, head)
                self.assertEqual(memory[0xcc7b:0xcc80], bytes(5), "unused attribute bytes were not cleared")

    def test_clear_slices_touch_exact_bytes_in_every_physical_column(self):
        patterns = b"".join(bytes([255 if code & 1 else 0, 255 if code & 2 else 0] * 8)
                            for tile in range(216) for code in (tile % 3 + 1,))
        for column in range(12):
            with self.subTest(column=column):
                base = 0x8280 + column * 288
                body = store(0xcc40, base & 255) + store(0xcc41, base >> 8)
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

    def test_live_lcd_dma_keeps_timer_audio_running_and_vram_bank_zero(self):
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
''' + select_head(11) + prepare_maps() + select_head(0) + prepare_maps() + r'''
    pop bc
    dec b
    jr nz,.round
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
    ; Spend most of this timer period in music, so return frequently lands
    ; in a different LCD mode from the foreground's last observation.
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
        memory, pixels, writes = self.run_program(assembly(body, interrupt))
        self.check_maps_and_screen(memory, pixels, 0)
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
