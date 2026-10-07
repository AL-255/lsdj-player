# LSDj 9.4.2 native standalone player

This repository contains the restored 128 KiB native interpreter prototype from
the checkpoint requested during development. Its shared Game Boy assembly
engine interprets native song data; ROM size does not grow with playback duration.
The same image detects DMG/CGB hardware and initializes the appropriate palette.

**Experimental:** the piano-roll map preparation and DMG sprite corruption
have regression fixes, but the native interpreter has not passed bit-exact
audio A/B verification against LSDj. The earlier recorded-replay results do
not establish audio equivalence for this native interpreter.

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
  --name TRIAC --align-startup
```

`src/native/boot.asm` hosts the shared sequencer and interrupt handling.
`src/native/display.asm` renders the playback information and live pitch pixels.
The builder adapts the shared display primitives in `src/exact_ui.asm` and
`src/waterfall.asm`; the previous recorded playback implementation is excluded
from this repository. `tools/recover_native_engine.py` can independently verify
that recovered engine banks assemble byte-for-byte to the locally supplied ROM.
Bank 7 is retained for the shared tempo-command helper and its lookup tables.
`--align-startup` uses the snapshot JSON timestamps, I/O state, and local capture
harness to match the song's startup point and timer state on each model.
Supplying both `--startup-trace-cgb` and `--startup-trace-dmg` with source traces
containing `--trace-address ff40` also aligns the initial LCD phase. The native
audio verifier automates these steps. No recorded performance is embedded in
the ROM.

Audio interrupts take priority over the display. A late screen update holds the
previous frame. DMG omits channel text to reduce rendering work; CGB displays
PU1, PU2, WAV, and NOI.

Run the native regression checks with the local inputs above available:

```sh
python3 -m unittest discover -s tests -p 'test_native*.py' -v
```

The display check exercises DMG and CGB map swaps through the complete ring.
The delay checks verify cycle counts and exclude the DMG OAM-corruption address
range. See [native validation](docs/native-validation.md) for the separate
LSDj/native audio comparisons and their remaining failures.

## Download test cases

```sh
python3 tools/download_test_saves.py
```

The downloader requires 7-Zip (`7zz` or `7z`), checks pinned SHA-256 hashes and
records source/license metadata. Downloads go into ignored `tests/corpus/`.
Use `--refresh` to discover the current Defense Mechanism catalog.
