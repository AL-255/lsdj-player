"""Native interpreter map swaps never reveal unfinished piano-roll rows.

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


@unittest.skipUnless(all(path.exists() for path in INPUTS)
                     and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix")),
                     "Prepare the local native TRIAC snapshots and SameBoy harness first")
class NativeDisplayTests(unittest.TestCase):
    def test_map_swaps_cover_both_halves_and_ring_wraps(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            rom = target / "native.gb"
            subprocess.run([
                "python3", str(ROOT / "tools/build_native_player.py"),
                "--rom", str(TRACKER), "--profile", str(PROFILE),
                "--snapshot-prefix", str(SNAPSHOTS), "--output", str(rom),
                "--name", "TRIAC",
            ], cwd=ROOT, capture_output=True, check=True)
            symbols = {name: int(address.rsplit(":", 1)[-1], 16)
                       for line in rom.with_suffix(".sym").read_text().splitlines()
                       if line and not line.startswith(";")
                       for address, name in [line.split()]}
            # Observe both ends of rows above/below the old nine-row cutoff,
            # including the bottom edge. Stay within the harness's 32 probes.
            rows = (0, 8, 9, 17)
            columns = (0, 10)
            note_addresses = (0x9915, 0x9955, 0x9995, 0x99d5)
            probes = [0xff40, 0xcc26, 0xcc00, *note_addresses] + [
                base + row * 32 + column
                for base in (0x9800, 0x9c00) for row in rows for column in columns
            ]
            for model in ("dmg", "cgb"):
                with self.subTest(model=model):
                    trace = target / f"{model}.tsv"
                    memory_path = target / f"{model}.bin"
                    state_path = target / f"{model}.state"
                    command = [str(RUNNER), "--model", model, "--rom", str(rom),
                               "--frames", "1200", "--trace", str(trace),
                               "--state-out", str(state_path)]
                    for address in probes:
                        command += ["--trace-address", f"{address:04x}"]
                    result = subprocess.run(command, capture_output=True, text=True, check=True)
                    metadata = json.loads(result.stdout)
                    self.assertEqual(metadata["native_cgb_mode"], model == "cgb")
                    self.assertEqual(metadata["double_speed"], model == "cgb")
                    with trace.open() as stream:
                        writes = list(csv.DictReader(stream, delimiter="\t"))
                    memory = {}
                    started = False
                    heads = set()
                    swaps = 0
                    note_updates = 0
                    for write in writes:
                        address = int(write["address"], 16)
                        value = int(write["value"], 16)
                        memory[address] = value
                        if address == 0xcc00 and value:
                            started = True
                        if started and address in note_addresses:
                            note_updates += 1
                        # Foreground scrolling publishes the new head after
                        # its short atomic LCDC/WX commit; inspect that point.
                        if address != 0xcc26 or not started:
                            continue
                        head = memory[0xcc26]
                        heads.add(head)
                        swaps += 1
                        base = 0x9c00 if memory[0xff40] & 0x40 else 0x9800
                        for row in rows:
                            for column in columns:
                                expected = ((head + column) % 12) * 18 + row + 40
                                self.assertEqual(
                                    memory.get(base + row * 32 + column), expected,
                                    f"{model}: unfinished map at tick {write['ticks_8mhz']}, "
                                    f"head {head}, row {row}, column {column}",
                                )
                    # The retired tracker watchdog must never DMA over the
                    # fixed keyboard sprites, including in normal-speed DMG.
                    # Read OAM during VBlank; a bus read in LCD modes 2/3
                    # correctly returns $ff even when the sprite RAM is fine.
                    subprocess.run([
                        str(RUNNER), "--model", model, "--rom", str(rom),
                        "--state-in", str(state_path), "--frames", "2",
                        "--break-pc", f"0:{symbols['NativeVBlank']}",
                        "--memory-out", str(memory_path),
                    ], capture_output=True, check=True)
                    expected_oam = bytearray(160)
                    for stripe in range(9):
                        expected_oam[stripe * 4:stripe * 4 + 4] = bytes(
                            (16 + stripe * 16, 80, 38, 0x11))
                    for pen in range(8):
                        expected_oam[0x24 + pen * 4:0x28 + pen * 4] = bytes((0, 0, 26, 0))
                    for stripe in range(12):
                        expected_oam[0x48 + stripe * 4:0x4c + stripe * 4] = bytes(
                            (16 + stripe * 12, 160, 26, 0x31))
                    actual_memory = memory_path.read_bytes()
                    self.assertEqual(actual_memory[0xfe00:0xfea0], expected_oam)
                    if model == "dmg":
                        self.assertEqual(note_updates, 0)
                        for address in (0x9911, 0x9951, 0x9991, 0x99d1):
                            self.assertEqual(actual_memory[address:address + 8], bytes(8))
                    else:
                        self.assertGreater(note_updates, 0)
                        for address, label in ((0x9911, (6, 2, 14)), (0x9951, (6, 2, 15)),
                                               (0x9991, (0, 8, 1)), (0x99d1, (2, 3, 4))):
                            self.assertEqual(actual_memory[address:address + 3], bytes(label))
                    # Bus probes see attempted stores before PPU restrictions.
                    # Also inspect actual VRAM while the LCD permits reads.
                    head = actual_memory[0xcc26]
                    base = 0x9c00 if actual_memory[0xff40] & 0x40 else 0x9800
                    for row in range(18):
                        expected = bytes(((head + column) % 12) * 18 + row + 40
                                         for column in range(11))
                        self.assertEqual(actual_memory[base + row * 32:base + row * 32 + 11],
                                         expected, f"{model}: actual VRAM row {row}")
                    self.assertEqual(heads, set(range(12)))
                    self.assertGreater(swaps, 24)



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
    ld a,1
    ld [WaterfallNextHead],a
    ld [NativeFrameReady],a
    ld a,30
    ld [NativePrepareIndex],a
    ld a,7
    ld [WaterfallPixelPhase],a
    ld a,$f7
    ld [WaterfallNextLCD],a
    ld a,$b7
    ld [WaterfallLCD],a
    ldh [$ff40],a
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
    ld a,$f0
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
.next_blank
    ldh a,[$ff44]
    cp 144
    jr nz,.next_blank
    call NativeDisplayScroll
    ld a,$33
    ldh [$ff03],a
    ld a,[NativeFrameReady]
    ld [$c103],a
    ld a,[WaterfallPixelPhase]
    ld [$c104],a
    ld a,[WaterfallHead]
    ld [$c105],a
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
                        "--trace-address", "ff4b", "--memory-out", str(memory),
                    ], capture_output=True, check=True)
                    # Busy audio retains the completed frame; the following
                    # available VBlank commits it once, with no catch-up queue.
                    self.assertEqual(memory.read_bytes()[0xc100:0xc106], bytes((1, 7, 0, 0, 0, 1)))
                    with trace.open() as stream:
                        writes = list(csv.DictReader(stream, delimiter="\t"))
                    markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                               for row in writes if row["address"] == "ff03"}
                    during_audio = [row for row in writes
                                    if markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
                    self.assertEqual([row["value"] for row in during_audio if row["address"] == "ff12"],
                                     ["f0", "a0"])
                    self.assertFalse([row for row in during_audio if row["address"] in ("ff40", "ff4b")])
                    committed = [row for row in writes
                                 if markers[0x32] < int(row["ticks_8mhz"]) < markers[0x33]
                                 and row["address"] == "ff4b"]
                    self.assertEqual([row["value"] for row in committed], ["57"])


if __name__ == "__main__":
    unittest.main()
