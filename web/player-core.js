// SPDX-License-Identifier: GPL-2.0-or-later
import {prepareSave, usedKitIndices, kitBank} from './save-format.js';

const BANK = 0x4000, FRAME_TICKS = 140448;
const shortDelays = [new Uint8Array()];
for (let n = 1; n < 46; n++) {
  const choices = [Uint8Array.from([...shortDelays[n - 1], 0])];
  if (n >= 3) choices.push(Uint8Array.from([...shortDelays[n - 3], 0x18, 0]));
  if (n >= 7) choices.push(Uint8Array.from([...shortDelays[n - 7], 0xf5, 0xf1]));
  shortDelays.push(choices.reduce((a, b) => b.length < a.length ? b : a));
}

export function delayCode(cycles) {
  if (!Number.isSafeInteger(cycles) || cycles < 0 || cycles % 4) throw new Error('Invalid startup delay.');
  const out = [];
  while (cycles > 180) {
    const count = Math.min(0xfdff, Math.floor((cycles - 64) / 28));
    out.push(0xf5, 0xc5, 0x01, count & 255, count >> 8, 0x0b, 0x78, 0xb1, 0x20, 0xfb, 0xc1, 0xf1);
    cycles -= 28 * count + 64;
  }
  return Uint8Array.from([...out, ...shortDelays[cycles / 4]]);
}

export function fixChecksums(rom) {
  let header = 0;
  for (let i = 0x134; i < 0x14d; i++) header = (header - rom[i] - 1) & 255;
  rom[0x14d] = header;
  rom[0x14e] = rom[0x14f] = 0;
  let sum = 0;
  for (const byte of rom) sum = (sum + byte) & 65535;
  rom[0x14e] = sum >> 8; rom[0x14f] = sum & 255;
  return rom;
}

export async function validateROM(rom, compatibility) {
  if (!(rom instanceof Uint8Array) || rom.length !== compatibility.romSize) {
    throw new Error('Choose a 1 MiB LSDj 9.4.2 ROM (.gb or .gbc).');
  }
  for (const [bankText, expected] of Object.entries(compatibility.engineBanks)) {
    const bank = Number(bankText), bytes = rom.slice(bank * BANK, (bank + 1) * BANK);
    if (bank === 0) bytes.fill(0, ...compatibility.bank0IgnoredHeader);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    const actual = [...digest].map(x => x.toString(16).padStart(2, '0')).join('');
    if (actual !== expected) throw new Error('This ROM’s playback engine is not supported. Use LSDj 9.4.2; custom sample kits are supported.');
  }
}

function patchTiming(rom, template, timing) {
  for (const model of ['dmg', 'cgb']) {
    for (const [kind, cycles] of [['Song', timing.song[model]], ['LCD', timing.lcd[model]]]) {
      const slot = template.delays[kind + model.toUpperCase()];
      const bytes = delayCode(cycles - 16);
      if (bytes.length + 3 > slot.length) throw new Error('Song startup exceeds the supported timing range.');
      rom.fill(0, slot.offset, slot.offset + slot.length);
      rom.set(bytes, slot.offset);
      rom.set([0xc3, slot.endAddress & 255, slot.endAddress >> 8], slot.offset + bytes.length);
    }
    for (const key of ['tima', 'tma', 'tac', 'if']) rom[template.timers[model][key]] = timing.timer[model][key];
  }
  fixChecksums(rom);
}

export function assemblePlayer(source, snapshots, template, glyphs, title, timing) {
  const song = snapshots.dmg.song;
  if (song.length !== 32768 || snapshots.cgb.song.length !== 32768 || snapshots.cgb.song.some((b, i) => b !== song[i])) {
    throw new Error('DMG and CGB prepared different song data. Save the song again in LSDj 9.4.2 and retry.');
  }
  const kits = usedKitIndices(song).map(kitBank);
  let size = 0x20000;
  while (size < (Math.max(7, ...kits) + 1) * BANK) size *= 2;
  const rom = new Uint8Array(size);
  for (const bank of [0, 2, 7, ...kits]) rom.set(source.subarray(bank * BANK, (bank + 1) * BANK), bank * BANK);
  for (const patch of template.patches) rom.set(Uint8Array.from(atob(patch.data), c => c.charCodeAt(0)), patch.offset);
  rom.set([0x80, 0xcc], 0x188a); // Redirect retired tracker font writes to WRAM.
  rom.set(song.subarray(0, BANK), BANK); rom.set(song.subarray(BANK), 3 * BANK);
  rom.set(snapshots.dmg.wram, 4 * BANK); rom.set(snapshots.cgb.wram, 4 * BANK + 8192);
  rom.set(snapshots.dmg.hram, 5 * BANK); rom.set(snapshots.cgb.hram, 5 * BANK + 127);
  const text = String(title || 'SONG').toUpperCase().slice(0, 8).padEnd(8, ' ');
  let cursor = template.titleOffset;
  for (const character of text) {
    for (const row of ['00000', ...(glyphs[character] || glyphs['?'])]) {
      const byte = parseInt(row, 2) << 2; rom[cursor++] = byte; rom[cursor++] = byte;
    }
  }
  rom[0x143] = 0x80; rom[0x147] = 0x1a;
  rom[0x148] = Math.log2(size / 32768); rom[0x149] = 3;
  patchTiming(rom, template, timing);
  return rom;
}

