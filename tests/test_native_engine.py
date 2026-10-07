"""Native interpreter regressions using optional local LSDj/WOW fixtures.

These checks cover the bank-7 tempo-command crash, not PCM equivalence.
Prepare the native A/B corpus first; proprietary ROMs and startup snapshots
remain local and are not part of the test source.
"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "build/sameboy-native-analysis"
REFERENCE = ROOT / "build/audio-ab/prepared/wow/WOW_v682-v901/original.gb"
FIXTURE = ROOT / "build/audio-ab/native-verification/wow/WOW_v682-v901-track-0"
PROFILE = FIXTURE / "cgb-execution.tsv"
STARTUP = FIXTURE / "startup"
CLOCK = 8388608
REQUIRED_FILES = [RUNNER, REFERENCE, PROFILE] + [
    FIXTURE / f"startup-{model}.{suffix}.bin"
    for model in ("cgb", "dmg") for suffix in ("wram", "hram", "song")
]


@unittest.skipUnless(
    all(path.exists() for path in REQUIRED_FILES)
    and all(shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix")),
    "Prepare the local WOW native A/B fixtures, SameBoy, and RGBDS first",
)
class NativeEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="native-engine-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.work = Path(cls.directory.name)
        cls.rom = cls.work / "WOW-native.gb"
        subprocess.run(
            [sys.executable, str(ROOT / "tools/build_native_player.py"),
             "--rom", str(REFERENCE), "--profile", str(PROFILE),
             "--snapshot-prefix", str(STARTUP), "--output", str(cls.rom),
             "--name", "WOW"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )

    def test_shared_tempo_bank_is_preserved_without_a_performance_trace(self):
        source, player = REFERENCE.read_bytes(), self.rom.read_bytes()
        bank = slice(7 * 0x4000, 8 * 0x4000)
        self.assertEqual(player[bank], source[bank])
        metadata = json.loads(self.rom.with_suffix(".json").read_text())
        self.assertEqual(metadata["architecture"], "native_song_interpreter")
        self.assertEqual(metadata["engine_banks"], [0, 2, 7])
        self.assertEqual(metadata["performance_trace_bytes"], 0)
        self.assertIsNone(metadata["duration_limit"])

    def test_wow_tempo_commands_keep_playing_on_both_hardware_models(self):
        for model in ("cgb", "dmg"):
            with self.subTest(model=model):
                trace = self.work / f"{model}-writes.tsv"
                profile = self.work / f"{model}-execution.tsv"
                result = subprocess.run(
                    [str(RUNNER), "--rom", str(self.rom), "--model", model,
                     "--ticks", str(20 * CLOCK), "--trace", str(trace),
                     "--execution-profile", str(profile)],
                    cwd=ROOT, check=True, capture_output=True, text=True,
                )
                metadata = json.loads(result.stdout)
                # Before the fix, executing an absent bank fell through into
                # song data and wrapped the stack through the I/O registers.
                self.assertNotIn("HW Register", result.stderr)
                self.assertNotIn("RAM Mirror", result.stderr)
                self.assertGreater(metadata["apu_writes"], 10000)
                with trace.open() as stream:
                    writes = [row for row in csv.DictReader(stream, delimiter="\t")
                              if 0xff10 <= int(row["address"], 16) <= 0xff3f]
                self.assertGreater(int(writes[-1]["ticks_8mhz"]), 19 * CLOCK)
                with profile.open() as stream:
                    tempo = [row for row in csv.DictReader(stream, delimiter="\t")
                             if row["bank"] == "7" and row["address"] == "5dd8"]
                self.assertTrue(tempo, "WOW must exercise the shared tempo helper")
                self.assertGreater(int(tempo[0]["count"]), 0)


if __name__ == "__main__":
    unittest.main()
