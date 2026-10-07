"""Read LSDj SRAM and exported projects without rewriting tracker data.

The block format is documented by libLSDJ (MIT, Stijn Frishert):
https://github.com/stijnfrishert/liblsdj/blob/master/liblsdj/src/compression.c
An exported .lsdsng/.lsdprj is an eight-byte name, a revision byte, and
sequential 512-byte compressed blocks. SRAM follows block links instead.
Song-format migration remains the job of the supplied LSDj ROM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Union

SONG_SIZE = 0x8000
SAVE_SIZE = 0x20000
HEADER = 0x8000
BLOCK_BASE = 0x8200
BLOCK_SIZE = 0x200
BLOCK_COUNT = 191
DEFAULT_WAVE = bytes.fromhex("8e cd cc bb aa a9 99 88 87 76 66 55 54 43 32 31")
DEFAULT_INSTRUMENT = bytes.fromhex("a8 00 00 ff 00 00 03 00 00 d0 00 00 00 f3 00 00")
Source = Union[str, Path, bytes, bytearray, memoryview]


class SaveFormatError(ValueError):
    """The file is truncated, corrupt, or not an LSDj save/project."""


def _read(source: Source) -> tuple[bytes, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        return path.read_bytes(), path.suffix.lower()
    return bytes(source), ""


def _kind(data: bytes, suffix: str) -> str:
    if suffix in (".lsdsng", ".lsdprj"):
        return "project"
    if suffix == ".sav" or len(data) in (0x10000, SAVE_SIZE):
        if len(data) not in (0x10000, SAVE_SIZE):
            raise SaveFormatError("LSDj SRAM must be 64 KiB or 128 KiB")
        if data[0x813E:0x8140] != b"jk":
            raise SaveFormatError("Missing LSDj SRAM directory signature at 0x813e")
        return "save"
    if len(data) >= 9 + BLOCK_SIZE and (len(data) - 9) % BLOCK_SIZE == 0:
        return "project"
    raise SaveFormatError("Expected a 64/128 KiB .sav or a block-compressed .lsdsng/.lsdprj")


def _name(raw: bytes) -> str:
    return raw.rstrip(b"\x00 \xff").decode("ascii", errors="replace")


def _decompress(data: bytes, start: int, base: int, follow_links: bool,
                owners: bytes | None = None, owner: int | None = None,
                return_end: bool = False) -> bytes | tuple[bytes, int]:
    output = bytearray()
    seen: set[int] = set()
    block = start
    while True:
        if block < base or (block - base) % BLOCK_SIZE or block + BLOCK_SIZE > len(data):
            raise SaveFormatError("Compressed song refers to a missing or unaligned block")
        block_index = (block - base) // BLOCK_SIZE
        if block in seen:
            raise SaveFormatError("Compressed song contains a circular block link")
        if owners is not None and (block_index >= len(owners) or owners[block_index] != owner):
            raise SaveFormatError("Compressed song block link crosses project ownership")
        seen.add(block)
        cursor, end = block, block + BLOCK_SIZE

        def read_byte() -> int:
            nonlocal cursor
            if cursor >= end:
                raise SaveFormatError("Compressed block has no terminator or an incomplete command")
            byte = data[cursor]
            cursor += 1
            return byte

        link = None
        while link is None:
            byte = read_byte()
            if byte == 0xC0:
                byte = read_byte()
                output.extend(bytes([byte]) if byte == 0xC0 else bytes([byte]) * read_byte())
            elif byte == 0xE0:
                action = read_byte()
                if action == 0xE0:
                    output.append(action)
                elif action == 0xF0:
                    output.extend(DEFAULT_WAVE * read_byte())
                elif action == 0xF1:
                    output.extend(DEFAULT_INSTRUMENT * read_byte())
                else:
                    link = action
            else:
                output.append(byte)
            if len(output) > SONG_SIZE:
                raise SaveFormatError("Compressed song expands beyond 32 KiB")
        if link == 0xFF:
            if len(output) != SONG_SIZE:
                raise SaveFormatError(f"Song expands to {len(output)} bytes; expected {SONG_SIZE}")
            return (bytes(output), block + BLOCK_SIZE) if return_end else bytes(output)
        if follow_links:
            if not 1 <= link <= BLOCK_COUNT:
                raise SaveFormatError(f"Invalid compressed block link {link}")
            block = base + (link - 1) * BLOCK_SIZE
        else:
            # Exported projects store the block chain sequentially. Link bytes
            # may retain the original SRAM block numbers and are ignored.
            block += BLOCK_SIZE


def _metadata(song: bytes, index: int | None, name: str, revision: int,
              working: bool = False) -> dict:
    matrix = song[0x1290:0x1690]
    return {"index": index, "name": name, "revision": revision,
            "format_version": song[0x7FFF], "working": working,
            "song_rows": sum(any(value != 0xFF for value in matrix[row:row + 4])
                             for row in range(0, len(matrix), 4)),
            "note_count": sum(value != 0 for value in song[:0x0FF0])}


def _saved_song(data: bytes, index: int) -> bytes:
    if not 0 <= index < 32:
        raise SaveFormatError("Project index must be between 0 and 31")
    owners = data[0x8141:0x8200]
    try:
        first = owners.index(index)
    except ValueError:
        raise SaveFormatError(f"Project slot {index} is empty") from None
    return _decompress(data, BLOCK_BASE + first * BLOCK_SIZE, BLOCK_BASE,
                       True, owners, index)


def read_exported_project(source: Source) -> tuple[bytes, bytes]:
    """Return uncompressed song and appended payload after the final block.

    LSDPatch project exports can append complete 16 KiB sample-kit ROM
    banks. These are outside the compressed song stream and must not be
    confused with its 512-byte blocks.
    """
    data, suffix = _read(source)
    if _kind(data, suffix) != "project":
        raise SaveFormatError("Expected an exported .lsdsng/.lsdprj project")
    song, end = _decompress(data, 9, 9, False, return_end=True)
    return song, data[end:]


def list_projects(source: Source, include_working: bool = True) -> list[dict]:
    """Return project metadata; working SRAM uses index=None and working=True.

    Saved project indices are the actual zero-based LSDj directory slots.
    Exported project files have one entry at index=0. Decoding also validates
    block boundaries, ownership, cycles, and the uncompressed song length.
    """
    data, suffix = _read(source)
    if _kind(data, suffix) == "project":
        song = _decompress(data, 9, 9, False)
        return [_metadata(song, 0, _name(data[:8]), data[8])]
    entries = []
    if include_working:
        active = data[0x8140]
        name = _name(data[HEADER + active * 8:HEADER + active * 8 + 8]) if active < 32 else "WORKING"
        revision = data[0x8100 + active] if active < 32 else 0
        entries.append(_metadata(data[:SONG_SIZE], None, name or "WORKING", revision, True))
    owners = data[0x8141:0x8200]
    invalid = sorted({index for index in owners if index != 0xFF and index >= 32})
    if invalid:
        raise SaveFormatError(f"Invalid project indices in block allocation table: {invalid}")
    for index in sorted(set(owners) - {0xFF}):
        song = _saved_song(data, index)
        entries.append(_metadata(song, index, _name(data[HEADER + index * 8:HEADER + index * 8 + 8]),
                                 data[0x8100 + index]))
    return entries


def prepare_song(source: Source, project: int | str | None = None) -> bytes:
    """Return 128 KiB SRAM with the selected song in working memory.

    None or 'working' selects SRAM working memory. An integer selects a
    zero-based directory slot. For .lsdsng/.lsdprj, None/'working'/0 select
    the single exported project. The input and song-format version are
    preserved; the LSDj ROM performs its own supported migrations on boot.
    """
    data, suffix = _read(source)
    kind = _kind(data, suffix)
    if kind == "project":
        if project not in (None, "working", 0):
            raise SaveFormatError("An exported project contains only project 0")
        song = _decompress(data, 9, 9, False)
        result = bytearray(SAVE_SIZE)
        result[0x813E:0x8140] = b"jk"
        result[0x8140:0x8200] = b"\xff" * 0xC0
    else:
        result = bytearray(data)
        result.extend(b"\xff" * (SAVE_SIZE - len(result)))
        if project in (None, "working"):
            song = data[:SONG_SIZE]
        else:
            if not isinstance(project, int):
                raise SaveFormatError("Select 'working' or a zero-based numeric project slot")
            song = _saved_song(data, project)
            result[0x8140] = project
    result[:SONG_SIZE] = song
    return bytes(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and prepare LSDj SRAM/projects")
    parser.add_argument("input", type=Path)
    parser.add_argument("--project", default="working", help="working or a zero-based directory slot")
    parser.add_argument("--output", type=Path, help="write normalized 128 KiB SRAM")
    args = parser.parse_args()
    try:
        entries = list_projects(args.input)
        if args.output:
            selection = args.project if args.project == "working" else int(args.project, 0)
            args.output.write_bytes(prepare_song(args.input, selection))
        print(json.dumps(entries, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
