import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { assemblePlayer, convertPlayer, delayCode, fixChecksums, validateROM } from '../web/player-core.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const BANK = 0x4000;
const assets = JSON.parse(readFileSync(path.join(ROOT, 'web/generated/templates.json'), 'utf8'));
const hash = (data) => createHash('sha256').update(data).digest('hex');
const python = (script, input) => JSON.parse(execFileSync('python3', ['-c', script], {
  cwd: ROOT, input: JSON.stringify(input), encoding: 'utf8', maxBuffer: 4 * 1024 * 1024,
}));
const defaultTiming = () => ({ song: { cgb: 8220996, dmg: 8547120 }, lcd: { cgb: 41700, dmg: 36960 },
  timer: { dmg: { tima: 144, tma: 73, tac: 6, if: 4 }, cgb: { tima: 170, tma: 73, tac: 6, if: 4 } } });

function sourceROM() {
  return Uint8Array.from({ length: 0x100000 }, (_, offset) =>
    ((offset * 13 + 17) & 255) ^ Math.floor(offset / BANK));
}

function compatibility(source) {
  return { romSize: source.length, bank0IgnoredHeader: [0x134, 0x150],
    engineBanks: Object.fromEntries([0, 2, 7].map((bank) => {
      const data = source.slice(bank * BANK, (bank + 1) * BANK);
      if (!bank) data.fill(0, 0x134, 0x150);
      return [bank, hash(data)];
    })) };
}

function snapshots(kitIds = []) {
  const song = new Uint8Array(32768);
  for (let i = 0; i < 300; i++) song[i] = (i * 7 + 5) & 255;
  kitIds.forEach((id, index) => {
    const base = 0x3080 + index * 16;
    song[base] = 2; song[base + 2] = id | 0xc0; song[base + 9] = id | 0x80;
  });
  return Object.fromEntries(['dmg', 'cgb'].map((model, index) => [model, {
    song: song.slice(), wram: new Uint8Array(8192).fill(0x20 + index),
    hram: new Uint8Array(127).fill(0x40 + index),
  }]));
}

function assertChecksums(rom) {
  const header = rom.subarray(0x134, 0x14d).reduce((sum, value) => sum - value - 1, 0) & 255;
  assert.equal(rom[0x14d], header);
  let sum = 0;
  for (let i = 0; i < rom.length; i++) if (i !== 0x14e && i !== 0x14f) sum += rom[i];
  assert.equal((rom[0x14e] << 8) | rom[0x14f], sum & 65535);
}

function assertTiming(rom, template, timing) {
  for (const model of ['dmg', 'cgb']) {
    for (const [kind, cycles] of [['Song', timing.song[model]], ['LCD', timing.lcd[model]]]) {
      const slot = template.delays[kind + model.toUpperCase()];
      const delay = delayCode(cycles - 16);
      assert.deepEqual(rom.subarray(slot.offset, slot.offset + delay.length), delay);
      assert.deepEqual(rom.subarray(slot.offset + delay.length, slot.offset + delay.length + 3),
        Uint8Array.of(0xc3, slot.endAddress & 255, slot.endAddress >> 8));
      assert.ok(rom.subarray(slot.offset + delay.length + 3, slot.offset + slot.length).every((value) => value === 0));
    }
    for (const key of ['tima', 'tma', 'tac', 'if']) assert.equal(rom[template.timers[model][key]], timing.timer[model][key]);
  }
}

test('delay bytecode matches Python at short, loop and DMG OAM boundaries', () => {
  const loop = 28 * 0xfdff + 64;
  const cycles = [...Array.from({ length: 501 }, (_, i) => i * 4),
    ...[-4, 0, 4, 180, 184].map((delta) => loop + delta),
    36960, 41700, 8220996, 8547120, 20_000_000];
  const expected = python(`import json,sys\nsys.path.insert(0,'tools')\nfrom native_delay import delay_code\nprint(json.dumps([delay_code(n).hex() for n in json.load(sys.stdin)]))`, cycles);
  cycles.forEach((value, index) => assert.equal(Buffer.from(delayCode(value)).toString('hex'), expected[index], `delay ${value}`));
  for (const invalid of [-4, 1, 1.5, NaN, Infinity, '64']) assert.throws(() => delayCode(invalid), /Invalid startup delay/);
});

