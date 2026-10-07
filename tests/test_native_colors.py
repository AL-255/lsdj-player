"""Exercise CGB channel colors on the real CPU and PPU in SameBoy."""
import csv
import itertools
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
AVAILABLE = RUNNER.exists() and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix"))
COLORS = (0x7f68, 0x559f, 0x23d6, 0x1f3f)
BACKGROUND = 0x2866


def store(address, value):
    return f"    ld a,${value:02x}\n    ld [${address:04x}],a\n"


def point(channel, column, y, x=0):
    address = 0x8280 + column * 288 + y * 2
    return (store(0xcc50, channel)
            + f"    ld hl,${address:04x}\n    ld b,${0x80 >> x:02x}\n"
            + "    call NativeColorPixel\n")


def program(body, *, bends=False, interrupt="    reti\n", refresh_maps=True):
    display = (ROOT / "src/native/display.asm").read_text()
    accessors = display.split("NativeStore:\n", 1)[1].split("NativePreparation:", 1)[0]
    maps = ""
    for base in (0x9800, 0x9c00):
        for row in range(18):
            maps += f"    ld hl,${base + row * 32:04x}\n"
            for column in range(11):
                maps += f"    ld a,{40 + column * 18 + row}\n    ld [hl+],a\n"
        # Solid swatches let us compare the PPU's actual colors without
        # assuming an emulator-specific RGB555 color-correction formula.
        for channel in range(4):
            maps += store(base + 12 + channel, 20 + channel)
    references = ""
    for channel, (palette, code) in enumerate(((1, 1), (1, 2), (1, 3), (2, 3))):
        for row in range(8):
            references += store(0x8140 + channel * 16 + row * 2, 255 if code & 1 else 0)
            references += store(0x8141 + channel * 16 + row * 2, 255 if code & 2 else 0)
        references += "    ld a,1\n    ldh [$ff4f],a\n"
        for base in (0x9800, 0x9c00):
            references += store(base + 12 + channel, palette)
        references += "    xor a\n    ldh [$ff4f],a\n"
    labels = "    ld a,1\n    ldh [$ff4f],a\n"
    for channel in range(4):
        for cell in range(8):
            labels += store(0x9911 + channel * 64 + cell, 8 if channel >= 2 else 0)
    labels += "    xor a\n    ldh [$ff4f],a\n"
    refresh = ""
    if refresh_maps:
        for base in (0x9800, 0x9c00):
            refresh += store(0xcc44, base >> 8)
            for row in range(18):
                refresh += f"    ld a,{row}\n    call NativeColorMapRow\n"
    return r'''
DEF NATIVE_CHANNEL_COLORS EQU 1
DEF WaterfallPitchY EQU $cc20
DEF WaterfallActive EQU $cc24
DEF WaterfallHead EQU $cc26
DEF WaterfallLCD EQU $cc27
DEF WaterfallColumnBase EQU $cc40
DEF WaterfallMapDestHigh EQU $cc44
DEF WaterfallPixelPhase EQU $cc47
SECTION "Timer interrupt", ROM0[$50]
    jp AudioInterrupt
SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150-@,0
SECTION "Program", ROM0[$150]
Start:
    di
    ld sp,$fffe
    xor a
    ldh [$ffff],a
    ldh [$ff07],a
.blank
    ldh a,[$ff44]
    cp 144
    jr c,.blank
    xor a
    ldh [$ff40],a
    ldh [$ff0f],a
    ldh [$ff4f],a
    ldh [$ff42],a
    ldh [$ff43],a
    ld a,1
    ldh [$ff90],a
    ldh [$ff4d],a
    stop
    ld hl,$8000
    ld bc,$2000
.clear
    xor a
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.clear
''' + maps + labels + store(0xcc27, 0xb7) + r'''
    call NativeColorInit
''' + references + body + refresh + r'''
    ; Preserve bank 1 for assertions before starting the display. CPU bus
    ; reads of VRAM during a later mode 3 would otherwise return $ff.
    ldh a,[$ff40]
    bit 7,a
    jr z,.snapshot_ready
.snapshot_blank
    ldh a,[$ff44]
    cp 144
    jr c,.snapshot_blank
.snapshot_ready
    di
    xor a
    ldh [$ff40],a
    ld a,1
    ldh [$ff4f],a
    ld hl,$9000
    ld de,$d000
    ld bc,$1000
.snapshot
    ld a,[hl+]
    ld [de],a
    inc de
    dec bc
    ld a,b
    or c
    jr nz,.snapshot
    xor a
    ldh [$ff4f],a
    ld hl,$c200
    ld b,0
.palette
    ld a,b
    ldh [$ff68],a
    ldh a,[$ff69]
    ld [hl+],a
    inc b
    ld a,b
    cp 64
    jr nz,.palette
    ld a,$91
    ldh [$ff40],a
    ld a,$ac
    ld [$c1ff],a
.forever
    jr .forever
AudioInterrupt:
''' + interrupt + r'''
INCLUDE "src/native/color.asm"
''' + ('INCLUDE "src/native/bend.asm"\n' if bends else '') + r'''
NativeStore:
''' + accessors + r'''
WaterfallPixelMasks:
    db $80,$40,$20,$10,$08,$04,$02,$01
WaterfallPixelPointers:
    FOR head,12
        dw $8280 + ((head + 10) % 12) * 288
    ENDR
'''


