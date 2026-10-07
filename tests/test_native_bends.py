"""Run the optional bend renderer on both native Game Boy CPU models."""
import csv
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
AVAILABLE = RUNNER.exists() and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix"))


def store(address, value):
    return f"    ld a,${value:02x}\n    ld [${address:04x}],a\n"


def sample(y, *, active=1, note=40, velocity=1, offset=0x8001,
           row=0, parsed=0, command=0, voice=0):
    result = store(0xcc24, active)
    for address, value in ((0xcc20 + voice, y), (0xc0e8 + voice, note),
                           (0xc2d0 + voice * 2, velocity & 255),
                           (0xc2d1 + voice * 2, velocity >> 8),
                           (0xc337 + voice * 2, offset & 255),
                           (0xc338 + voice * 2, offset >> 8),
                           (0xc16c + voice, row), (0xc0cc + voice, parsed),
                           (0xc365 + voice, command)):
        result += store(address, value)
    return result + "    call NativeBendFrame\n"


def program(body, interrupt="    reti\n"):
    display = (ROOT / "src/native/display.asm").read_text()
    accessors = display.split("NativeStore:\n", 1)[1].split("NativePreparation:", 1)[0]
    return r'''
DEF WaterfallPitchY EQU $cc20
DEF WaterfallActive EQU $cc24
DEF WaterfallHead EQU $cc26
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
    ld a,b
    cp $11
    jr nz,.normal
    ld a,1
    ldh [$ff4d],a
    stop
.normal
    call ClearCanvas
''' + body + r'''
    ld a,$ac
    ld [$c1ff],a
.forever
    jr .forever
ClearCanvas:
    ld hl,$8280
    ld bc,12*288
    xor a
.clear
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr nz,.clear_again
    ret
.clear_again
    xor a
    jr .clear
AudioInterrupt:
''' + interrupt + r'''
INCLUDE "src/native/bend.asm"
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
class NativeBendTests(unittest.TestCase):
    def run_program(self, assembly, *, addresses=()):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        target = Path(directory.name)
        source, obj, rom = (target / name for name in ("bends.asm", "bends.o", "bends.gb"))
        source.write_text(assembly)
        for command in (["rgbasm", "-o", str(obj), str(source)],
                        ["rgblink", "-o", str(rom), str(obj)],
                        ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        results = {}
        for model in ("dmg", "cgb"):
            memory, trace = target / f"{model}.bin", target / f"{model}.tsv"
            command = [str(RUNNER), "--model", model, "--rom", str(rom), "--frames", "300",
                       "--memory-out", str(memory), "--trace", str(trace)]
            for address in addresses:
                command += ["--trace-address", f"{address:04x}"]
            subprocess.run(command, check=True, capture_output=True)
            contents = memory.read_bytes()
            self.assertEqual(contents[0xc1ff], 0xac, f"{model}: test program did not finish")
            with trace.open() as stream:
                results[model] = contents, list(csv.DictReader(stream, delimiter="\t"))
        return results

    def test_spans_and_note_boundaries(self):
        # Output contains connector pixels only; the normal point renderer
        # still draws every current note independently in NativeDisplayFrame.
        cases = [
            ("descending", sample(25), sample(39), range(25, 40)),
            ("ascending", sample(90), sample(73), range(73, 91)),
            ("full height", sample(0), sample(143), range(144)),
            ("effect between pitch and metadata", sample(25, velocity=0, command=0x0a),
             sample(39, velocity=0, command=0x0a), range(25, 40)),
            ("inactive gap", sample(25, active=0), sample(39), ()),
            ("became inactive", sample(25), sample(39, active=0), ()),
            ("new note", sample(25), sample(39, note=41), ()),
            ("same note retrigger", sample(25), sample(39, row=1, parsed=40), ()),
            ("delayed retrigger reset", sample(25, row=1),
             sample(39, row=1, parsed=40, velocity=0, offset=0x8000), ()),
            ("instant legato", sample(25, velocity=0),
             sample(39, velocity=0, offset=0x8100, row=1, parsed=40, command=0x0a), range(25, 40)),
            ("legato to base", sample(25, velocity=0, offset=0x8100),
             sample(39, velocity=0, offset=0x8000, row=1, parsed=40, command=0x0a), range(25, 40)),
            ("last bend step", sample(25),
             sample(39, velocity=0, offset=0x8001), range(25, 40)),
            ("unbent oscillator change", sample(25, velocity=0),
             sample(39, velocity=0), ()),
            ("noise stays discrete", sample(25, active=8, voice=3),
             sample(39, active=8, voice=3), ()),
        ]
        body = store(0xcc26, 2) + store(0xcc47, 3)
        for index, (_, before, after, _) in enumerate(cases):
            body += "    call ClearCanvas\n    call NativeBendInit\n" + before + after
            body += (f"    ld hl,$8280\n    ld de,${0xd000 + index * 288:04x}\n"
                     "    ld bc,288\n"
                     f".copy{index}\n    ld a,[hl+]\n    ld [de],a\n    inc de\n"
                     f"    dec bc\n    ld a,b\n    or c\n    jr nz,.copy{index}\n")
        for model, (memory, _) in self.run_program(program(body)).items():
            for index, (name, _, _, rows) in enumerate(cases):
                with self.subTest(model=model, case=name):
                    expected = bytearray(288)
                    for row in rows:
                        expected[row * 2] = 0x10
                    actual = memory[0xd000 + index * 288:0xd000 + (index + 1) * 288]
                    self.assertEqual(actual, expected)

    def test_all_ring_heads_pixel_phases_and_tonal_voices(self):
        body = ""
        expected = bytearray(12 * 288)
        for head in range(12):
            for phase in range(8):
                voice = (head + phase) % 3
                first, last = phase * 15, phase * 15 + head + 1
                body += "    call NativeBendInit\n" + store(0xcc26, head) + store(0xcc47, phase)
                body += sample(first, voice=voice, active=1 << voice)
                body += sample(last, voice=voice, active=1 << voice)
                for row in range(first, last + 1):
                    expected[((head + 10) % 12) * 288 + row * 2] |= 0x80 >> phase
        # Place the large deterministic driver in another ROM bank; no
        # banking operation is needed because ROM-only maps bank 1 at $4000.
        small = program("    call RingCases\n")
        assembly = small + '\nSECTION "Ring cases", ROMX[$4000], BANK[1]\nRingCases:\n' + body + "    ret\n"
        for model, (memory, _) in self.run_program(assembly).items():
            with self.subTest(model=model):
                self.assertEqual(memory[0x8280:0x9000], expected)

    def test_long_span_keeps_audio_interrupts_available(self):
        body = "    call NativeBendInit\n" + store(0xcc26, 2) + store(0xcc47, 0)
        for voice in range(3):
            body += sample(0, voice=voice, active=7)
        body += r'''
    xor a
    ld [$c100],a
    ldh [$ff04],a
    ldh [$ff0f],a
    ld a,$80
    ldh [$ff26],a
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
        for voice in range(3):
            body += store(0xcc20 + voice, 143)
        body += r'''
    call NativeBendFrame
    ld a,$32
    ldh [$ff03],a
    di
    xor a
    ldh [$ff07],a
    ldh [$ffff],a
'''
        interrupt = r'''
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
'''
        for model, (memory, writes) in self.run_program(program(body, interrupt), addresses=(0xff03,)).items():
            with self.subTest(model=model):
                markers = {int(row["value"], 16): int(row["ticks_8mhz"])
                           for row in writes if row["address"] == "ff03"}
                audio = [int(row["ticks_8mhz"]) for row in writes
                         if row["address"] == "ff12"
                         and markers[0x31] < int(row["ticks_8mhz"]) < markers[0x32]]
                self.assertGreater(len(audio), 10)
                self.assertEqual(memory[0xc100], len(audio))
                # The 4096-cycle timer continues throughout all three spans;
                # the short VRAM accesses add bounded instruction jitter.
                scale = 1 if model == "cgb" else 2
                self.assertLessEqual(max(b - a for a, b in zip(audio, audio[1:])), 4300 * scale)


if __name__ == "__main__":
    unittest.main()