test('header and global checksums are correct and idempotent', () => {
  const rom = sourceROM();
  assert.equal(fixChecksums(rom), rom);
  assertChecksums(rom);
  const before = rom.slice();
  fixChecksums(rom);
  assert.deepEqual(rom, before);
  rom[0x9000] ^= 1;
  fixChecksums(rom);
  assertChecksums(rom);
});

test('ROM validation allows declared header/custom-kit changes and rejects engine changes', async () => {
  const source = sourceROM();
  const policy = compatibility(source);
  const original = source.slice();
  await validateROM(source, policy);
  assert.deepEqual(source, original, 'validation must preserve the selected file');
  const customized = source.slice();
  for (let i = 0x134; i < 0x150; i++) customized[i] ^= 0xff;
  customized[8 * BANK + 30] ^= 0xff;
  customized[63 * BANK + 90] ^= 0xff;
  await validateROM(customized, policy);
  for (const address of [0x133, 0x150, 2 * BANK + 90, 7 * BANK + 90]) {
    const corrupt = source.slice(); corrupt[address] ^= 1;
    await assert.rejects(validateROM(corrupt, policy), /engine is not supported/);
  }
  await assert.rejects(validateROM(source.subarray(0, 0x20000), policy), /1 MiB/);
  await assert.rejects(validateROM(source.buffer, policy), /1 MiB/);
});

test('all public templates preserve engine/sample banks and patch startup/title data', () => {
  const source = sourceROM(), original = source.slice();
  const states = snapshots([0, 18, 19, 50]), timing = defaultTiming();
  const title = 'a?/-Z012extra';
  const titleBytes = python(`import json,sys,tempfile,pathlib\nsys.path.insert(0,'tools')\nfrom song_title import write_tiles\nwith tempfile.TemporaryDirectory() as d:\n p=pathlib.Path(d)/'title';write_tiles(p,json.load(sys.stdin));print(json.dumps(list(p.read_bytes())))`, title);
  assert.deepEqual(Object.keys(assets.templates).sort(), ['full-bends', 'full-points', 'low-bends', 'low-points']);
  for (const [name, template] of Object.entries(assets.templates)) {
    const rom = assemblePlayer(source, states, template, assets.glyphs, title, timing);
    assert.equal(rom.length, 0x100000, name);
    for (const bank of [2, 7, 8, 26, 32, 63]) {
      assert.deepEqual(rom.subarray(bank * BANK, (bank + 1) * BANK), source.subarray(bank * BANK, (bank + 1) * BANK));
    }
    for (const bank of [6, 9, 27, 28, 29, 30, 31]) assert.ok(rom.subarray(bank * BANK, (bank + 1) * BANK).every((value) => value === 0));
    assert.deepEqual(rom.subarray(BANK, 2 * BANK), states.dmg.song.subarray(0, BANK));
    assert.deepEqual(rom.subarray(3 * BANK, 4 * BANK), states.dmg.song.subarray(BANK));
    assert.deepEqual(rom.subarray(4 * BANK, 4 * BANK + 8192), states.dmg.wram);
    assert.deepEqual(rom.subarray(4 * BANK + 8192, 5 * BANK), states.cgb.wram);
    assert.deepEqual(rom.subarray(5 * BANK, 5 * BANK + 127), states.dmg.hram);
    assert.deepEqual(rom.subarray(5 * BANK + 127, 5 * BANK + 254), states.cgb.hram);
    assert.deepEqual(rom.subarray(template.titleOffset, template.titleOffset + 128), Uint8Array.from(titleBytes));
    assert.deepEqual(rom.subarray(0x188a, 0x188c), Uint8Array.of(0x80, 0xcc));
    assert.equal(rom[0x143], 0x80); assert.equal(rom[0x147], 0x1a);
    assert.equal(rom[0x148], 5); assert.equal(rom[0x149], 3);
    assertTiming(rom, template, timing);
    const replacedEngine = [[0x134, 0x150], [0x188a, 0x188c],
      ...template.patches.map((patch) => [patch.offset, patch.offset + Buffer.from(patch.data, 'base64').length])];
    for (let offset = 0; offset < BANK; offset++) {
      if (!replacedEngine.some(([start, end]) => start <= offset && offset < end)) {
        assert.equal(rom[offset], source[offset], `${name} preserved engine ${offset.toString(16)}`);
      }
    }
    const mutable = [[5 * BANK, 5 * BANK + 254], [template.titleOffset, template.titleOffset + 128],
      ...Object.values(template.delays).map((slot) => [slot.offset, slot.offset + slot.length]),
      ...Object.values(template.timers).flatMap((fields) => Object.values(fields).map((offset) => [offset, offset + 1]))];
    for (const patch of template.patches) {
      const expected = Buffer.from(patch.data, 'base64');
      for (let i = 0; i < expected.length; i++) {
        const offset = patch.offset + i;
        if (!mutable.some(([start, end]) => start <= offset && offset < end)) assert.equal(rom[offset], expected[i], `${name} patch ${offset.toString(16)}`);
      }
    }
    assertChecksums(rom);
  }
  assert.deepEqual(source, original);
});