@unittest.skipUnless(AVAILABLE, "Build the SameBoy harness and install RGBDS first")
class NativeColorTests(unittest.TestCase):
    def run_program(self, assembly):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source, obj, rom = (target / name for name in ("colors.asm", "colors.o", "colors.gb"))
            source.write_text(assembly)
            for command in (["rgbasm", "-o", str(obj), str(source)],
                            ["rgblink", "-o", str(rom), str(obj)],
                            ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            memory, screen, trace = (target / name for name in ("memory.bin", "screen.ppm", "trace.tsv"))
            result = subprocess.run([
                str(RUNNER), "--model", "cgb", "--rom", str(rom), "--frames", "300",
                "--memory-out", str(memory), "--screen", str(screen),
                "--trace", str(trace), "--trace-address", "ff03",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            contents = memory.read_bytes()
            self.assertEqual(contents[0xc1ff], 0xac, "test program did not finish")
            magic, dimensions, maximum, pixels = screen.read_bytes().split(b"\n", 3)
            self.assertEqual((magic, dimensions, maximum), (b"P6", b"160 144", b"255"))
            with trace.open() as stream:
                writes = list(csv.DictReader(stream, delimiter="\t"))
            return contents, pixels, writes

    def pixel(self, pixels, x, y):
        offset = (y * 160 + x) * 3
        return pixels[offset:offset + 3]

    def assert_screen(self, pixels, expected):
        colors = [self.pixel(pixels, (12 + channel) * 8, 0) for channel in range(4)]
        self.assertEqual(len(set(colors)), 4)
        background = self.pixel(pixels, 7, 7)
        for y in range(144):
            for x in range(88):
                channel = expected.get((x, y))
                actual = self.pixel(pixels, x, y)
                desired = background if channel is None else colors[channel]
                self.assertEqual(actual, desired, f"pixel ({x}, {y}), channel {channel}")

    def test_palettes_preserve_channel_identity_and_hide_only_lowest_priority(self):
        # Every insertion order is tested; changing a tile's palette must
        # recolor its stored indices so that old pixels retain their channel.
        body = ""
        expected = {}
        orders = list(itertools.permutations(range(4)))
        for index, order in enumerate(orders):
            column, tile_row = index % 11, index // 11
            for channel in order:
                y = tile_row * 8 + channel
                body += point(channel, column, y, channel)
            for channel in range(3):
                expected[(column * 8 + channel, tile_row * 8 + channel)] = channel
        # Every possible palette also renders two or three channels with
        # their exact identities. Include NOI in each non-full subset.
        for index, channels in enumerate(((3,), (2, 3), (1, 3), (0, 3),
                                           (1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2))):
            for channel in reversed(channels):
                body += point(channel, index, 32 + channel, channel)
                expected[(index * 8 + channel, 32 + channel)] = channel
        memory, pixels, _ = self.run_program(program(body))
        self.assert_screen(pixels, expected)
        for palette, channels in enumerate(((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)), 1):
            expected_palette = b"".join(value.to_bytes(2, "little")
                                         for value in (BACKGROUND, *(COLORS[ch] for ch in channels)))
            self.assertEqual(memory[0xc200 + palette * 8:0xc208 + palette * 8], expected_palette)
        for channel, (palette, attribute) in enumerate(((5, 5), (6, 6), (1, 9), (7, 15))):
            self.assertEqual(memory[0xc206 + palette * 8:0xc208 + palette * 8],
                             COLORS[channel].to_bytes(2, "little"))
            offset = 0xd911 + channel * 64
            self.assertEqual(memory[offset:offset + 8], bytes([attribute]) * 8)

    def test_pixel_overlap_priority_and_register_preservation(self):
        body = ""
        expected = {}
        for index, order in enumerate(itertools.permutations(range(4))):
            column, row = index % 11, index // 11
            for channel in order:
                body += point(channel, column, row * 8, 0)
            expected[(column * 8, row * 8)] = 0
        for index, channels in enumerate(((3, 2), (2, 3), (3, 1), (1, 3), (2, 1), (1, 2))):
            for channel in channels:
                body += point(channel, index, 40, 3)
            expected[(index * 8 + 3, 40)] = min(channels)
        body += store(0xcc50, 1) + r'''
    ld bc,$104e
    ld de,$bace
    ld hl,$82e4
    call NativeColorPixel
'''
        for offset, register in enumerate(("b", "c", "d", "e", "h", "l")):
            body += f"    ld a,{register}\n    ld [${0xc280 + offset:04x}],a\n"
        expected[(3, 50)] = 1
        memory, pixels, _ = self.run_program(program(body))
        self.assert_screen(pixels, expected)
        self.assertEqual(memory[0xc280:0xc286], bytes.fromhex("10 4e ba ce 82 e4"))

    def test_column_reuse_clears_only_its_metadata_and_prepares_both_maps(self):
        body = ""
        for column in range(12):
            for row in range(18):
                body += point(3, column, row * 8, 0)
        # Clear the last physical column and then the first, spanning the
        # ring wrap. Clear patterns as the normal preparation slices do.
        for column in (11, 0):
            base = 0x8280 + column * 288
            body += store(0xcc40, base & 255) + store(0xcc41, base >> 8)
            body += "    call NativeColorBegin\n"
            body += f"    ld hl,${base:04x}\n    ld b,144\n"
            body += f".clear_column{column}\n    xor a\n    ld [hl+],a\n    ld [hl+],a\n"
            body += f"    dec b\n    jr nz,.clear_column{column}\n"
        # Change the inactive map's IDs as normal preparation does, then
        # ask the color row helper to publish corresponding attributes.
        for base in (0x9800, 0x9c00):
            body += store(0xcc44, base >> 8)
            for row in range(18):
                for column in range(11):
                    physical = (column + 1) % 12
                    body += store(base + row * 32 + column, 40 + physical * 18 + row)
                body += f"    ld a,{row}\n    call NativeColorMapRow\n"
        # This driver is large enough to occupy the fixed bank-1 window.
        assembly = program("    call ColumnCases\n")
        assembly += '\nSECTION "Column cases", ROMX[$4000], BANK[1]\nColumnCases:\n' + body + "    ret\n"
        memory, _, _ = self.run_program(assembly)
        metadata = memory[0xd000:0xd0d8]
        self.assertEqual(metadata[:18], bytes(18))
        self.assertEqual(metadata[-18:], bytes(18))
        self.assertTrue(all(metadata[18:198]))
        for base in (0xd800, 0xdc00):
            for row in range(18):
                for column in range(11):
                    expected = 1 if column == 10 else 2
                    self.assertEqual(memory[base + row * 32 + column] & 7, expected,
                                     f"map {base:04x}, row {row}, column {column}")

    def test_palette_changes_update_current_and_already_prepared_future_map(self):
        body = ""
        expected = bytearray()
        for variant, active in enumerate((0x9800, 0x9c00)):
            inactive = active ^ 0x400
            body += "    call NativeColorInit\n"
            body += store(0xcc27, 0xb7 | (0x40 if variant else 0))
            body += store(0xcc26, 1)  # newest physical column is 11
            body += store(0xcc44, inactive >> 8)
            for row in (0, 17):
                # The next map shifts the current newest tile left once.
                body += store(active + row * 32 + 10, 40 + 11 * 18 + row)
                body += store(inactive + row * 32 + 9, 40 + 11 * 18 + row)
                body += f"    ld a,{row}\n    call NativeColorMapRow\n"
            for stage, channel in enumerate((3, 2, 1, 0)):
                for row in (0, 17):
                    body += point(channel, 11, row * 8 + channel, channel)
                for row in (0, 17):
                    for base, column in ((active, 10), (inactive, 9)):
                        destination = 0xc300 + len(expected)
                        body += (f"    ld hl,${base + row * 32 + column:04x}\n"
                                 "    call NativeColorRead\n"
                                 f"    ld [${destination:04x}],a\n")
                        expected.append((2, 3, 4, 1)[stage])
        memory, _, _ = self.run_program(program(body, refresh_maps=False))
        self.assertEqual(memory[0xc300:0xc300 + len(expected)], expected)

    def test_bend_span_uses_its_channel_color(self):
        body = store(0xcc26, 2) + store(0xcc47, 3)
        expected = {}
        for channel, (first, last) in enumerate(((4, 19), (30, 10), (123, 143))):
            body += store(0xcc50, channel)
            body += f"    ld b,{first}\n    ld c,{last}\n    call NativeBendSpan\n"
            for y in range(min(first, last), max(first, last) + 1):
                expected[(3, y)] = min(expected.get((3, y), channel), channel)
        _, pixels, _ = self.run_program(program(body, bends=True))
        self.assert_screen(pixels, expected)

    def test_long_colored_spans_allow_audio_irqs_with_bank_zero_restored(self):
        body = store(0xcc26, 2) + r'''
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
'''
        for channel in range(3):
            body += store(0xcc50, channel) + store(0xcc47, channel)
            body += "    ld b,0\n    ld c,143\n    call NativeBendSpan\n"
        body += r'''
    ld a,$32
    ldh [$ff03],a
    di
    xor a
    ldh [$ff07],a
    ldh [$ffff],a
.wait_blank
    ldh a,[$ff44]
    cp 144
    jr c,.wait_blank
'''
        interrupt = r'''
    push af
    ldh a,[$ff4f]
    and 1
    jr z,.bank_ok
    ld [$c101],a
.bank_ok
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
        memory, pixels, writes = self.run_program(program(body, bends=True, interrupt=interrupt))
        markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                   for row in writes if row["address"] == "ff03"}
        audio = [int(row["ticks_8mhz"]) for row in writes
                 if row["address"] == "ff12"
                 and markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
        self.assertGreater(len(audio), 10)
        self.assertEqual(memory[0xc100], len(audio) & 255)
        self.assertEqual(memory[0xc101], 0, "music interrupt observed VRAM bank 1")
        self.assertLessEqual(max(b - a for a, b in zip(audio, audio[1:])), 4300)
        self.assert_screen(pixels, {(channel, y): channel for channel in range(3) for y in range(144)})


if __name__ == "__main__":
    unittest.main()
