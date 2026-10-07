// Browser-side counterpart of tools/save_format.py. This preserves song bytes;
// supported format migration remains the job of the user's supplied LSDj ROM.
// Block format: https://github.com/stijnfrishert/liblsdj/blob/master/liblsdj/src/compression.c
export const SONG_SIZE = 0x8000;
export const SAVE_SIZE = 0x20000;
const HEADER = 0x8000;
const BLOCK_BASE = 0x8200;
const BLOCK_SIZE = 0x200;
const BLOCK_COUNT = 191;
const KIT_BANK_SIZE = 0x4000;
const DEFAULT_WAVE = Uint8Array.from([
  0x8e, 0xcd, 0xcc, 0xbb, 0xaa, 0xa9, 0x99, 0x88,
  0x87, 0x76, 0x66, 0x55, 0x54, 0x43, 0x32, 0x31,
]);
const DEFAULT_INSTRUMENT = Uint8Array.from([
  0xa8, 0, 0, 0xff, 0, 0, 3, 0, 0, 0xd0, 0, 0, 0, 0xf3, 0, 0,
]);

export class SaveFormatError extends Error {
  constructor(message) {
    super(message);
    this.name = 'SaveFormatError';
  }
}

function requireBytes(data) {
  if (!(data instanceof Uint8Array)) {
    throw new SaveFormatError('Expected file contents as a Uint8Array');
  }
}

function kind(data) {
  requireBytes(data);
  if (data.length === 0x10000 || data.length === SAVE_SIZE) {
    if (data[0x813e] !== 0x6a || data[0x813f] !== 0x6b) {
      throw new SaveFormatError('Missing LSDj SRAM directory signature at 0x813e');
    }
    return 'save';
  }
  if (data.length >= 9 + BLOCK_SIZE && (data.length - 9) % BLOCK_SIZE === 0) {
    return 'project';
  }
  throw new SaveFormatError('Expected a 64/128 KiB .sav or a block-compressed .lsdsng/.lsdprj');
}

function projectName(raw) {
  let end = raw.length;
  while (end && (raw[end - 1] === 0 || raw[end - 1] === 0x20 || raw[end - 1] === 0xff)) end--;
  let name = '';
  // TextDecoder('ascii') actually decodes Windows-1252 in browsers. Match
  // Python's ASCII replacement behavior instead, including interior $ff.
  for (let i = 0; i < end; i++) name += String.fromCharCode(raw[i] < 128 ? raw[i] : 0xfffd);
  return name;
}

function decompress(data, start, base, followLinks, owners = null, owner = null) {
  const output = new Uint8Array(SONG_SIZE);
  const seen = new Set();
  let length = 0;
  let block = start;
  const reserve = (count) => {
    if (length + count > SONG_SIZE) throw new SaveFormatError('Compressed song expands beyond 32 KiB');
  };
  for (;;) {
    if (block < base || (block - base) % BLOCK_SIZE || block + BLOCK_SIZE > data.length) {
      throw new SaveFormatError('Compressed song refers to a missing or unaligned block');
    }
    const blockIndex = (block - base) / BLOCK_SIZE;
    if (seen.has(block)) throw new SaveFormatError('Compressed song contains a circular block link');
    if (owners && (blockIndex >= owners.length || owners[blockIndex] !== owner)) {
      throw new SaveFormatError('Compressed song block link crosses project ownership');
    }
    seen.add(block);
    let cursor = block;
    const end = block + BLOCK_SIZE;
    const readByte = () => {
      if (cursor >= end) throw new SaveFormatError('Compressed block has no terminator or an incomplete command');
      return data[cursor++];
    };
    let link = null;
    while (link === null) {
      const byte = readByte();
      if (byte === 0xc0) {
        const value = readByte();
        const count = value === 0xc0 ? 1 : readByte();
        reserve(count);
        output.fill(value, length, length + count);
        length += count;
      } else if (byte === 0xe0) {
        const action = readByte();
        if (action === 0xe0) {
          reserve(1);
          output[length++] = action;
        } else if (action === 0xf0 || action === 0xf1) {
          const count = readByte();
          const pattern = action === 0xf0 ? DEFAULT_WAVE : DEFAULT_INSTRUMENT;
          reserve(pattern.length * count);
          for (let i = 0; i < count; i++) {
            output.set(pattern, length);
            length += pattern.length;
          }
        } else {
          link = action;
        }
      } else {
        reserve(1);
        output[length++] = byte;
      }
    }
    if (link === 0xff) {
      if (length !== SONG_SIZE) {
        throw new SaveFormatError(`Song expands to ${length} bytes; expected ${SONG_SIZE}`);
      }
      return { song: output, end: block + BLOCK_SIZE };
    }
    if (followLinks) {
      if (link < 1 || link > BLOCK_COUNT) throw new SaveFormatError(`Invalid compressed block link ${link}`);
      block = base + (link - 1) * BLOCK_SIZE;
    } else {
      // Exported blocks are sequential; their old SRAM link numbers may
      // survive export and must not be interpreted as file offsets.
      block += BLOCK_SIZE;
    }
  }
}

