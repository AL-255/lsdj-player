import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  SONG_SIZE, SAVE_SIZE, SaveFormatError, inspectSave, prepareSave,
  usedKitIndices, kitBank, extractProjectKits,
} from '../web/save-format.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const WAVE = Uint8Array.from([0x8e, 0xcd, 0xcc, 0xbb, 0xaa, 0xa9, 0x99, 0x88,
  0x87, 0x76, 0x66, 0x55, 0x54, 0x43, 0x32, 0x31]);
const INSTRUMENT = Uint8Array.from([0xa8, 0, 0, 0xff, 0, 0, 3, 0, 0, 0xd0, 0, 0, 0, 0xf3, 0, 0]);
const sha256 = (data) => createHash('sha256').update(data).digest('hex');

function concat(...parts) {
  const result = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function packedProject(tokens, links = []) {
  const blocks = [];
  let current = [];
  for (const token of tokens) {
    if (current.length + token.length + 2 > 512) {
      blocks.push(current);
      current = [];
    }
    current.push(...token);
  }
  blocks.push(current);
  const result = new Uint8Array(9 + blocks.length * 512);
  result.set(new TextEncoder().encode('BOUNDARY'));
  result[8] = 7;
  blocks.forEach((block, index) => {
    const start = 9 + index * 512;
    result.set(block, start);
    result.set([0xe0, index === blocks.length - 1 ? 0xff : (links[index] ?? index + 2)], start + block.length);
  });
  return result;
}

function repeats(length, value = 0) {
  const tokens = [];
  for (let i = 0; i < length; i += 255) tokens.push([0xc0, value, Math.min(255, length - i)]);
  return tokens;
}

function encodeSong(song) {
  const tokens = [];
  for (let start = 0; start < song.length;) {
    const value = song[start];
    if (value === 0xc0) {
      tokens.push([0xc0, 0xc0]);
      start++;
      continue;
    }
    let end = start + 1;
    while (end < song.length && end - start < 255 && song[end] === value) end++;
    tokens.push([0xc0, value, end - start]);
    start = end;
  }
  return packedProject(tokens);
}

function fragmentedSave() {
  const project = packedProject([...Array.from({ length: 600 }, () => [1]), ...repeats(SONG_SIZE - 600)]);
  assert.equal(project.length, 9 + 1024);
  const save = new Uint8Array(0x10000);
  save.set([0x6a, 0x6b], 0x813e);
  save.fill(0xff, 0x8140, 0x8200);
  save.set(new TextEncoder().encode('THREE'), 0x8018);
  save[0x8103] = 9;
  save[0x8140] = 3;
  save[0x8141] = save[0x8141 + 20] = 3;
  save.set(project.subarray(9, 521), 0x8200);
  save[0x8200 + 511] = 21;
  save.set(project.subarray(521), 0x8200 + 20 * 512);
  return save;
}

function errorMatches(action, expression) {
  assert.throws(action, (error) => error instanceof SaveFormatError && expression.test(error.message));
}

test('special expansions and escaped markers preserve every song byte', () => {
  const prefix = concat(WAVE, INSTRUMENT, [0xc0, 0xe0]);
  const project = packedProject([[0xe0, 0xf0, 1], [0xe0, 0xf1, 1], [0xc0, 0xc0], [0xe0, 0xe0],
    ...repeats(SONG_SIZE - prefix.length)]);
  const save = prepareSave(project);
  assert.deepEqual(save.subarray(0, SONG_SIZE), concat(prefix, new Uint8Array(SONG_SIZE - prefix.length)));
  assert.equal(save.length, SAVE_SIZE);
  assert.deepEqual(save.subarray(0x813e, 0x8140), Uint8Array.of(0x6a, 0x6b));
  assert.equal(inspectSave(project)[0].name, 'BOUNDARY');
  assert.deepEqual(prepareSave(project, 0), save);
  errorMatches(() => prepareSave(project, 1), /only project 0/);
});

test('exported blocks are sequential even with old nonsequential SRAM links', () => {
  const project = packedProject(Array.from({ length: SONG_SIZE }, () => [1]), Array(100).fill(190));
  assert.deepEqual(prepareSave(project).subarray(0, SONG_SIZE), new Uint8Array(SONG_SIZE).fill(1));
});

test('fragmented 64 KiB SRAM preserves its input, directory, and FF padding', () => {
  const original = fragmentedSave();
  const before = original.slice();
  const entries = inspectSave(original);
  assert.equal(entries.length, 2);
  assert.deepEqual(entries.map(({ index, name, revision, working }) => ({ index, name, revision, working })), [
    { index: null, name: 'THREE', revision: 9, working: true },
    { index: 3, name: 'THREE', revision: 9, working: false },
  ]);
  const save = prepareSave(original, 3);
  assert.deepEqual(save.subarray(0, SONG_SIZE), concat(new Uint8Array(600).fill(1), new Uint8Array(SONG_SIZE - 600)));
  assert.deepEqual(original, before);
  assert.deepEqual(save.subarray(0x10000), new Uint8Array(0x10000).fill(0xff));
  assert.equal(save[0x8140], 3);
  assert.deepEqual(prepareSave(original).subarray(0, SONG_SIZE), original.subarray(0, SONG_SIZE));
  errorMatches(() => prepareSave(original, '3'), /numeric project slot/);
  errorMatches(() => prepareSave(original, 32), /between 0 and 31/);
  errorMatches(() => prepareSave(original, 2), /empty/);
});

test('SRAM rejects ownership crossings, cycles, invalid allocation and absent 64 KiB blocks', () => {
  let save = fragmentedSave();
  save[0x8141 + 20] = 4;
  errorMatches(() => prepareSave(save, 3), /ownership/);
  save = fragmentedSave();
  save[0x8200 + 511] = 1;
  errorMatches(() => prepareSave(save, 3), /circular/);
  save = fragmentedSave();
  save[0x8200 + 511] = 0;
  errorMatches(() => prepareSave(save, 3), /Invalid compressed block link 0/);
  save = fragmentedSave();
  save[0x8141 + 100] = 3;
  save[0x8200 + 511] = 101;
  errorMatches(() => prepareSave(save, 3), /missing or unaligned/);
  errorMatches(() => inspectSave(save), /missing or unaligned/);
  // Padding must never turn an absent original block into an available one.
  assert.equal(prepareSave(save, 'working').length, SAVE_SIZE);
  save = fragmentedSave();
  save[0x8141 + 20] = 32;
  errorMatches(() => inspectSave(save), /Invalid project indices.*32/);
});

test('malformed blocks fail with bounded expansion and actionable errors', () => {
  errorMatches(() => prepareSave(packedProject(Array.from({ length: 129 }, () => [0xc0, 1, 255]))), /beyond 32 KiB/);
  errorMatches(() => prepareSave(packedProject([[1]])), /expands to 1 bytes; expected 32768/);
  errorMatches(() => inspectSave(new Uint8Array(0x10000)), /signature/);
  errorMatches(() => inspectSave(new Uint8Array(500)), /Expected a 64\/128 KiB/);
  const unterminated = new Uint8Array(9 + 512).fill(1);
  errorMatches(() => prepareSave(unterminated), /no terminator/);
  unterminated[9 + 510] = 0xe0;
  unterminated[9 + 511] = 0xf0;
  errorMatches(() => prepareSave(unterminated), /incomplete command/);
});

test('revision and song format are surfaced unchanged for ROM migration', () => {
  const song = new Uint8Array(SONG_SIZE);
  song.fill(0xff, 0x1290, 0x1690);
  song[0x1290] = 0;
  song[0x1297] = 1;
  song[17] = 2;
  song[0x7fff] = 0xfe;
  const project = encodeSong(song);
  project.set([0x41, 0x80, 0xff, 0, 0x20, 0xff, 0, 0x20]);
  project[8] = 0xfd;
  assert.deepEqual(inspectSave(project), [{ index: 0, name: 'A\ufffd', revision: 0xfd,
    format_version: 0xfe, working: false, song_rows: 2, note_count: 1 }]);
  assert.deepEqual(prepareSave(project).subarray(0, SONG_SIZE), song);
});

test('sample-kit IDs preserve flags and skip reserved ROM banks', () => {
  const song = new Uint8Array(SONG_SIZE);
  song[0x3080] = song[0x3090] = 2;
  song[0x3082] = 0xc7;
  song[0x3089] = 0x8a;
  song[0x3092] = song[0x3099] = 0x47;
  assert.deepEqual(usedKitIndices(song), [7, 10]);
  assert.deepEqual([0, 18, 19, 50].map(kitBank), [8, 26, 32, 63]);
  for (const id of [-1, 51, 63, 1.5]) errorMatches(() => kitBank(id), /Kit ID/);
  errorMatches(() => usedKitIndices(new Uint8Array(3)), /32 KiB/);
  const project = encodeSong(song);
  const first = new Uint8Array(0x4000).fill(0x23);
  const second = new Uint8Array(0x4000).fill(0x45);
  const kits = extractProjectKits(concat(project, first, second));
  assert.deepEqual(kits.map(({ id, bank }) => ({ id, bank })), [{ id: 7, bank: 15 }, { id: 10, bank: 18 }]);
  assert.deepEqual(kits[0].data, first);
  assert.deepEqual(kits[1].data, second);
  assert.deepEqual(extractProjectKits(project), []);
  errorMatches(() => extractProjectKits(concat(project, first)), /appends 1 kits.*reference 2/);
  errorMatches(() => extractProjectKits(concat(project, new Uint8Array(512))), /truncated appended/);
});

function corpusFiles(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? corpusFiles(file) : (/\.(sav|lsdsng|lsdprj)$/i.test(entry.name) ? [file] : []);
  });
}