test('assembly rejects differing or truncated songs and timing-slot overflow', () => {
  const source = sourceROM(), template = assets.templates['full-points'], timing = defaultTiming();
  const clean = snapshots();
  assert.equal(assemblePlayer(source, clean, template, assets.glyphs, '', timing).length, 0x20000);
  for (const model of ['dmg', 'cgb']) {
    const states = snapshots(); states[model].song = states[model].song.subarray(0, 32767);
    assert.throws(() => assemblePlayer(source, states, template, assets.glyphs, 'TEST', timing), /different song data/);
  }
  const different = snapshots(); different.cgb.song[200] ^= 1;
  assert.throws(() => assemblePlayer(source, different, template, assets.glyphs, 'TEST', timing), /different song data/);
  const tooLong = defaultTiming(); tooLong.song.dmg = 100_000_000;
  assert.throws(() => assemblePlayer(source, clean, template, assets.glyphs, 'TEST', tooLong), /supported timing range/);
});

function savedSlot() {
  const data = new Uint8Array(0x20000);
  data.set([0x6a, 0x6b], 0x813e); data.fill(0xff, 0x8140, 0x8200); data[0x8141] = 3;
  let cursor = 0x8200;
  for (let length = 32768; length > 0; length -= 255) {
    data.set([0xc0, 1, Math.min(255, length)], cursor); cursor += 3;
  }
  data.set([0xe0, 0xff], cursor);
  return data;
}