export async function captureStartup(module, rom, save, model, {automatic = true, signal, maxFrames = 900} = {}) {
  const romPtr = module._malloc(rom.length), savePtr = module._malloc(save.length);
  try {
    module.HEAPU8.set(rom, romPtr); module.HEAPU8.set(save, savePtr);
    const status = module._lsdj_init(romPtr, rom.length, savePtr, save.length, model === 'cgb' ? 1 : 0);
    if (status) throw new Error('Unable to open the selected ROM and save in the converter.');
  } finally { module._free(romPtr); module._free(savePtr); }
  module._lsdj_set_auto_buttons(automatic ? 1 : 0);
  try {
    for (let frame = 0; frame < maxFrames; frame += 8) {
      if (signal?.aborted) throw new Error('Conversion cancelled.');
      const status = module._lsdj_run_frames(8);
      if (status < 0) throw new Error('The emulator could not prepare this song.');
      if (status === 1) {
        const copy = (pointer, length) => module.HEAPU8.slice(pointer, pointer + length);
        return {wram: copy(module._lsdj_snapshot_wram(), 8192), hram: copy(module._lsdj_snapshot_hram(), 127),
                song: copy(module._lsdj_snapshot_song(), 32768), io: copy(module._lsdj_snapshot_io(), 256),
                metadata: JSON.parse(module.UTF8ToString(module._lsdj_metadata()))};
      }
      // Allow cancellation/progress and leave the main browser thread responsive.
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    throw new Error(`The song did not start on ${model.toUpperCase()}. Open and save it in LSDj 9.4.2, then retry.`);
  } finally { module._lsdj_destroy(); }
}

export async function convertPlayer({rom, save, project = 'working', title = 'SONG', lowRange = false,
                                     connectBends = false, onProgress = () => {}, signal}, dependencies) {
  const {module, templates, compatibility} = dependencies;
  onProgress({progress: .03, message: 'Checking your files…'});
  await validateROM(rom, compatibility);
  const prepared = prepareSave(save, project), snapshots = {};
  for (const [index, model] of ['dmg', 'cgb'].entries()) {
    onProgress({progress: .12 + index * .18, message: `Preparing the song for ${model.toUpperCase()}…`});
    snapshots[model] = await captureStartup(module, rom, prepared, model, {signal});
  }
  const template = templates.templates[(lowRange ? 'low' : 'full') + (connectBends ? '-bends' : '-points')];
  const timing = {song: {cgb: 8220996, dmg: 8547120}, lcd: {cgb: 41700, dmg: 36960}, timer: {}};
  for (const model of ['dmg', 'cgb']) {
    const io = snapshots[model].io;
    if ((io[7] & 7) !== 6) throw new Error(`Unsupported ${model.toUpperCase()} song timer state.`);
    timing.timer[model] = {tima: io[5], tma: io[6], tac: io[7] & 7, if: io[15] & 0x1e};
  }
  let result = assemblePlayer(rom, snapshots, template, templates.glyphs, title, timing);
  // Match pre-song entry, timer state and LCD phase on both models, exactly
  // as the native verifier does. This is not a claim of bit-exact audio.
  for (let attempt = 0; attempt < 5; attempt++) {
    let aligned = true;
    for (const model of ['dmg', 'cgb']) {
      onProgress({progress: .5 + attempt * .085, message: `Checking ${model.toUpperCase()} playback startup…`});
      const observed = await captureStartup(module, result, new Uint8Array(0x20000), model, {automatic: false, signal});
      const expected = snapshots[model], scale = model === 'cgb' ? 1 : 2;
      const timeDelta = expected.metadata.tick - observed.metadata.tick;
      const lcdDelta = ((expected.metadata.last_lcd_enable_tick - observed.metadata.last_lcd_enable_tick) % FRAME_TICKS + FRAME_TICKS) % FRAME_TICKS;
      const correctTimer = observed.io[5] === expected.io[5] && (observed.io[15] & 0x1e) === (expected.io[15] & 0x1e);
      if (!timeDelta && !lcdDelta && correctTimer) continue;
      aligned = false;
      if (timeDelta % (4 * scale) || lcdDelta % (4 * scale)) throw new Error('This song’s startup timing cannot be aligned.');
      timing.song[model] += (timeDelta - lcdDelta) / scale;
      timing.lcd[model] += lcdDelta / scale;
      if (timing.song[model] < 16) throw new Error('This song starts too early for the player bootstrap.');
      const initial = timing.timer[model].tima, final = observed.io[5], tma = timing.timer[model].tma;
      const increments = final >= initial ? final - initial : 256 - initial + final - tma;
      const corrected = expected.io[5] - increments;
      if (corrected < 0 || corrected > 255 || (final < initial && final < tma)) throw new Error('Could not restore the song’s startup timer.');
      timing.timer[model].tima = corrected;
    }
    if (aligned) { onProgress({progress: 1, message: 'Your player ROM is ready.'}); return result; }
    patchTiming(result, template, timing);
  }
  throw new Error('Startup verification did not converge. Try saving the song again in LSDj 9.4.2.');
}