test('all available local corpus files match Python metadata and prepared-save hashes', (context) => {
  const files = [...new Set([
    'tests/corpus/files', 'tests/corpus/downloads', 'reference/liblsdj/resources',
    'reference/lsdpatch/src/test/resources',
  ].flatMap((directory) => corpusFiles(path.join(ROOT, directory))))].sort();
  if (!files.length) return context.skip('Local optional corpus is not installed');
  const script = `
import hashlib,json,pathlib,sys
sys.path.insert(0,str(pathlib.Path.cwd()/'tools'))
import save_format as sf
import project_kits as pk
output=[]
for filename in json.load(sys.stdin):
    data=pathlib.Path(filename).read_bytes()
    try:
        entries=sf.list_projects(data)
        selections=['working']+[entry['index'] for entry in entries if entry['index'] is not None]
        result={'file':filename,'entries':entries,'prepared':[]}
        for selection in selections:
            prepared=sf.prepare_song(data,selection)
            result['prepared'].append({'selection':selection,'sha256':hashlib.sha256(prepared).hexdigest(),
                                       'kit_ids':pk.used_kit_indices(prepared[:sf.SONG_SIZE])})
    except sf.SaveFormatError as error:
        result={'file':filename,'error':str(error)}
    output.append(result)
print(json.dumps(output))
`;
  const expected = JSON.parse(execFileSync('python3', ['-c', script], {
    cwd: ROOT, input: JSON.stringify(files), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
  }));
  let selections = 0;
  for (const item of expected) {
    const input = new Uint8Array(readFileSync(item.file));
    if (item.error) {
      assert.throws(() => inspectSave(input), (error) => error instanceof SaveFormatError && error.message === item.error,
        path.relative(ROOT, item.file));
      continue;
    }
    assert.deepEqual(inspectSave(input), item.entries, path.relative(ROOT, item.file));
    for (const prepared of item.prepared) {
      const result = prepareSave(input, prepared.selection);
      assert.equal(sha256(result), prepared.sha256, `${item.file}: project ${prepared.selection}`);
      assert.deepEqual(usedKitIndices(result.subarray(0, SONG_SIZE)), prepared.kit_ids);
      selections++;
    }
  }
  context.diagnostic(`Python parity: ${files.length} files and ${selections} selected songs`);
});