function captureModule({ wrongTimer = false, fractionalTick = false, neverAlign = false } = {}) {
  const memory = new Uint8Array(0x240000), captures = [];
  const pointers = { wram: 0x180000, hram: 0x182000, song: 0x182100, io: 0x18a100 };
  let next = 0x100, active, automatic = true, metadata;
  return {
    HEAPU8: memory, captures, destroyed: 0,
    _malloc(size) { const pointer = next; next += size; return pointer; },
    _free() { next = 0x100; },
    _lsdj_init(romPointer, romLength, savePointer, saveLength, model) {
      active = { rom: memory.slice(romPointer, romPointer + romLength), save: memory.slice(savePointer, savePointer + saveLength), model };
      captures.push(active); return 0;
    },
    _lsdj_set_auto_buttons(value) { automatic = Boolean(value); active.automatic = automatic; },
    _lsdj_run_frames() {
      const index = captures.length - 1, model = active.model;
      const expectedTick = model ? 21_000_000 : 20_000_000;
      const expectedLCD = model ? 1_100_000 : 1_000_000;
      const correcting = index >= 2 && (index < 4 || neverAlign);
      const song = automatic ? active.save.subarray(0, 32768)
        : Uint8Array.from([...active.rom.subarray(BANK, 2 * BANK), ...active.rom.subarray(3 * BANK, 4 * BANK)]);
      memory.set(song, pointers.song);
      memory.fill(0x20 + model, pointers.wram, pointers.wram + 8192);
      memory.fill(0x40 + model, pointers.hram, pointers.hram + 127);
      memory.fill(0, pointers.io, pointers.io + 256);
      memory[pointers.io + 5] = correcting && !neverAlign ? (model ? 173 : 76) : (model ? 170 : 144);
      memory[pointers.io + 6] = 73;
      memory[pointers.io + 7] = wrongTimer ? 0 : 0xfe;
      memory[pointers.io + 15] = automatic ? 0xe5 : 0xe4;
      metadata = { tick: expectedTick + (correcting ? (model ? (fractionalTick ? -1 : -12) : 16) : 0),
        last_lcd_enable_tick: expectedLCD + (correcting ? (model ? -4 : 8) : (automatic ? 0 : -140448)),
        pc: 0x5fe7, rom_bank: 2 };
      return 1;
    },
    _lsdj_snapshot_wram: () => pointers.wram, _lsdj_snapshot_hram: () => pointers.hram,
    _lsdj_snapshot_song: () => pointers.song, _lsdj_snapshot_io: () => pointers.io,
    _lsdj_metadata: () => JSON.stringify(metadata), UTF8ToString: (value) => value,
    _lsdj_destroy() { this.destroyed++; },
  };
}

test('conversion selects a saved slot and aligns both models, LCD wrap and timer reload', async () => {
  const rom = sourceROM(), module = captureModule(), progress = [];
  const result = await convertPlayer({ rom, save: savedSlot(), project: 3, title: 'SELECTED',
    lowRange: true, connectBends: true, onProgress: (value) => progress.push(value) },
  { module, templates: assets, compatibility: compatibility(rom) });
  assert.equal(module.captures.length, 6);
  assert.equal(module.destroyed, 6);
  for (const source of module.captures.slice(0, 2)) {
    assert.equal(source.save[0x8140], 3);
    assert.ok(source.save.subarray(0, 32768).every((value) => value === 1));
    assert.equal(source.automatic, true);
  }
  assert.ok(module.captures.slice(2).every((capture) => !capture.automatic));
  const corrected = defaultTiming();
  corrected.song.dmg -= 70228; corrected.lcd.dmg += 70220; corrected.timer.dmg.tima = 29;
  corrected.song.cgb += 8; corrected.lcd.cgb += 4; corrected.timer.cgb.tima = 167;
  assertTiming(result, assets.templates['low-bends'], corrected);
  assertChecksums(result);
  assert.equal(progress.at(-1).progress, 1);
});

test('conversion rejects unsupported timer state and unrepresentable timing', async () => {
  const rom = sourceROM();
  for (const [options, message] of [[{ wrongTimer: true }, /Unsupported DMG song timer/],
    [{ fractionalTick: true }, /startup timing cannot be aligned/]]) {
    const module = captureModule(options);
    await assert.rejects(convertPlayer({ rom, save: savedSlot() },
      { module, templates: assets, compatibility: compatibility(rom) }), message);
    assert.equal(module.destroyed, module.captures.length);
  }
});

test('startup calibration stops after five attempts without publishing a ROM', async () => {
  const rom = sourceROM(), module = captureModule({ neverAlign: true });
  await assert.rejects(convertPlayer({ rom, save: savedSlot() },
    { module, templates: assets, compatibility: compatibility(rom) }), /did not converge/);
  assert.equal(module.captures.length, 12);
  assert.equal(module.destroyed, 12);
});
