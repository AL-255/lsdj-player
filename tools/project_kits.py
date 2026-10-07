"""Preserve sample-kit banks appended to LSDPatch exported projects.

Format reference:
https://github.com/jkotlinski/lsdpatch/blob/master/src/main/java/Document/LSDSavFile.java
LSDPatch exports kits in increasing original ID order. Each kit occupies
one whole 16 KiB ROM bank; legacy reserved code banks 27..31 are skipped.
"""

from __future__ import annotations

import hashlib

try:
    from . import save_format as sf
except ImportError:
    import save_format as sf

BANK_SIZE = 0x4000
KIT_BANKS = tuple(range(8, 27)) + tuple(range(32, 64))


def kit_bank(index: int) -> int:
    """Map an instrument's logical kit ID to its legacy ROM bank."""
    if not 0 <= index < len(KIT_BANKS):
        raise sf.SaveFormatError(f"Kit ID {index} does not fit the 1 MiB LSDj kit-bank layout")
    return KIT_BANKS[index]


def instrument_kit_locations(song: bytes) -> list[int]:
    if len(song) != sf.SONG_SIZE:
        raise sf.SaveFormatError("Expected a 32 KiB uncompressed song")
    return [base + field for instrument in range(64)
            for base in (0x3080 + instrument * 16,)
            if song[base] == 2 for field in (2, 9)]


def used_kit_indices(song: bytes) -> list[int]:
    return sorted({song[offset] & 0x3F for offset in instrument_kit_locations(song)})


def extract_project_kits(source: sf.Source) -> list[tuple[int, bytes]]:
    """Return appended (original kit ID, 16 KiB bank) pairs.

    A project without embedded banks returns an empty list and depends on
    the caller's source ROM for any kit instruments. Partial banks or an
    appended bank count that differs from the referenced IDs are rejected.
    """
    song, payload = sf.read_exported_project(source)
    if not payload:
        return []
    if len(payload) % BANK_SIZE:
        raise sf.SaveFormatError("Project has a truncated appended sample-kit bank")
    indices = used_kit_indices(song)
    count = len(payload) // BANK_SIZE
    if count != len(indices):
        raise sf.SaveFormatError(f"Project appends {count} kits but its instruments reference {len(indices)} IDs")
    for index in indices:
        kit_bank(index)
    return [(index, payload[position * BANK_SIZE:(position + 1) * BANK_SIZE])
            for position, index in enumerate(indices)]


def patch_project_kits(source: sf.Source, prepared_save: bytes, rom: bytes,
                       remap: dict[int, int] | None = None) -> tuple[bytes, bytes, dict]:
    """Install embedded kits into a disposable recording ROM and working SRAM.

    By default the original kit IDs are retained. Each converted song has
    its own temporary source ROM, so existing standard kits can be replaced
    safely without competing with another song's banks. An explicit remap
    may move IDs into other legal kit banks and preserves pitch/flag bits
    above the six-bit kit ID. Header checksums are updated after copying.
    """
    if len(prepared_save) != sf.SAVE_SIZE:
        raise sf.SaveFormatError("Prepared SRAM must be 128 KiB")
    if len(rom) != 64 * BANK_SIZE:
        raise sf.SaveFormatError("Sample-kit patching requires a 1 MiB LSDj ROM")
    kits = extract_project_kits(source)
    mapping = {old: (remap.get(old, old) if remap else old) for old, _ in kits}
    if len(set(mapping.values())) != len(mapping):
        raise sf.SaveFormatError("Multiple appended kits map to the same destination bank")
    updated_save, updated_rom = bytearray(prepared_save), bytearray(rom)
    records = []
    for old, data in kits:
        new = mapping[old]
        bank = kit_bank(new)
        updated_rom[bank * BANK_SIZE:(bank + 1) * BANK_SIZE] = data
        records.append({"source_id": old, "destination_id": new, "bank": bank,
                        "sha256": hashlib.sha256(data).hexdigest()})
    for offset in instrument_kit_locations(prepared_save[:sf.SONG_SIZE]):
        value = updated_save[offset]
        if (value & 0x3F) in mapping:
            updated_save[offset] = (value & 0xC0) | mapping[value & 0x3F]
    if kits:
        checksum = 0
        for value in updated_rom[0x134:0x14D]:
            checksum = (checksum - value - 1) & 0xFF
        updated_rom[0x14D] = checksum
        checksum = (sum(updated_rom) - updated_rom[0x14E] - updated_rom[0x14F]) & 0xFFFF
        updated_rom[0x14E:0x150] = checksum.to_bytes(2, "big")
    return bytes(updated_save), bytes(updated_rom), {"kit_count": len(kits), "kits": records}
