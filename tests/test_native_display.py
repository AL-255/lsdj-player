"""Hardware scrolling never reveals unfinished native piano-roll rows.

These integration checks use the locally supplied tracker and startup snapshots;
no recovered tracker bytes or song data are stored in the test itself.
"""
import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
TRACKER = ROOT / "rom/lsdj9_4_2.gb"
PROFILE = ROOT / "build/audio-ab/native-triac-execution.tsv"
SNAPSHOTS = ROOT / "build/native-engine/triac"
INPUTS = [RUNNER, TRACKER, PROFILE] + [
    Path(f"{SNAPSHOTS}-{model}.{suffix}.bin")
    for model in ("dmg", "cgb") for suffix in ("wram", "hram", "song")
]


def verify_hardware_scroll(test_case, *, low_range=False):
    """Observe both map rings while SCX moves and the right window stays fixed."""
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        rom = target / "native.gb"
        command = ["python3", str(ROOT / "tools/build_native_player.py"),
                   "--rom", str(TRACKER), "--profile", str(PROFILE),
                   "--snapshot-prefix", str(SNAPSHOTS), "--output", str(rom),
                   "--name", "TRIAC"]
        if low_range:
            command += ["--low-range", "--connect-pitch-bends"]
        subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
        manifest = json.loads(rom.with_suffix(".json").read_text())
        test_case.assertEqual(manifest["low_range"], low_range)
        symbols = {name: int(address.rsplit(":", 1)[-1], 16)
                   for line in rom.with_suffix(".sym").read_text().splitlines()
                   if line and not line.startswith(";")
                   for address, name in [line.split()]}
        execution_symbols = {
            name: (int(address.split(":")[0], 16) if ":" in address else 0,
                   int(address.rsplit(":", 1)[-1], 16))
            for line in rom.with_suffix(".sym").read_text().splitlines()
            if line and not line.startswith(";")
            for address, name in [line.split()]
        }
        note_addresses = (0x9d05, 0x9d45, 0x9d85, 0x9dc5)
        probes = [0xff40, 0xff42, 0xff43, 0xff4a, 0xff4b, 0xff4f,
                  *range(0xff51, 0xff56), 0xcc26, 0xcc00, 0xcc01, 0xcc47, 0xcc48,
                  *note_addresses]
        for model in ("dmg", "cgb"):
            with test_case.subTest(model=model, low_range=low_range):
                trace, state = target / f"{model}.tsv", target / f"{model}.state"
                first_screen = target / f"{model}-first.ppm"
                execution = target / f"{model}-execution.tsv"
                command = [str(RUNNER), "--model", model, "--rom", str(rom),
                           "--frames", "1200", "--trace", str(trace), "--state-out", str(state),
                           "--screen", str(first_screen), "--execution-profile", str(execution)]
                for address in probes:
                    command += ["--trace-address", f"{address:04x}"]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                metadata = json.loads(result.stdout)
                test_case.assertEqual(metadata["native_cgb_mode"], model == "cgb")
                test_case.assertEqual(metadata["double_speed"], model == "cgb")
                test_case.assertGreater(metadata["apu_writes"], 1000)
                with execution.open() as stream:
                    hits = {(int(row["bank"]), int(row["address"], 16)): int(row["count"])
                            for row in csv.DictReader(stream, delimiter="\t")
                            if row["bank"] != "entry"}
                selected = model.upper()
                other = "DMG" if model == "cgb" else "CGB"
                paths = ["NativeLoop", "NativeDisplayFrame"]
                paths += [prefix for prefix in ("NativeLowRangePoints", "NativeBendFrame")
                          if prefix + selected in execution_symbols]
                for prefix in paths:
                    test_case.assertGreater(hits.get(execution_symbols[prefix + selected], 0), 0,
                                            f"{model}: its dedicated {prefix} path did not run")
                    test_case.assertEqual(hits.get(execution_symbols[prefix + other], 0), 0,
                                          f"{model}: executed the other model's {prefix} path")
                with trace.open() as stream:
                    writes = list(csv.DictReader(stream, delimiter="\t"))
                test_case.assertTrue(any(row["address"] == "ff23" for row in writes),
                                     "Noise must remain audible when its plot is hidden")
                observed, heads, map_heads = {}, set(), set()
                ready = started = False
                commits = note_updates = 0
                scx_values = []
                for write in writes:
                    address, value = int(write["address"], 16), int(write["value"], 16)
                    observed[address] = value
                    if not started and address == 0xcc01 and value == 1 and int(write["rom_bank"]) == 5:
                        started = True
                    if not started:
                        continue
                    if address in note_addresses:
                        note_updates += 1
                    if address in (0xff40, 0xff42, 0xff4a, 0xff4b):
                        test_case.fail(f"{model}: runtime changed fixed LCD/window register {address:04x}")
                    if address == 0xff55 and model == "cgb":
                        test_case.assertEqual(value, 0, "Release audio after every short DMA block")
                        test_case.assertEqual(observed[0xff4f] & 1, 0)
                        dest = 0x8000 + (observed[0xff53] & 0x1f) * 256 + (observed[0xff54] & 0xf0)
                        test_case.assertTrue(0x8280 <= dest < 0x9000,
                                             "Hardware scrolling must not DMA whole map rows")
                    if address == 0xff43:
                        if scx_values:
                            test_case.assertEqual(value, (scx_values[-1] + 1) & 255,
                                                 "Each completed frame advances SCX exactly one pixel")
                        scx_values.append(value)
                    if address != 0xcc01:
                        continue
                    if value:
                        ready = True
                        continue
                    if not ready:
                        continue
                    ready = False
                    commits += 1
                    head, map_head, phase = observed[0xcc26], observed[0xcc48], observed[0xcc47]
                    heads.add(head)
                    map_heads.add(map_head)
                    test_case.assertEqual(observed[0xff43], ((map_head * 8) + phase) & 255)
                    test_case.assertEqual(observed[0xff4b], 87)
                    test_case.assertEqual(observed[0xff4a], 0)
                    test_case.assertEqual(observed[0xff42], 0)
                    test_case.assertEqual(observed[0xff40] & 0x6c, 0x60,
                                         "BG uses $9800, window $9c00, keyboard uses 8x8 sprites")
                test_case.assertEqual(heads, set(range(12)))
                test_case.assertEqual(map_heads, set(range(32)))
                test_case.assertGreater(commits, 256)
                test_case.assertIn(0, scx_values, "Hardware SCX must wrap at 256 pixels")
                if model == "cgb":
                    test_case.assertGreater(note_updates, 0)
                else:
                    test_case.assertEqual(note_updates, 0)
                memory = target / f"{model}.bin"
                # Read actual VRAM and OAM in VBlank. CPU bus probes alone
                # also see stores that the PPU could have rejected.
                subprocess.run([
                    str(RUNNER), "--model", model, "--rom", str(rom),
                    "--state-in", str(state), "--frames", "2",
                    "--break-pc", f"0:{symbols['NativeVBlank']}", "--memory-out", str(memory),
                ], capture_output=True, check=True)
                contents = memory.read_bytes()
                head, map_head = contents[0xcc26], contents[0xcc48]
                for row in range(18):
                    for column in range(11):
                        address = 0x9800 + row * 32 + (map_head + column) % 32
                        expected = ((head + column) % 12) * 18 + row + 40
                        test_case.assertEqual(contents[address], expected,
                                             f"{model}: row {row}, map column {(map_head + column) % 32}")
                expected_oam = bytearray(160)
                for stripe in range(18):
                    expected_oam[stripe * 4:stripe * 4 + 4] = bytes(
                        (16 + stripe * 8, 80, (26, 27, 37)[stripe % 3], 0x31))
                test_case.assertEqual(contents[0xfe00:0xfea0], expected_oam)
                for address, label in ((0x9d01, (6, 2, 14)), (0x9d41, (6, 2, 15)),
                                       (0x9d81, (0, 8, 1)), (0x9dc1, (2, 3, 4))):
                    expected = bytes(label) if model == "cgb" else bytes(3)
                    test_case.assertEqual(contents[address:address + 3], expected)
                upper_planes = contents[0x8281:0x9000:2]
                if model == "cgb":
                    test_case.assertTrue(any(upper_planes))
                else:
                    test_case.assertFalse(any(upper_planes))
                # Inspect the actual stationary title, not only WX/SCX.
                second_screen = target / f"{model}-second.ppm"
                subprocess.run([
                    str(RUNNER), "--model", model, "--rom", str(rom),
                    "--state-in", str(state), "--frames", "16", "--screen", str(second_screen),
                ], capture_output=True, check=True)
                panels = []
                for screen in (first_screen, second_screen):
                    pixels = screen.read_bytes().split(b"\n", 3)[3]
                    panels.append(b"".join(pixels[(y * 160 + 80) * 3:(y * 160 + 160) * 3]
                                           for y in range(32)))
                test_case.assertEqual(panels[0], panels[1], "The right-hand title must remain stationary")
                test_case.assertGreater(len(set(panels[0][i:i + 3] for i in range(0, len(panels[0]), 3))), 1,
                                        "The stationary title must actually be visible")


