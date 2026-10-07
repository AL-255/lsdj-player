"""Screenshots must contain a completed frame without extending audio capture."""
import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
AVAILABLE = RUNNER.exists() and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix"))

# Each VBlank changes the entire next frame from white to black or back.
# Reading SameBoy's live output in the middle of scanout would therefore
# combine the new color above LY with the previous frame's color below it.
PROGRAM = r'''
DEF FrameCounter EQU $c100
DEF ColorHardware EQU $c101
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
    ld [ColorHardware],a
    xor a
    ldh [$ffff],a
    ldh [$ff07],a
.blank
    ldh a,[$ff44]
    cp 144
    jr c,.blank
    xor a
    ldh [$ff40],a
    ldh [$ff42],a
    ldh [$ff43],a
    ldh [$ff4f],a
    ld [FrameCounter],a
    ld hl,$8000
    ld bc,$2000
.clear
    ld [hl+],a
    dec bc
    ld a,b
    or c
    jr z,.cleared
    xor a
    jr .clear
.cleared
    ld a,$80
    ldh [$ff26],a
    ld a,$77
    ldh [$ff24],a
    ld a,$11
    ldh [$ff25],a
    ld a,$80
    ldh [$ff11],a
    ld a,$f0
    ldh [$ff12],a
    ld a,$60
    ldh [$ff13],a
    ld a,$87
    ldh [$ff14],a
    call PaintNextFrame
    ld a,$91
    ldh [$ff40],a
.visible
    ldh a,[$ff44]
    cp 144
    jr nc,.visible
.next_blank
    ldh a,[$ff44]
    cp 144
    jr c,.next_blank
    ld hl,FrameCounter
    inc [hl]
    call PaintNextFrame
    ld a,[FrameCounter]
    ldh [$ff03],a
    add $60
    ldh [$ff13],a
    jr .visible
PaintNextFrame:
    ld a,[FrameCounter]
    and 1
    ld b,$ff
    jr z,.shade
    ld b,0
.shade
    ld a,[ColorHardware]
    or a
    jr nz,.color
    ld a,b
    ldh [$ff47],a
    ret
.color
    ld a,$80
    ldh [$ff68],a
    ld a,b
    ldh [$ff69],a
    ldh [$ff69],a
    ret
'''


