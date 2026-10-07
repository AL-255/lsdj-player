"""Run the optional doubled lower pitch range on both Game Boy PPUs."""
import csv
import itertools
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_native_colors import store
from test_native_display import INPUTS, verify_hardware_scroll

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
AVAILABLE = RUNNER.exists() and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix"))


def points(column, phase, pitches, active=15):
    # The eleventh column receives the newest pixel; one more stays hidden.
    head = (column - 10) % 12
    result = store(0xcc26, head) + store(0xcc47, phase) + store(0xcc24, active)
    for channel, pitch in enumerate(pitches):
        result += store(0xcc20 + channel, pitch)
    return result + "    call TestLowRangePoints\n"


def span(column, phase, before, after, channel=0):
    return (store(0xcc26, (column - 10) % 12) + store(0xcc47, phase)
            + store(0xcc50, channel)
            + f"    ld b,{before}\n    ld c,{after}\n    call TestBendSpan\n")


def assembly(body, interrupt="    reti\n"):
    source = (ROOT / "src/native/display.asm").read_text()
    accessors = source.split("NativeStore:\n", 1)[1].split("NativePreparation:", 1)[0]
    low_range = (ROOT / "src/native/low_range.asm").read_text()
    maps = ""
    for base in (0x9800, 0x9c00):
        for row in range(18):
            for column in range(11):
                maps += store(base + row * 32 + column, 40 + column * 18 + row)
        for channel in range(4):
            maps += store(base + 12 + channel, 20 + channel)
    references = ""
    for channel, code in enumerate((1, 2, 3, 3)):
        for row in range(8):
            references += store(0x8140 + channel * 16 + row * 2, 255 if code & 1 else 0)
            references += store(0x8141 + channel * 16 + row * 2, 255 if code & 2 else 0)
    return r'''
DEF NATIVE_LOW_RANGE EQU 1
DEF NATIVE_CHANNEL_COLORS EQU 1
DEF WaterfallPatternBase EQU $8280
DEF WaterfallRingColumns EQU 12
DEF WaterfallPitchY EQU $cc20
DEF WaterfallActive EQU $cc24
DEF WaterfallHead EQU $cc26
DEF WaterfallLCD EQU $cc27
DEF WaterfallColumnBase EQU $cc40
DEF WaterfallMapSource EQU $cc42
DEF WaterfallMapDestHigh EQU $cc44
DEF WaterfallPixelPhase EQU $cc47
DEF NativeMapColumn EQU $cc49
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
    cp $11
    ld a,0
    jr nz,.model
    inc a
.model
    ldh [$ff90],a
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
    ld hl,$8000
    ld bc,$2000
    call Clear
    ld hl,$9000
    ld bc,$800
    call Canary
    ld a,$e4
    ldh [$ff47],a
    ldh a,[$ff90]
    or a
    jr z,.color_ready
    ld a,1
    ldh [$ff4d],a
    stop
    ldh [$ff4f],a
    ld hl,$8000
    ld bc,$2000
    call Clear
    ld hl,$9000
    ld bc,216
    call Canary
    xor a
    ldh [$ff4f],a
    call NativeColorInit
    ld a,1
    ldh [$ff4f],a
    FOR map,2
        FOR channel,4
            ld a,1 + (channel == 3)
            ld [$980c + map * $400 + channel],a
        ENDR
    ENDR
    xor a
    ldh [$ff4f],a
.color_ready
''' + maps + references + body + r'''
    ldh a,[$ff40]
    bit 7,a
    jr z,.lcd_off
.snapshot_blank
    ldh a,[$ff44]
    cp 144
    jr c,.snapshot_blank
.lcd_off
    di
    xor a
    ldh [$ff40],a
    ldh [$ff07],a
    ldh [$ffff],a
    ld hl,$8280
    ld de,$c300
    ld bc,12*288
    call Copy
    ld hl,$9000
    ld de,$d100
    ld bc,$800
    call Copy
    ldh a,[$ff90]
    or a
    jr z,.snapshot_ready
    ld a,1
    ldh [$ff4f],a
    ld hl,$9000
    ld de,$d900
    ld bc,216
    call Copy
    xor a
    ldh [$ff4f],a
.snapshot_ready
    ld a,$ac
    ld [$c1ff],a
    ld a,$91
    ldh [$ff40],a
.forever
    jr .forever
Clear:
    xor a
    jr Fill
Canary:
    ld a,$a5
Fill:
    ld [hl+],a
    dec bc
    push af
    ld a,b
    or c
    jr z,.done
    pop af
    jr Fill
.done
    pop af
    ret
Copy:
    ld a,[hl+]
    ld [de],a
    inc de
    dec bc
    ld a,b
    or c
    jr nz,Copy
    ret
''' + 'AudioInterrupt:\n' + interrupt + low_range + r'''
TestLowRangePoints:
    ldh a,[$ff90]
    or a
    jp z,NativeLowRangePointsDMG
    jp NativeLowRangePointsCGB
TestBendSpan:
    ldh a,[$ff90]
    or a
    jp z,NativeBendSpanDMG
    jp NativeBendSpanCGB

INCLUDE "src/native/color.asm"
INCLUDE "src/native/bend.asm"
INCLUDE "src/native/color_map.asm"
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
class NativeLowRangeTests(unittest.TestCase):
    def run_program(self, source):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            asm, obj, rom = (target / filename for filename in ("low.asm", "low.o", "low.gb"))
            asm.write_text(source)
            for command in (["rgbasm", "-o", str(obj), str(asm)],
                            ["rgblink", "-o", str(rom), str(obj)],
                            ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            results = {}
            for model in ("dmg", "cgb"):
                memory, screen, trace = (target / f"{model}.{suffix}" for suffix in ("bin", "ppm", "tsv"))
                result = subprocess.run([
                    str(RUNNER), "--model", model, "--rom", str(rom), "--frames", "300",
                    "--memory-out", str(memory), "--screen", str(screen),
                    "--trace", str(trace), "--trace-address", "ff03",
                ], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                contents = memory.read_bytes()
                self.assertEqual(contents[0xc1ff], 0xac, f"{model}: test program did not finish")
                self.assertEqual(contents[0xd100:0xd900], bytes([0xa5]) * 0x800,
                                 f"{model}: rendering wrote beyond the canvas")
                if model == "cgb":
                    self.assertEqual(contents[0xd900:0xd9d8], bytes([0xa5]) * 216,
                                     "Fixed tonal palettes must not touch per-tile metadata")
                magic, dimensions, maximum, pixels = screen.read_bytes().split(b"\n", 3)
                self.assertEqual((magic, dimensions, maximum), (b"P6", b"160 144", b"255"))
                with trace.open() as stream:
                    writes = list(csv.DictReader(stream, delimiter="\t"))
                results[model] = contents, pixels, writes
            return results

    def assert_canvas(self, model, memory, pixels, expected):
        def pixel(x, y):
            offset = (y * 160 + x) * 3
            return pixels[offset:offset + 3]
        background = pixel(87, 143)
        colors = [pixel((12 + channel) * 8, 0) for channel in range(3)]
        if model == "cgb":
            self.assertEqual(len(set(colors)), 3)
        patterns = bytearray(12 * 288)
        for (x, y), channel in expected.items():
            code = channel + 1 if model == "cgb" else 1
            offset = (x // 8) * 288 + y * 2
            if code & 1:
                patterns[offset] |= 0x80 >> (x % 8)
            if code & 2:
                patterns[offset + 1] |= 0x80 >> (x % 8)
        self.assertEqual(memory[0xc300:0xd080], patterns, f"{model}: actual tile bitplanes")
        for y in range(144):
            for x in range(88):
                channel = expected.get((x, y))
                color = background if channel is None else colors[channel if model == "cgb" else 0]
                self.assertEqual(pixel(x, y), color, f"{model}: pixel ({x}, {y})")

    def test_lower_half_points_are_doubled_and_noise_is_not_plotted(self):
        body, expected = "", {}
        cases = ((71, 0), (72, 0), (73, 1), (143, 2), (0, 2), (142, 1))
        for phase, (pitch, channel) in enumerate(cases):
            pitches = [0, 0, 0, 100]
            pitches[channel] = pitch
            body += points(0, phase, pitches, active=(1 << channel) | 8)
            if pitch >= 72:
                for y in ((pitch - 72) * 2, (pitch - 72) * 2 + 1):
                    expected[(phase, y)] = channel
        # All four active voices, including a noise pitch that would be
        # visible if the old fourth point entry were still being called.
        body += points(1, 7, (72, 90, 143, 100))
        for channel, pitch in enumerate((72, 90, 143)):
            for y in ((pitch - 72) * 2, (pitch - 72) * 2 + 1):
                expected[(15, y)] = channel
        for model, (memory, pixels, _) in self.run_program(assembly(body)).items():
            with self.subTest(model=model):
                self.assert_canvas(model, memory, pixels, expected)

    def test_bends_clip_both_directions_and_fill_two_rows_per_pitch(self):
        body, expected = "", {}
        cases = ((0, 71), (71, 72), (72, 71), (0, 143), (143, 0),
                 (140, 143), (143, 140), (72, 72))
        for phase, (before, after) in enumerate(cases):
            channel = phase % 3
            body += span(0, phase, before, after, channel)
            if before != after:
                for pitch in range(max(72, min(before, after)), max(before, after) + 1):
                    for y in ((pitch - 72) * 2, (pitch - 72) * 2 + 1):
                        expected[(phase, y)] = channel
        for model, (memory, pixels, _) in self.run_program(assembly(body)).items():
            with self.subTest(model=model):
                self.assert_canvas(model, memory, pixels, expected)

    def test_tonal_overlap_preserves_priority_and_all_ring_columns(self):
        body, expected = "", {}
        for column in range(12):
            for phase in range(8):
                pitch = 72 + (column * 9 + phase) % 72
                orders = tuple(itertools.permutations(range(3)))
                for channel in orders[(column * 8 + phase) % len(orders)]:
                    body += points(column, phase, (pitch, pitch, pitch, pitch), active=(1 << channel) | 8)
                for y in ((pitch - 72) * 2, (pitch - 72) * 2 + 1):
                    expected[(column * 8 + phase, y)] = 0
        # The twelfth column is hidden, but its patterns must still be right.
        for model, (memory, pixels, _) in self.run_program(assembly(body)).items():
            with self.subTest(model=model):
                self.assert_canvas(model, memory, pixels, expected)


    def test_full_height_spans_keep_audio_running_with_the_lcd_active(self):
        body = r"""
    xor a
    ld [$c100],a
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
"""
        expected = {}
        for channel in range(3):
            body += span(0, channel, 0, 143, channel)
            for y in range(144):
                expected[(channel, y)] = channel
        body += r"""
    ld a,$32
    ldh [$ff03],a
"""
        interrupt = r"""
    push af
    ld a,[$c100]
    inc a
    ld [$c100],a
    and 1
    swap a
    or $e0
    ldh [$ff12],a
    pop af
    reti
"""
        for model, (memory, pixels, writes) in self.run_program(assembly(body, interrupt)).items():
            with self.subTest(model=model):
                self.assert_canvas(model, memory, pixels, expected)
                markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                           for row in writes if row["address"] == "ff03"}
                audio = [int(row["ticks_8mhz"]) for row in writes
                         if row["address"] == "ff12"
                         and markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
                self.assertGreater(len(audio), 10, "Drawing must allow repeated audio interrupts")
                ticks_per_cpu_cycle = 1 if model == "cgb" else 2
                gaps = [(later - earlier) / ticks_per_cpu_cycle
                        for earlier, later in zip(audio, audio[1:])]
                self.assertLessEqual(max(gaps), 4300,
                                     "A 4096-cycle audio timer must not wait for the bend span")


@unittest.skipUnless(AVAILABLE and all(path.exists() for path in INPUTS),
                     "Prepare the local native TRIAC snapshots first")
class NativeLowRangeIntegrationTests(unittest.TestCase):
    def test_low_range_keeps_all_hardware_scroll_rows_and_channel_audio(self):
        verify_hardware_scroll(self, low_range=True)