function metadata(song, index, name, revision, working = false) {
  let songRows = 0;
  let noteCount = 0;
  for (let offset = 0x1290; offset < 0x1690; offset += 4) {
    if (song.subarray(offset, offset + 4).some((value) => value !== 0xff)) songRows++;
  }
  for (let offset = 0; offset < 0x0ff0; offset++) if (song[offset] !== 0) noteCount++;
  return {
    index, name, revision, format_version: song[0x7fff], working,
    song_rows: songRows, note_count: noteCount,
  };
}

function savedSong(data, index) {
  if (!Number.isInteger(index) || index < 0 || index >= 32) {
    throw new SaveFormatError('Project index must be between 0 and 31');
  }
  const owners = data.subarray(0x8141, 0x8200);
  const first = owners.indexOf(index);
  if (first < 0) throw new SaveFormatError(`Project slot ${index} is empty`);
  return decompress(data, BLOCK_BASE + first * BLOCK_SIZE, BLOCK_BASE, true, owners, index).song;
}

/** Validate the directory and compressed songs, returning project metadata.
 * A save includes {index:null, working:true}; an export contains index 0.
 * revision and format_version are reported unchanged, without guessing the
 * migration support of a ROM that has not yet been supplied.
 */
export function inspectSave(data) {
  if (kind(data) === 'project') {
    const { song } = decompress(data, 9, 9, false);
    return [metadata(song, 0, projectName(data.subarray(0, 8)), data[8])];
  }
  const active = data[0x8140];
  const name = active < 32 ? projectName(data.subarray(HEADER + active * 8, HEADER + active * 8 + 8)) : 'WORKING';
  const revision = active < 32 ? data[0x8100 + active] : 0;
  const entries = [metadata(data.subarray(0, SONG_SIZE), null, name || 'WORKING', revision, true)];
  const owners = [...new Set(data.subarray(0x8141, 0x8200))].filter((value) => value !== 0xff).sort((a, b) => a - b);
  const invalid = owners.filter((value) => value >= 32);
  if (invalid.length) {
    throw new SaveFormatError(`Invalid project indices in block allocation table: [${invalid.join(', ')}]`);
  }
  for (const index of owners) {
    entries.push(metadata(savedSong(data, index), index,
      projectName(data.subarray(HEADER + index * 8, HEADER + index * 8 + 8)), data[0x8100 + index]));
  }
  return entries;
}

/** Return a new 128 KiB SRAM image. Decode against the original file length,
 * before padding 64 KiB inputs, so links into absent blocks remain errors.
 */
export function prepareSave(data, project = 'working') {
  const inputKind = kind(data);
  const result = new Uint8Array(SAVE_SIZE);
  let song;
  if (inputKind === 'project') {
    if (project !== null && project !== 'working' && project !== 0) {
      throw new SaveFormatError('An exported project contains only project 0');
    }
    song = decompress(data, 9, 9, false).song;
    result.set([0x6a, 0x6b], 0x813e);
    result.fill(0xff, 0x8140, 0x8200);
  } else {
    result.fill(0xff);
    result.set(data);
    if (project === null || project === 'working') {
      song = data.subarray(0, SONG_SIZE);
    } else {
      if (!Number.isInteger(project)) {
        throw new SaveFormatError("Select 'working' or a zero-based numeric project slot");
      }
      song = savedSong(data, project);
      result[0x8140] = project;
    }
  }
  result.set(song);
  return result;
}

export function kitBank(index) {
  if (!Number.isInteger(index) || index < 0 || index > 50) {
    throw new SaveFormatError(`Kit ID ${index} does not fit the 1 MiB LSDj kit-bank layout`);
  }
  return index < 19 ? index + 8 : index + 13;
}

export function usedKitIndices(song) {
  requireBytes(song);
  if (song.length !== SONG_SIZE) throw new SaveFormatError('Expected a 32 KiB uncompressed song');
  const ids = new Set();
  for (let instrument = 0; instrument < 64; instrument++) {
    const base = 0x3080 + instrument * 16;
    if (song[base] === 2) {
      ids.add(song[base + 2] & 0x3f);
      ids.add(song[base + 9] & 0x3f);
    }
  }
  return [...ids].sort((a, b) => a - b);
}

/** Embedded LSDPatch banks follow the compressed stream, in kit-ID order. */
export function extractProjectKits(data) {
  if (kind(data) !== 'project') throw new SaveFormatError('Expected an exported .lsdsng/.lsdprj project');
  const { song, end } = decompress(data, 9, 9, false);
  const length = data.length - end;
  if (!length) return [];
  if (length % KIT_BANK_SIZE) throw new SaveFormatError('Project has a truncated appended sample-kit bank');
  const ids = usedKitIndices(song);
  const count = length / KIT_BANK_SIZE;
  if (count !== ids.length) {
    throw new SaveFormatError(`Project appends ${count} kits but its instruments reference ${ids.length} IDs`);
  }
  return ids.map((id, position) => ({ id, bank: kitBank(id),
    data: Uint8Array.from(data.subarray(end + position * KIT_BANK_SIZE, end + (position + 1) * KIT_BANK_SIZE)) }));
}
