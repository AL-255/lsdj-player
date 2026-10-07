# LSDj 9.4.2 native standalone player

This repository contains the restored 128 KiB native interpreter prototype from
the checkpoint requested during development. Its shared Game Boy assembly
engine interprets native song data; ROM size does not grow with playback duration.
The same image detects DMG/CGB hardware and initializes the appropriate palette.

**Experimental:** this checkpoint still has known audio and scrolling problems.
It has not passed bit-exact A/B verification. This commit preserves the requested
rollback point; it does not claim that those issues are fixed.

## Dependencies

RGBDS, Python 3.10+, clang, make, git, and a locally supplied LSDj 9.4.2 ROM.
Initialize the pinned reference repositories:

```sh
git submodule update --init --recursive
python3 tools/build_sameboy.py --output build/sameboy-native-analysis
```

The reference repositories are submodules. ROMs, recovered engine assembly,
startup snapshots, recordings, screenshots, emulator builds and downloaded songs
are local artifacts excluded by `.gitignore`.

## Build the native prototype

Put your tracker ROM at `rom/lsdj9_4_2.gb`. Prepare the selected save using
`tools/save_format.py`. The capture tool supports `--execution-profile`,
`--break-pc 2:0x5fe7` and `--native-dump` to obtain local instruction coverage and
startup state. Use the same normalized save and Start input sequence in DMG and
CGB mode; startup snapshot prefixes must end in `-dmg` and `-cgb`.

With those local inputs available:

```sh
python3 tools/build_native_player.py \
  --rom rom/lsdj9_4_2.gb \
  --profile build/audio-ab/native-triac-execution.tsv \
  --snapshot-prefix build/native-engine/triac \
  --output build/native-engine/TRIAC-native.gb \
  --name TRIAC
```

`src/native/boot.asm` hosts the shared sequencer and interrupt handling.
`src/native/display.asm` renders the playback information and live pitch pixels.
The builder adapts the shared display primitives in `src/exact_ui.asm` and
`src/waterfall.asm`; the previous recorded playback implementation is excluded
from this repository. `tools/recover_native_engine.py` can independently verify
that recovered engine banks assemble byte-for-byte to the locally supplied ROM.

## Download test cases

```sh
python3 tools/download_test_saves.py
```

The downloader requires 7-Zip (`7zz` or `7z`), checks pinned SHA-256 hashes and
records source/license metadata. Downloads go into ignored `tests/corpus/`.
Use `--refresh` to discover the current Defense Mechanism catalog.
