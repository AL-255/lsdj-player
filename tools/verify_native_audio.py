#!/usr/bin/env python3
"""Build native interpreters from existing saves and compare real LSDj playback.

Each case is captured independently on CGB-E and DMG-B. The startup snapshots
contain song/interpreter state; neither APU writes nor audio are embedded in
the player ROM. The pass criterion is strict PCM equality from reset with zero
offsets. Relative song-write diagnostics never turn a PCM failure into a pass.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import subprocess
import struct
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
TIMEBASE = 8388608
SAMEBOY_REVISION = 'c458e7c5d2d350fb37a1931c40da9f758d28d240'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def iter_trace(path: Path):
    fields = ('ticks_8mhz', 'frame', 'pc', 'rom_bank', 'address', 'value')
    with path.open() as stream:
        if tuple(stream.readline().strip().split('\t')) != fields:
            raise ValueError(f'Unexpected trace format: {path}')
        for line in stream:
            values = line.strip().split('\t')
            if len(values) != len(fields):
                raise ValueError(f'Truncated trace: {path}')
            yield {field: int(value, 16 if field in ('pc', 'address', 'value') else 10)
                   for field, value in zip(fields, values)}


def compare_wav(reference: Path, player: Path, *, sample_frames: int) -> dict:
    """Require every signed stereo PCM byte to match from reset, at zero offset."""
    if sample_frames <= 0:
        raise ValueError('Empty recordings cannot pass')
    with wave.open(str(reference), 'rb') as a, wave.open(str(player), 'rb') as b:
        sources = (a, b)
        formats = [(source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype())
                   for source in sources]
        if formats[0] != formats[1] or formats[0][:2] != (2, 2) or formats[0][3] != 'NONE':
            raise ValueError('Expected matching signed 16-bit stereo PCM WAV files')
        hashes = [hashlib.sha256(), hashlib.sha256()]
        read_frames = [0, 0]
        first = None
        for position in range(0, sample_frames, 8192):
            count = min(8192, sample_frames - position)
            chunks = [source.readframes(count) for source in sources]
            for side, chunk in enumerate(chunks):
                hashes[side].update(chunk)
                read_frames[side] += len(chunk) // 4
            if chunks[0] != chunks[1] and first is None:
                common = min(map(len, chunks))
                byte = next((i for i in range(common) if chunks[0][i] != chunks[1][i]), common)
                frame = byte // 4
                stereo = [list(struct.unpack_from('<hh', chunk, frame * 4))
                          if len(chunk) >= (frame + 1) * 4 else None for chunk in chunks]
                first = dict(sample_frame=position + frame, seconds=(position + frame)/formats[0][2],
                             reference=stereo[0], player=stereo[1])
        complete = read_frames == [sample_frames, sample_frames]
        return dict(bit_exact=complete and first is None, comparison='strict signed 16-bit stereo PCM bytes',
                    reference=str(reference), player=str(player), sample_rate=formats[0][2],
                    sample_frames_requested=sample_frames, sample_frames_read=read_frames, window_complete=complete,
                    reference_offset_samples=0, player_offset_samples=0,
                    reference_pcm_sha256=hashes[0].hexdigest(), player_pcm_sha256=hashes[1].hexdigest(),
                    first_mismatch=first)


def audit_apu_timeline(reference: Path, player: Path) -> dict:
    """Audit sound writes, DIV reset, speed writes and the CGB wave PC quirk."""
    def relevant(path):
        return (row for row in iter_trace(path) if row['address'] in (0xff04, 0xff4d)
                or 0xff10 <= row['address'] <= 0xff3f)
    counts = [0, 0]
    first = pc_first = None
    mismatch_count = 0
    for index, (a, b) in enumerate(zip_longest(relevant(reference), relevant(player))):
        for side, row in enumerate((a, b)):
            counts[side] += row is not None
        if a is None or b is None or any(a[key] != b[key] for key in ('ticks_8mhz', 'address', 'value')):
            mismatch_count += 1
            if first is None:
                first = dict(write_index=index, reference=a, player=b)
        elif a['address'] == 0xff1a and not a['value'] & 0x80 and a['pc'] & 15 != b['pc'] & 15:
            if pc_first is None:
                pc_first = dict(write_index=index, reference=a, player=b)
    return dict(write_timeline_equal=bool(counts[0]) and mismatch_count == 0,
                write_counts=counts, mismatched_writes=mismatch_count,
                first_write_mismatch=first, nr30_pc_nibbles_equal=pc_first is None,
                first_nr30_pc_mismatch=pc_first)


def song_writes(path: Path) -> list[dict]:
    """Select shared native song-init entry, excluding the boot chime."""
    result = []
    started = False
    for row in iter_trace(path):
        if (row['rom_bank'] == 2 and row['pc'] == 0x5fee
                and row['address'] == 0xff26 and row['value'] == 0x80):
            started = True
        if started and 0xff10 <= row['address'] <= 0xff3f:
            result.append(row)
    return result


def compare_song_writes(reference: Path, player: Path) -> dict:
    """Diagnose time and value differences without accepting an alignment."""
    sides = [song_writes(path) for path in (reference, player)]
    starts = [side[0]['ticks_8mhz'] if side else None for side in sides]
    result = dict(reference_start_tick=starts[0], player_start_tick=starts[1],
                  start_delta_ticks=None, write_counts=list(map(len, sides)),
                  first_relative_timing_mismatch=None, first_value_mismatch=None)
    if None in starts:
        result['missing_song_start'] = True
        return result
    result['start_delta_ticks'] = starts[1] - starts[0]
    for index, (a, b) in enumerate(zip_longest(*sides)):
        evidence = dict(write_index=index, reference=a, player=b)
        if (a is None or b is None or (a['address'], a['value']) != (b['address'], b['value'])):
            if result['first_value_mismatch'] is None:
                result['first_value_mismatch'] = evidence
        if (a is None or b is None or a['ticks_8mhz'] - starts[0] != b['ticks_8mhz'] - starts[1]):
            if result['first_relative_timing_mismatch'] is None:
                result['first_relative_timing_mismatch'] = evidence
        if result['first_value_mismatch'] and result['first_relative_timing_mismatch']:
            break
    return result


def validate_settings(record: dict, model: str, rate: int) -> None:
    expected = dict(sameboy_revision=SAMEBOY_REVISION,
                    model='CGB-E' if model == 'cgb' else 'DMG-B',
                    native_cgb_mode=model == 'cgb', double_speed=model == 'cgb',
                    sample_rate=rate, highpass_mode='accurate',
                    interference_volume=0, random_enabled=False, timebase_hz=TIMEBASE)
    mismatches = {key: (value, record.get(key)) for key, value in expected.items() if record.get(key) != value}
    if mismatches:
        raise ValueError(f'Unexpected capture settings: {mismatches}')


def run(command: list[str], stem: Path) -> dict:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix('.command.json').write_text(json.dumps(command, indent=2) + '\n')
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    stem.with_suffix('.log').write_text(completed.stderr)
    stats = json.loads(completed.stdout)
    stem.with_suffix('.json').write_text(json.dumps(stats, indent=2) + '\n')
    return stats


def verify_case(case: dict, runner: Path, output: Path, seconds: float,
                rate: int, native_rom: Path | None = None, *, connect_pitch_bends: bool = False) -> dict:
    directory = output / case['id'].replace(':', '-track-')
    directory.mkdir(parents=True, exist_ok=True)
    inputs = {key: Path(case[key]).resolve() for key in ('reference_rom', 'prepared_save')}
    for key, path in inputs.items():
        if sha256(path) != case[key + '_sha256']:
            raise ValueError(f'{case["id"]}: {key} no longer matches the prepared manifest')
    ticks = round(seconds * TIMEBASE)
    if ticks <= 0:
        raise ValueError('Comparison length must be positive')
    reference_commands = {}
    for model in ('cgb', 'dmg'):
        reference_commands[model] = [str(runner), '--model', model, '--rom', str(inputs['reference_rom']),
                                     '--sav', str(inputs['prepared_save']), '--ticks', str(ticks),
                                     '--sample-rate', str(rate), '--buttons', case['reference_buttons']]

    def original(model: str) -> tuple[str, dict]:
        stem = directory / (model + '-reference')
        command = reference_commands[model] + ['--wav', str(stem.with_suffix('.wav')),
            '--trace', str(stem.with_suffix('.tsv')), '--reads', str(directory / (model + '-reads.tsv')),
            '--trace-address', 'ff40',
            '--execution-profile', str(directory / (model + '-execution.tsv')),
            '--screen', str(stem.with_suffix('.ppm'))]
        stats = run(command, stem)
        # The snapshot is independently collected immediately before LSDj's
        # own native sound initializer; it must reach that exact entry.
        if native_rom is None:
            prefix = directory / ('startup-' + model)
            snapshot = run(reference_commands[model] + ['--break-pc', '2:0x5fe7',
                           '--native-dump', str(prefix)], directory / (model + '-snapshot-run'))
            if snapshot['pc'] != 0x5fe7:
                raise ValueError(f'{case["id"]}/{model}: song did not reach the native entry')
        return model, stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        originals = dict(pool.map(original, ('cgb', 'dmg')))
    if native_rom is None:
        native_rom = directory / 'native.gb'
        build_command = [sys.executable, str(ROOT/'tools/build_native_player.py'),
                         '--rom', str(inputs['reference_rom']),
                         '--profile', str(directory/'cgb-execution.tsv'),
                         '--snapshot-prefix', str(directory/'startup'),
                         '--output', str(native_rom), '--name', case['name'],
                         '--align-startup', '--runner', str(runner),
                         '--startup-trace-cgb', str(directory/'cgb-reference.tsv'),
                         '--startup-trace-dmg', str(directory/'dmg-reference.tsv')]
        if connect_pitch_bends:
            build_command += ['--connect-pitch-bends']
        build_metadata = run(build_command, directory/'build')
    else:
        build_metadata = json.loads(native_rom.with_suffix('.json').read_text())
    if (build_metadata.get('architecture') != 'native_song_interpreter'
            or build_metadata.get('performance_trace_bytes') != 0):
        raise ValueError('Only native song interpreters with no performance trace may be verified')
    native_hash = sha256(native_rom)

    def player(model: str) -> dict:
        stem = directory / (model + '-player')
        command = [str(runner), '--model', model, '--rom', str(native_rom), '--ticks', str(ticks),
                   '--sample-rate', str(rate), '--wav', str(stem.with_suffix('.wav')),
                   '--trace', str(stem.with_suffix('.tsv')), '--screen', str(stem.with_suffix('.ppm'))]
        stats = run(command, stem)
        reference = directory / (model + '-reference')
        with wave.open(str(reference.with_suffix('.wav')), 'rb') as recording:
            samples = recording.getnframes()
        for record in (stats, originals[model]):
            validate_settings(record, model, rate)
        pcm = compare_wav(reference.with_suffix('.wav'), stem.with_suffix('.wav'), sample_frames=samples)
        diagnostic = compare_song_writes(reference.with_suffix('.tsv'), stem.with_suffix('.tsv'))
        # Nonzero reset audio can consist only of the boot chime. Require actual
        # musical writes after native initialization on both sides as well.
        music_observed = min(diagnostic['write_counts']) > 6
        return dict(model=stats['model'], reference_stats=originals[model], player_stats=stats,
                    pcm=pcm, song_writes=diagnostic, music_observed=music_observed,
                    bus=audit_apu_timeline(reference.with_suffix('.tsv'), stem.with_suffix('.tsv')),
                    capture_sha256={f'{side}_{extension}': sha256(path.with_suffix('.' + extension))
                                    for side, path in (('reference', reference), ('player', stem))
                                    for extension in ('wav', 'tsv')},
                    bit_exact=pcm['bit_exact'] and music_observed,
                    extra_player_samples=stats['audio_frames'] - samples)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        models = list(pool.map(player, ('cgb', 'dmg')))
    if sha256(native_rom) != native_hash or any(sha256(path) != case[key + '_sha256'] for key, path in inputs.items()):
        raise ValueError('An input changed during capture')
    result = dict(case=case, native_rom=str(native_rom), native_rom_sha256=native_hash,
                  native_build=build_metadata, models=models)
    (directory/'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT/'build/audio-ab/prepared/cases.json')
    parser.add_argument('--match', action='append', default=[], help='Case ID glob; repeat to select more cases')
    parser.add_argument('--runner', type=Path, default=ROOT/'build/sameboy-native-analysis')
    parser.add_argument('--output', type=Path, default=ROOT/'build/audio-ab/native-verification')
    parser.add_argument('--seconds', type=float, default=20, help='Seconds from reset, including boot and song startup')
    parser.add_argument('--sample-rate', type=int, default=48000)
    parser.add_argument('--native-rom', type=Path, help='Verify this prebuilt native ROM against exactly one selected case')
    parser.add_argument('--connect-pitch-bends', action='store_true', help='Build the optional legato/pitch-bend waterfall renderer')
    args = parser.parse_args()
    cases = [case for case in json.loads(args.cases.read_text())['cases']
             if any(fnmatch.fnmatchcase(case['id'], pattern) for pattern in (args.match or ['triac/*']))]
    if not cases or (args.native_rom and len(cases) != 1):
        parser.error('Select at least one case, or exactly one with --native-rom')
    if args.native_rom and args.connect_pitch_bends:
        parser.error('--connect-pitch-bends builds a new ROM; omit it when checking --native-rom')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    runner = args.runner.resolve()
    runner_hash = sha256(runner)
    build_path = runner.with_suffix('.build.json')
    report = dict(scope='Native song interpreter versus real LSDj START; strict PCM from reset, zero offsets',
                  recorded_seconds_from_reset=args.seconds, sample_rate=args.sample_rate,
                  runner=str(runner), runner_sha256=runner_hash,
                  connect_pitch_bends=args.connect_pitch_bends,
                  runner_build=json.loads(build_path.read_text()) if build_path.exists() else None,
                  build_source_sha256={str(path.relative_to(ROOT)): sha256(path) for path in
                      (ROOT/'tools/build_native_player.py', ROOT/'tools/native_delay.py',
                       ROOT/'src/native/boot.asm', ROOT/'src/native/display.asm',
                       ROOT/'src/native/bend.asm', ROOT/'src/native/color.asm',
                       ROOT/'src/waterfall.asm', ROOT/'src/exact_ui.asm')}, cases=[])
    for case in cases:
        result = verify_case(case, runner, args.output, args.seconds, args.sample_rate,
                             args.native_rom.resolve() if args.native_rom else None,
                             connect_pitch_bends=args.connect_pitch_bends)
        report['cases'].append(result)
        all_models = [model for entry in report['cases'] for model in entry['models']]
        report['summary'] = dict(comparisons=len(all_models), bit_exact=sum(model['bit_exact'] for model in all_models),
                                 failed=sum(not model['bit_exact'] for model in all_models))
        (args.output/'report.json').write_text(json.dumps(report, indent=2) + '\n')
        for model in result['models']:
            print(f"{case['id']} {model['model']} bit_exact={model['bit_exact']} "
                  f"first_pcm_mismatch={model['pcm']['first_mismatch']} "
                  f"start_delta_ticks={model['song_writes']['start_delta_ticks']}", flush=True)
    if sha256(runner) != runner_hash:
        raise ValueError('Capture executable changed during comparison')
    return int(report['summary']['failed'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