@unittest.skipUnless(all(path.exists() for path in INPUTS)
                     and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix")),
                     "Prepare the local native TRIAC snapshots and SameBoy harness first")
class NativeDisplayTests(unittest.TestCase):
    def test_hardware_scroll_wraps_both_rings_and_keeps_the_window_fixed(self):
        verify_hardware_scroll(self)


@unittest.skipUnless(RUNNER.exists()
                     and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix")),
                     "Build the SameBoy harness and install RGBDS first")
class NativeAudioPriorityTests(unittest.TestCase):
    def test_audio_interrupt_can_consume_vblank_and_defer_scroll(self):
        """A heavy audio IRQ preempts commit preparation and forces a skip."""
        source = (ROOT / "src/native/display.asm").read_text()
        scroll = source.split("NativeDisplayScroll::", 1)[1].split(
            "; HL=11-bit oscillator frequency", 1)[0]
        program = r'''
DEF NativeFrameReady EQU $cc01
DEF NativePrepareIndex EQU $cc00
DEF WaterfallClearCount EQU 12
DEF WaterfallMapCount EQU 18
DEF WaterfallPixelPhase EQU $cc47
DEF WaterfallLCD EQU $cc27
DEF WaterfallHead EQU $cc26
DEF WaterfallNextLCD EQU $cc46
DEF WaterfallNextHead EQU $cc45
DEF NativeMapHead EQU $cc48
DEF NativeScrollX EQU $cc4b
SECTION "Timer interrupt", ROM0[$50]
    jp StressAudio
SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150-@,0
SECTION "Program", ROM0[$150]
Start:
    di
    ld sp,$fffe
    cp $11
    jr nz,.normal
    ld a,1
    ldh [$ff4d],a
    stop
.normal
    xor a
    ldh [$ffff],a
    ldh [$ff40],a
    ldh [$ff07],a
    ldh [$ff0f],a
    ld [WaterfallHead],a
    ld a,31
    ld [NativeMapHead],a
    ld a,1
    ld [WaterfallNextHead],a
    ld [NativeFrameReady],a
    ld a,30
    ld [NativePrepareIndex],a
    ld a,7
    ld [WaterfallPixelPhase],a
    ld a,$f3
    ld [WaterfallLCD],a
    ldh [$ff40],a
    ld a,255
    ld [NativeScrollX],a
    ldh [$ff43],a
    ld a,87
    ldh [$ff4b],a
    ld a,4
    ldh [$ffff],a
    ei
.wait
    ldh a,[$ff44]
    cp 144
    jr nz,.wait
    ld a,$31
    ldh [$ff03],a
    xor a
    ldh [$ff04],a
    ldh [$ff06],a
    ld a,$f8
    ldh [$ff05],a
    ld a,5
    ldh [$ff07],a
    call NativeDisplayScroll
    ld a,$32
    ldh [$ff03],a
    ld a,[NativeFrameReady]
    ld [$c100],a
    ld a,[WaterfallPixelPhase]
    ld [$c101],a
    ld a,[WaterfallHead]
    ld [$c102],a
    ld a,[NativeMapHead]
    ld [$c103],a
.next_blank
    ldh a,[$ff44]
    cp 144
    jr nz,.next_blank
    call NativeDisplayScroll
    ld a,$33
    ldh [$ff03],a
    ld a,[NativeFrameReady]
    ld [$c104],a
    ld a,[WaterfallPixelPhase]
    ld [$c105],a
    ld a,[WaterfallHead]
    ld [$c106],a
    ld a,[NativeMapHead]
    ld [$c107],a
.forever
    jr .forever
StressAudio:
    push af
    xor a
    ldh [$ff07],a
    ld a,$f0
    ldh [$ff12],a
.busy
    ldh a,[$ff44]
    cp 152
    jr c,.busy
    ld a,$a0
    ldh [$ff12],a
    pop af
    reti
NativeDisplayScroll::
'''+scroll
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            assembly, obj, rom = (target / name for name in ("stress.asm", "stress.o", "stress.gb"))
            assembly.write_text(program)
            for command in (["rgbasm", "-o", str(obj), str(assembly)],
                            ["rgblink", "-o", str(rom), str(obj)],
                            ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
                subprocess.run(command, capture_output=True, check=True)
            for model in ("dmg", "cgb"):
                with self.subTest(model=model):
                    trace, memory = target / f"{model}.tsv", target / f"{model}.bin"
                    subprocess.run([
                        str(RUNNER), "--model", model, "--rom", str(rom),
                        "--frames", "300", "--trace", str(trace),
                        "--trace-address", "ff03", "--trace-address", "ff40",
                        "--trace-address", "ff4b", "--trace-address", "ff43", "--memory-out", str(memory),
                    ], capture_output=True, check=True)
                    # Busy audio retains the completed frame; the following
                    # available VBlank commits it once, with no catch-up queue.
                    self.assertEqual(memory.read_bytes()[0xc100:0xc108], bytes((1, 7, 0, 31, 0, 0, 1, 0)))
                    with trace.open() as stream:
                        writes = list(csv.DictReader(stream, delimiter="\t"))
                    markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                               for row in writes if row["address"] == "ff03"}
                    during_audio = [row for row in writes
                                    if markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
                    self.assertEqual([row["value"] for row in during_audio if row["address"] == "ff12"],
                                     ["f0", "a0"])
                    self.assertFalse([row for row in during_audio if row["address"] in ("ff40", "ff4b", "ff43")])
                    committed = [row for row in writes
                                 if markers[0x32] < int(row["ticks_8mhz"]) < markers[0x33]
                                 and row["address"] == "ff43"]
                    self.assertEqual([row["value"] for row in committed], ["00"])


if __name__ == "__main__":
    unittest.main()
