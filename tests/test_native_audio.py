"""Native A/B diagnostics must not hide startup/timing or missing music."""
from pathlib import Path
import sys
import struct
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from verify_native_audio import compare_song_writes, compare_wav, validate_settings, SAMEBOY_REVISION, TIMEBASE
from build_native_player import (_adjust_startup_delays, _startup_targets,
                                 _last_lcd_enable, _timer_increment_count)
import json


class NativeAudioDiagnosticsTests(unittest.TestCase):
    def write(self, path, rows):
        path.write_text('ticks_8mhz\tframe\tpc\trom_bank\taddress\tvalue\n' + rows)

    def test_reports_start_offset_and_later_irq_jitter_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary)/'a.tsv', Path(temporary)/'b.tsv'
            self.write(a, '10\t0\t0020\t0\tff26\t80\n100\t1\t5fee\t2\tff26\t80\n200\t2\t610a\t2\tff1c\t40\n')
            self.write(b, '10\t0\t0020\t0\tff26\t80\n120\t1\t5fee\t2\tff26\t80\n228\t2\t610a\t2\tff1c\t40\n')
            result = compare_song_writes(a, b)
            self.assertEqual(result['start_delta_ticks'], 20)
            self.assertEqual(result['first_relative_timing_mismatch']['write_index'], 1)
            self.assertIsNone(result['first_value_mismatch'])

    def test_boot_chime_is_not_song_playback(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary)/'a.tsv', Path(temporary)/'b.tsv'
            for path in (a, b):
                self.write(path, '10\t0\t0020\t0\tff26\t80\n')
            result = compare_song_writes(a, b)
            self.assertTrue(result['missing_song_start'])
            self.assertEqual(result['write_counts'], [0, 0])

    def wav(self, path, frames):
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b''.join(struct.pack('<hh', *frame) for frame in frames))

    def test_single_changed_bit_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary)/'a.wav', Path(temporary)/'b.wav'
            self.wav(a, [(0, 0), (123, -456)])
            self.wav(b, [(0, 0), (123, -455)])
            result = compare_wav(a, b, sample_frames=2)
            self.assertFalse(result['bit_exact'])
            self.assertEqual(result['first_mismatch']['sample_frame'], 1)
            self.assertEqual(result['reference_offset_samples'], 0)
            self.assertEqual(result['player_offset_samples'], 0)

    def test_truncated_identical_prefix_does_not_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary)/'a.wav', Path(temporary)/'b.wav'
            self.wav(a, [(123, -456)] * 3)
            self.wav(b, [(123, -456)] * 2)
            result = compare_wav(a, b, sample_frames=3)
            self.assertFalse(result['bit_exact'])
            self.assertFalse(result['window_complete'])
            self.assertEqual(result['first_mismatch']['sample_frame'], 2)

    def test_identical_complete_window_passes_but_empty_cannot(self):
        with tempfile.TemporaryDirectory() as temporary:
            a = Path(temporary)/'a.wav'
            self.wav(a, [(123, -456)] * 3)
            self.assertTrue(compare_wav(a, a, sample_frames=3)['bit_exact'])
            with self.assertRaises(ValueError):
                compare_wav(a, a, sample_frames=0)

    def test_setting_mismatches_are_rejected(self):
        correct = dict(sameboy_revision=SAMEBOY_REVISION, model='DMG-B', native_cgb_mode=False,
                       double_speed=False, sample_rate=48000, highpass_mode='accurate',
                       interference_volume=0, random_enabled=False, timebase_hz=TIMEBASE)
        validate_settings(correct, 'dmg', 48000)
        for field, bad in [('sameboy_revision', 'different'), ('double_speed', True), ('sample_rate', 96000)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_settings({**correct, field: bad}, 'dmg', 48000)

    def test_startup_delay_adjustment_accounts_for_each_hardware_clock(self):
        delays = {'cgb': 800, 'dmg': 800}
        self.assertEqual(_adjust_startup_delays(delays, {'cgb': 1000, 'dmg': 1000},
                                               {'cgb': 1200, 'dmg': 600}),
                         {'cgb': 600, 'dmg': 1000})
        with self.assertRaises(ValueError):
            _adjust_startup_delays(delays, {'cgb': 1000, 'dmg': 1000}, {'cgb': 2000, 'dmg': 1000})
        with self.assertRaises(ValueError):
            _adjust_startup_delays(delays, {'cgb': 1000, 'dmg': 1000}, {'cgb': 1000, 'dmg': 1004})

    def test_startup_targets_require_the_correct_native_snapshot_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)/'startup'
            for model in ('cgb', 'dmg'):
                metadata = dict(model='CGB-E' if model == 'cgb' else 'DMG-B', pc=0x5fe7,
                                rom_bank=2, double_speed=model == 'cgb', tick=1000)
                Path(f'{prefix}-{model}.json').write_text(json.dumps(metadata))
            self.assertEqual(_startup_targets(prefix), {'cgb': 1000, 'dmg': 1000})
            metadata['pc'] = 0x4000
            Path(f'{prefix}-dmg.json').write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                _startup_targets(prefix)

    def test_lcd_phase_uses_enable_edge_before_song_not_later_map_swaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary)/'lcd.tsv'
            self.write(trace, '10\t0\t0100\t0\tff40\t91\n'
                       '20\t0\t0100\t0\tff40\t00\n'
                       '30\t0\t0100\t0\tff40\tc3\n'
                       '40\t0\t0100\t0\tff40\tf7\n'
                       '60\t0\t0100\t0\tff40\t00\n'
                       '70\t0\t0100\t0\tff40\tf7\n')
            self.assertEqual(_last_lcd_enable(trace, 50), 30)
            self.assertEqual(_last_lcd_enable(trace, 80), 70)
            with self.assertRaises(ValueError):
                _last_lcd_enable(trace, 25)

    def test_startup_timer_increment_count_handles_reload(self):
        self.assertEqual(_timer_increment_count(0x90, 0x94, 0x49), 4)
        self.assertEqual(_timer_increment_count(0xfe, 0x4b, 0x49), 4)
        with self.assertRaises(ValueError):
            _timer_increment_count(0xfe, 0x40, 0x49)


if __name__ == '__main__':
    unittest.main()
