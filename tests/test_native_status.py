"""Check status text and skipped VRAM writes on the real Game Boy CPU."""
import csv
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
AVAILABLE = RUNNER.exists() and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix"))
BPM_CELLS = tuple(range(0x9895, 0x9898))
NOTE_CELLS = tuple(0x9915 + channel * 64 + cell for channel in range(4) for cell in range(4))
LABEL_CELLS = tuple(0x9911 + channel * 64 + cell for channel in range(4) for cell in range(3))
OBSERVED_CELLS = BPM_CELLS + NOTE_CELLS + LABEL_CELLS
CGB_LABELS = bytes((6, 2, 14, 6, 2, 15, 0, 8, 1, 2, 3, 4))


def store(address, value):
    return f"    ld a,${value:02x}\n    ld [${address:04x}],a\n"


def sample(body, index):
    return (store(0xff03, index + 1) + body
            + f"    ld de,${0xd000 + index * len(OBSERVED_CELLS):04x}\n"
            + "    call SnapshotStatus\n")


def program(body):
    display = (ROOT / "src/native/display.asm").read_text()
    definitions = "\n".join(re.findall(r"^DEF Native(?:BPM\w*|Previous\w*).*", display, re.M))
    bpm = "NativeBPM:\n" + display.split("NativeBPM:\n", 1)[1].split("NativePreparation:", 1)[0]
    status = "NativeSongInfoInit:\n" + display.split("NativeSongInfoInit:\n", 1)[1]
    status = status.rsplit("\nIF DEF(NATIVE_CONNECT_PITCH_BENDS)", 1)[0]
    glyph_macro = (ROOT / "src/exact_ui.asm").read_text().split("MACRO ExactUIGlyph\n", 1)[1].split("ENDM", 1)[0]
    addresses = "    dw " + ",".join(f"${address:04x}" for address in OBSERVED_CELLS)
    return definitions + r'''
DEF WaterfallPitchY EQU $cc20
DEF WaterfallActive EQU $cc24
DEF WaterfallLCD EQU $cc27
DEF WaterfallColumnBase EQU $cc40
DEF WaterfallMapDestHigh EQU $cc44
MACRO ExactUIGlyph
''' + glyph_macro + r'''ENDM
SECTION "Header", ROM0[$100]
    nop
    jp Start
    ds $150-@,0
SECTION "Program", ROM0[$150]
Start:
    di
    ld sp,$fffe
    ld b,a
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
    ldh [$ff90],a
    ld a,b
    cp $11
    jr nz,.hardware_ready
    ld a,1
    ldh [$ff90],a
    ldh [$ff4d],a
    stop
.hardware_ready
    ld hl,$8000
    ld bc,$2000
.clear
    xor a
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.clear
    call NativeSongInfoInit
    ld a,$91
    ldh [$ff40],a
''' + body + r'''
    ld a,$ff
    ldh [$ff03],a
.finish_blank
    ldh a,[$ff44]
    cp 144
    jr c,.finish_blank
    xor a
    ldh [$ff40],a
    ld a,$ac
    ld [$c1ff],a
.forever
    jr .forever
SnapshotStatus:
    ld hl,ObservedAddresses
    ld b,31
.cell
    ld a,[hl+]
    ld c,a
    ld a,[hl+]
    push hl
    ld h,a
    ld l,c
    call NativeRead
    ld [de],a
    inc de
    pop hl
    dec b
    jr nz,.cell
    ret
ObservedAddresses:
''' + addresses + "\n" + bpm + status + '\nINCLUDE "src/native/color.asm"\n'