@unittest.skipUnless(AVAILABLE, "Build the SameBoy harness and install RGBDS first")
class CaptureScreenTests(unittest.TestCase):
    def test_mid_scanout_uses_latest_complete_frame_and_preserves_capture_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source, obj, rom = (target / name for name in ("frames.asm", "frames.o", "frames.gb"))
            source.write_text(PROGRAM)
            for command in (["rgbasm", "-o", str(obj), str(source)],
                            ["rgblink", "-o", str(rom), str(obj)],
                            ["rgbfix", "-v", "-C", "-p", "0", str(rom)]):
                subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
            for model in ("dmg", "cgb"):
                with self.subTest(model=model):
                    boundaries = target / f"{model}-boundaries.tsv"
                    subprocess.run([
                        str(RUNNER), "--model", model, "--rom", str(rom), "--frames", "340",
                        "--trace", str(boundaries), "--trace-address", "ff03",
                    ], capture_output=True, check=True)
                    with boundaries.open() as stream:
                        markers = [int(row["ticks_8mhz"]) for row in csv.DictReader(stream, delimiter="\t")
                                   if row["address"] == "ff03"]
                    self.assertGreater(len(markers), 10, "The fixture must have reached stable alternating frames")
                    boundary = markers[-3]
                    self.assertLessEqual(abs(markers[-2] - boundary - 140448), 80)
                    # Reference stops safely inside VBlank. The arbitrary
                    # middle endpoint is around line 98 of the next scanout.
                    limits = {"reference": boundary + 500,
                              "middle": boundary + (10 + 98) * 912 + 128,
                              "next": markers[-2] + 500}
                    results = {}
                    for name, ticks in (*limits.items(), ("plain", limits["middle"])):
                        prefix = target / f"{model}-{name}"
                        command = [str(RUNNER), "--model", model, "--rom", str(rom), "--ticks", str(ticks),
                                   "--wav", str(prefix.with_suffix(".wav")),
                                   "--trace", str(prefix.with_suffix(".tsv")), "--trace-address", "ff03",
                                   "--memory-out", str(prefix.with_suffix(".bin"))]
                        if name != "plain":
                            command += ["--screen", str(prefix.with_suffix(".ppm"))]
                        if name == "middle":
                            command += ["--state-out", str(prefix.with_suffix(".state"))]
                        result = subprocess.run(command, capture_output=True, text=True, check=True)
                        metadata = json.loads(result.stdout)
                        self.assertTrue(metadata["boot_rom_finished"])
                        self.assertGreater(metadata["audio_energy"], 0)
                        self.assertGreaterEqual(metadata["ticks_8mhz"], ticks)
                        self.assertLess(metadata["ticks_8mhz"] - ticks, 64,
                                        "A screenshot must not run ahead to the next VBlank")
                        results[name] = metadata
                    pixels = {}
                    for name in limits:
                        magic, dimensions, maximum, image = (target / f"{model}-{name}.ppm").read_bytes().split(b"\n", 3)
                        self.assertEqual((magic, dimensions, maximum), (b"P6", b"160 144", b"255"))
                        self.assertEqual(len(image), 160 * 144 * 3)
                        pixels[name] = image
                    self.assertEqual(pixels["middle"], pixels["reference"],
                                     "Mid-scanout must return the previous complete frame, not a mixture")
                    self.assertNotEqual(pixels["next"], pixels["reference"],
                                        "The next completed frame must actually have a different color")
                    for name in limits:
                        colors = {pixels[name][offset:offset + 3] for offset in range(0, len(pixels[name]), 3)}
                        self.assertEqual(len(colors), 1, f"{model}: {name} screenshot contains mixed frame colors")
                    for key in ("screen_ticks_8mhz", "screen_frame"):
                        self.assertEqual(results["middle"][key], results["reference"][key])
                        self.assertEqual(results["plain"][key], 0)
                    self.assertLessEqual(results["middle"]["screen_ticks_8mhz"], limits["reference"])
                    self.assertEqual(results["next"]["screen_ticks_8mhz"] - results["reference"]["screen_ticks_8mhz"], 140448)
                    self.assertEqual(results["next"]["screen_frame"], results["reference"]["screen_frame"] + 1)
                    memory = (target / f"{model}-middle.bin").read_bytes()
                    self.assertTrue(95 <= memory[0xff44] <= 101,
                                    "The tested capture must stop during visible scanout")
                    # Taking a screenshot is observation only: identical
                    # tick limits must preserve every PCM sample, bus trace,
                    # visible CPU/memory byte, and capture timing statistic.
                    for suffix in (".wav", ".tsv", ".bin"):
                        self.assertEqual((target / f"{model}-middle{suffix}").read_bytes(),
                                         (target / f"{model}-plain{suffix}").read_bytes(),
                                         f"{model}: --screen changed {suffix} output")
                    for key in ("ticks_8mhz", "frames", "audio_frames", "apu_writes", "writes", "audio_energy", "pc"):
                        self.assertEqual(results["middle"][key], results["plain"][key], key)

                    # Save states restore the PPU's position, but not the
                    # host pixel buffer. The remainder of this frame cannot
                    # become a valid screenshot after restoring at line 98.
                    resumed = target / f"{model}-resumed.ppm"
                    continuation = [str(RUNNER), "--model", model, "--rom", str(rom),
                                    "--state-in", str(target / f"{model}-middle.state"),
                                    "--screen", str(resumed)]
                    first_duration = limits["next"] - results["middle"]["ticks_8mhz"]
                    first = subprocess.run([*continuation, "--ticks", str(first_duration)],
                                           capture_output=True, text=True)
                    self.assertNotEqual(first.returncode, 0,
                                        "The first partial frame after state restoration is not complete")
                    self.assertIn("complete", first.stderr.lower())
                    self.assertFalse(resumed.exists(), "Do not publish an incomplete restored screenshot")
                    second_duration = first_duration + 140448
                    second = subprocess.run([*continuation, "--ticks", str(second_duration)],
                                            capture_output=True, text=True, check=True)
                    resumed_metadata = json.loads(second.stdout)
                    resumed_pixels = resumed.read_bytes().split(b"\n", 3)[3]
                    self.assertEqual(resumed_pixels, pixels["reference"],
                                     "The second VBlank after restoration contains the next full frame")
                    self.assertEqual(resumed_metadata["screen_frame"], 2)
                    # SameBoy restarts its debugger clock on state load;
                    # compare elapsed times within each capture timeline.
                    self.assertEqual(resumed_metadata["screen_ticks_8mhz"] - resumed_metadata["initial_ticks_8mhz"],
                                     results["next"]["screen_ticks_8mhz"] + 140448 - results["middle"]["ticks_8mhz"])
                    elapsed = resumed_metadata["ticks_8mhz"] - resumed_metadata["initial_ticks_8mhz"]
                    self.assertGreaterEqual(elapsed, second_duration)
                    self.assertLess(elapsed - second_duration, 64,
                                    "Restored screenshot capture must also respect the requested endpoint")


if __name__ == "__main__":
    unittest.main()
