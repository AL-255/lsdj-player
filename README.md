# LSDj 9.4.2 native standalone player

This repository contains the restored 128 KiB native interpreter prototype from
the checkpoint requested during development. Its shared Game Boy assembly
engine interprets native song data; ROM size does not grow with playback duration.
The same image detects DMG/CGB hardware at startup and selects separate
rendering loops, without repeated hardware checks in frame or pixel drawing.

**Experimental:** the piano-roll map preparation and DMG sprite corruption
have regression fixes, but the native interpreter has not passed bit-exact
audio A/B verification against LSDj. The earlier recorded-replay results do
not establish audio equivalence for this native interpreter.

## Browser exporter

Open **[LSDj Player](https://al-255.github.io/lsdj-player/)** to generate a player
without installing build tools. Choose your LSDj 9.4.2 ROM and `.sav`, select
working memory or a saved project, choose the display options, and download
the `.gb` file. Custom kits already installed in your ROM are retained.

Conversion runs locally in a browser worker; your ROM and save never upload
to a server. Each export is checked on both DMG and CGB before download.
The site includes downloadable source and licenses; it does not distribute
LSDj ROMs, sample kits, songs, or captured startup data. See
[web release details](docs/web-release.md) for local previews and rebuilding.

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
`src/native/scroll.asm` maintains the hardware-scrolled background and fixed
window panel. The builder reuses the font initializer in `src/exact_ui.asm`;
the previous recorded playback implementation is excluded from this repository. `tools/recover_native_engine.py` can independently verify
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

The waterfall occupies the left 80 pixels; the information panel stays fixed
in the right-side window. SCX scrolls the background by one pixel per committed
update. Every eight pixels, only the entering column's 18 tile IDs change,
instead of copying 198 map cells. The 32-column map and twelve physical tile
columns wrap independently. CGB additionally updates palette attributes when
needed and clears recycled tile data with short DMA blocks. Unchanged tempo
and note labels skip VRAM updates.

On the three validation songs, display CPU cycles per update fall about
34–40% on DMG, 41–51% on full-range CGB, and 4–7% on low-range CGB compared
with the previous 80-pixel renderer. Both bend settings are included; these
measurements cover seconds 8–20 after reset, not worst-case loads.

Add `--connect-pitch-bends` to the build command to connect successive legato
and pitch-bend points with vertical lines in the waterfall. This optional
feature is disabled by default and works on DMG and CGB. It follows the live
bend state of PU1, PU2, and WAV, breaking the line on an observed new note or
a sampled inactive channel; NOI stays as discrete points. Connections span the pitches
observed in rendered frames, including frames separated by a skipped update.
Note events entirely between those samples cannot be reconstructed.
The extra drawing remains interruptible and can reduce the display frame rate
on busy songs. The same flag is available in `tools/verify_native_audio.py`.

Add `--low-range` to hide NOI from the waterfall and show the original lower
six octaves (C1–B6) at two pixels per semitone. The hardware-scrolled plot keeps
its 80-pixel width on both models. NOI audio and its CGB readout remain active.
This option also works with `--connect-pitch-bends`; connections clip at the new
range boundary. It is disabled by default and available in the audio verifier.

Both pitch ranges use 216 canvas tiles. The enlarged keyboard reuses existing
font tiles through 8×8 sprites, preserving all displayed text and song titles.
CGB's low-range view uses fixed channel colors, eliminating the 216-byte channel
metadata, palette remapping, and attribute updates. See the validation notes
for performance measurements and the remaining audio-equivalence failure.

Run the native regression checks with the local inputs above available:

```sh
python3 -m unittest discover -s tests -p 'test_native*.py' -v
```

The display checks exercise both hardware-scroll rings and the stationary
window on DMG and CGB, and confirm that each model uses its own rendering code.
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