@unittest.skipUnless(AVAILABLE, "Build the SameBoy harness and install RGBDS first")
class NativeStatusTests(unittest.TestCase):
    def run_program(self, body, models):
        results = {}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source, obj, rom = (target / name for name in ("status.asm", "status.o", "status.gb"))
            source.write_text(program(body))
            for command in (["rgbasm", "-o", str(obj), str(source)],
                            ["rgblink", "-o", str(rom), str(obj)],
                            ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for model in models:
                memory, trace = target / f"{model}.bin", target / f"{model}.tsv"
                command = [str(RUNNER), "--model", model, "--rom", str(rom), "--frames", "300",
                           "--memory-out", str(memory), "--trace", str(trace)]
                for address in (0xff03, *OBSERVED_CELLS):
                    command += ["--trace-address", f"{address:04x}"]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                contents = memory.read_bytes()
                self.assertEqual(contents[0xc1ff], 0xac, f"{model}: status program did not finish")
                with trace.open() as stream:
                    trace_rows = list(csv.DictReader(stream, delimiter="\t"))
                phases, phase = {}, None
                for row in trace_rows:
                    address, value = int(row["address"], 16), int(row["value"], 16)
                    if address == 0xff03:
                        phase = value
                        phases[phase] = []
                    elif phase is not None and address in OBSERVED_CELLS:
                        phases[phase].append((address, value))
                results[model] = contents, phases
        return results

    def snapshot(self, memory, index):
        first = 0xd000 + index * len(OBSERVED_CELLS)
        return memory[first:first + len(OBSERVED_CELLS)]

    def test_tempo_changes_and_repeated_values_on_both_models(self):
        # Original LSDj encodes 40..295 BPM in one byte: 0..39 mean 256..295.
        # Starting at 255 also catches using a valid tempo as an invalid marker.
        cases = ((255, "255"), (255, "255"), (0, "256"), (0, "256"),
                 (39, "295"), (39, "295"), (40, "040"), (40, "040"),
                 (120, "120"), (120, "120"), (44, "044"), (44, "044"))
        body = "".join(sample(store(0xc529, encoded) + "    call NativeBPM\n", index)
                       for index, (encoded, _) in enumerate(cases))
        for model, (memory, phases) in self.run_program(body, ("dmg", "cgb")).items():
            for index, (_, text) in enumerate(cases):
                with self.subTest(model=model, sample=index, tempo=text):
                    digits = bytes(13 + int(digit) for digit in text)
                    expected_writes = list(zip(BPM_CELLS, digits)) if index % 2 == 0 else []
                    self.assertEqual(phases[index + 1], expected_writes)
                    snapshot = self.snapshot(memory, index)
                    self.assertEqual(snapshot[:3], digits)
                    self.assertEqual(snapshot[3:19], bytes(16))
                    self.assertEqual(snapshot[19:], CGB_LABELS if model == "cgb" else bytes(12))
            self.assertEqual(memory[0x9891:0x9894], bytes((24, 6, 1)))

    def test_note_changes_silence_and_octave_width_preserve_labels(self):
        # Explicit glyph expectations exercise one/two-digit octave changes,
        # sharps, all four channel caches, and pitch movement during silence.
        silence = bytes(4)
        b12, c11 = bytes((24, 0, 14, 15)), bytes((5, 0, 14, 14))
        c4, cs4, c1 = bytes((5, 0, 17, 0)), bytes((5, 23, 17, 0)), bytes((5, 0, 14, 0))
        cases = (
            (0, (0, 23, 107, 106), (silence,) * 4, (0, 1, 2, 3)),
            (15, (0, 23, 107, 106), (b12, c11, c4, cs4), (0, 1, 2, 3)),
            (15, (0, 23, 107, 106), (b12, c11, c4, cs4), ()),
            (15, (107, 23, 107, 106), (c4, c11, c4, cs4), (0,)),
            (12, (0, 23, 107, 106), (silence, silence, c4, cs4), (0, 1)),
            (12, (143, 0, 107, 106), (silence, silence, c4, cs4), ()),
            (15, (143, 0, 107, 106), (c1, b12, c4, cs4), (0, 1)),
            (0, (143, 0, 107, 106), (silence,) * 4, (0, 1, 2, 3)),
            (0, (0, 0, 0, 143), (silence,) * 4, ()),
            (8, (0, 0, 0, 143), (silence, silence, silence, c1), (3,)),
        )
        body = ""
        for index, (active, pitches, _, _) in enumerate(cases):
            update = store(0xcc24, active)
            update += "".join(store(0xcc20 + channel, pitch) for channel, pitch in enumerate(pitches))
            body += sample(update + "    call NativeNotes\n", index)
        memory, phases = self.run_program(body, ("cgb",))["cgb"]
        for index, (_, _, values, changed) in enumerate(cases):
            with self.subTest(sample=index):
                expected_writes = [(0x9915 + channel * 64 + cell, value)
                                   for channel in changed for cell, value in enumerate(values[channel])]
                self.assertEqual(phases[index + 1], expected_writes)
                snapshot = self.snapshot(memory, index)
                self.assertEqual(snapshot[3:19], b"".join(values))
                self.assertEqual(snapshot[19:], CGB_LABELS)


if __name__ == "__main__":
    unittest.main()
