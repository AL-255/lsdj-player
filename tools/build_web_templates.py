#!/usr/bin/env python3
"""Compile public player patches from synthetic zero-filled inputs only.

No LSDj ROM or song is read. The browser supplies engine banks and captures
song/startup memory locally; these assets contain only this project's code.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import tempfile

import build_native_player as native
from native_delay import delay_code
from song_title import ROWS

ROOT = Path(__file__).resolve().parents[1]
SLOT_SIZE = 192
SLOTS = {8220996: 'SongCGB', 8547120: 'SongDMG', 41700: 'LCDCGB', 36960: 'LCDDMG'}
SOURCES = ['tools/build_web_templates.py', 'tools/build_native_player.py', 'tools/native_delay.py',
           'tools/song_title.py', 'src/exact_ui.asm', *[str(p.relative_to(ROOT)) for p in sorted((ROOT/'src/native').glob('*.asm'))]]


def slot_source(cycles):
    name = '.WebDelay' + SLOTS[cycles]
    encoded = delay_code(cycles - 16)  # final JP costs16cycles
    assert len(encoded) + 3 <= SLOT_SIZE
    return (f'{name}::\n    db ' + ','.join(f'${x:02x}' for x in encoded)
            + f'\n    jp {name}End\n    ds {SLOT_SIZE}-(@-{name}),0\n{name}End::')


def build(output):
    # Add assembly labels around patchable immediates, without modifying the
    # normal native builder or changing its instruction semantics.
    source = (ROOT/'tools/build_native_player.py').read_text()
    old = "early += [f'    ld a,${timer_state[model][key]:02x}',f'    ldh [${address:04x}],a']"
    new = "early += [f'.WebTimer{model.upper()}{key.upper()}::', f'    ld a,${timer_state[model][key]:02x}',f'    ldh [${address:04x}],a']"
    if source.count(old) != 1:
        raise ValueError('Native timer patch location changed')
    source = source.replace('jr nz,.dmg_delay', 'jp nz,.dmg_delay').replace('jr .song_ready', 'jp .song_ready')
    source = source.replace('jr nz,.cgb', 'jp nz,.cgb').replace('jr .done', 'jp .done')
    namespace = {'__file__': str(ROOT/'tools/build_native_player.py'), '__name__': 'web_template_native'}
    exec(compile(source.replace(old, new), 'web-template-builder', 'exec'), namespace)
    namespace['delay_source'] = slot_source
    templates = {}
    with tempfile.TemporaryDirectory(prefix='lsdj-web-templates-') as temp:
        work = Path(temp)
        rom = bytearray(0x100000)
        rom[0x1889:0x188c] = bytes.fromhex('21 00 80')  # expected patch-site sentinel only
        (work/'empty.gb').write_bytes(rom)
        (work/'profile.tsv').write_text('bank\taddress\tcount\n')
        for model in ('dmg', 'cgb'):
            for suffix, size in [('wram', 8192), ('hram', 127), ('song', 32768)]:
                (work/f'empty-{model}.{suffix}.bin').write_bytes(bytes(size))
        for low in (False, True):
            for bends in (False, True):
                name = ('low' if low else 'full') + ('-bends' if bends else '-points')
                target = work/(name+'.gb')
                namespace['_build_once'](work/'empty.gb', work/'profile.tsv', work/'empty', target,
                                         name='        ', low_range=low, connect_pitch_bends=bends)
                data = target.read_bytes()
                assert not any(data[0x8000:0xc000]) and not any(data[0x1c000:0x20000])
                symbols = {}
                for line in target.with_suffix('.sym').read_text().splitlines():
                    if not line or line.startswith(';'): continue
                    address, label = line.split()
                    if ':' not in address: continue
                    bank, offset = (int(x, 16) for x in address.split(':'))
                    symbols[label.rsplit('.', 1)[-1]] = offset if not bank else bank*0x4000+offset-0x4000
                chunks = [(0x42, 0x45), (0x100, 0x104), (0xb54, 0x1306), (0x14000, 0x18000)]
                templates[name] = {
                    'patches': [{'offset': a, 'data': base64.b64encode(data[a:b]).decode()} for a,b in chunks],
                    'delays': {kind: {'offset': symbols['WebDelay'+kind], 'length': SLOT_SIZE,
                                     'endAddress': symbols['WebDelay'+kind+'End'] % 0x4000 + (0x4000 if symbols['WebDelay'+kind+'End'] >= 0x4000 else 0)}
                               for kind in SLOTS.values()},
                    'timers': {model: {key: symbols['WebTimer'+model.upper()+key.upper()]+1
                                      for key in ('tima','tma','tac','if')} for model in ('dmg','cgb')},
                    'titleOffset': symbols['ExactUIFont'] + 28*16,
                }
    artifact = {'format': 1, 'slotSize': SLOT_SIZE, 'templates': templates, 'glyphs': ROWS,
                'sourceSha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, separators=(',', ':'))+'\n')
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'web/generated/templates.json')
    build(parser.parse_args().output)
