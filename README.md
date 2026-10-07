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
PU1, PU2, WAV, and NOI. CGB waterfall pixels, bend connections, and channel
labels use cyan for PU1, pink for PU2, green for WAV, and yellow for NOI.
DMG retains the monochrome waterfall.

CGB allows three note colors plus the background in each 8×8 tile. If a
tile's history contains all four channels, NOI is hidden in that tile until
the tile scrolls out and is reused; the other channel colors stay exact.
At an identical pixel, priority is PU1, then PU2, WAV, and NOI.

CGB prepares map rows and clears hidden columns with short, interruptible
sequences of 16-byte DMA transfers. Unchanged tempo and note labels skip
VRAM updates. The optimized color renderer uses about 58–62% fewer display
CPU cycles per screen update on the three validation songs.

Add `--connect-pitch-bends` to the build command to connect successive legato
and pitch-bend points with vertical lines in the waterfall. This optional
feature is disabled by default and works on DMG and CGB. It follows the live
bend state of PU1, PU2, and WAV, breaking the line on an observed new note or
a sampled inactive channel; NOI stays as discrete points. Connections span the pitches
observed in rendered frames, including frames separated by a skipped update.
Note events entirely between those samples cannot be reconstructed.
The extra drawing remains interruptible and can reduce the display frame rate
on busy songs. The same flag is available in `tools/verify_native_audio.py`.

Add `--low-range` for one combined lower-cost display mode on DMG and CGB:
hide NOI from the waterfall, show the original lower six octaves (C1–B6)
at two pixels per semitone, and narrow the plot from 80 to 40 pixels.
NOI audio and its CGB readout remain active. This option also works with
`--connect-pitch-bends`; connections are clipped at the new range boundary.
It is disabled by default and is available in the audio verifier too.

The narrow ring uses 126 canvas tiles instead of 216. After two extra
keyboard tiles, this frees **88 tile slots (1,408 VRAM bytes)**. CGB also
eliminates the 216-byte channel metadata and uses fixed channel colors,
skipping palette remapping and attribute updates. On the three validation
songs this mode uses **60–66% fewer CGB display cycles per update** and
**16–20% fewer on DMG**, compared with the optimized full-width view.
These measurements cover seconds 8–20 with bend connections both off and on;
screen updates still yield to audio. See the validation notes for details.

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
