# Native interpreter validation

The native player interprets the saved song with recovered shared LSDj 9.4.2
code. It does not contain a recording of APU writes. Historical verification
of the recorded-replay player is not proof for this implementation.

## Display and DMG fixes

- The native scheduler previously prepared only nine tilemap rows. The shared
  monochrome pixel renderer requires 18 one-row stages. The stage table and
  work budget now use the renderer's exported counts: 12 clears and 18 rows.
- VBlank previously advanced the pixel phase even while rendering was still
  working on that phase. A completion flag now holds scrolling until the frame
  is finished; a tilemap swap additionally requires all preparation stages.
  Display commits run in the foreground during VBlank. If audio uses that
  window, the previous screen is retained and the next display frame is skipped.
- Long startup delays decremented BC through `$FE00–$FEFF` with the LCD on.
  This triggers DMG's hardware OAM corruption bug without an explicit sprite
  memory write. Loop counters now stay at or below `$FDFF`, preserving the
  requested CPU cycle count and registers.
- The original music VBlank vector and timer bookkeeping are preserved, with
  no added rendering in that handler. VRAM access retries allow music
  interrupts between polls; only the final access is briefly protected.
- DMG skips channel-note formatting and VRAM updates entirely. Only CGB shows
  the channel status, labeled PU1, PU2, WAV, and NOI.
- Tempo commands call a shared helper in ROM bank 7. The native build omitted
  that bank, causing WOW to execute empty ROM and eventually wrap its stack.
  Retaining the bank and its tables restores tempo-command playback on both
  models without increasing the minimum 128 KiB ROM size.
- Startup now explicitly disables interrupts and removes stale pending VBlank
  requests before enabling the music interrupt sources. Optional
  `--align-startup` matches each model's captured entry timestamp, TIMA value,
  and pending non-VBlank requests. Source startup traces can additionally align
  the LCD enable phase. Calibration reads only startup state; it does not use
  subsequent sound writes or compile a playback schedule.

Run:

```sh
python3 -m unittest discover -s tests -p 'test_native*.py' -v
```

The integration check requires the locally supplied ROM, the TRIAC startup
snapshots described in the README, RGBDS, and `build/sameboy-native-analysis`.
It runs 1,200 frames separately on CGB-E and DMG-B, checks upper and lower map
rows at every swap across all 12 ring positions, and inspects all 18 × 11
visible tiles and all 160 OAM bytes in actual memory during VBlank. A missed
rendering deadline can hold the scroll for a frame; it must not expose an
unfinished map.

A read-only diagnostic build of the pinned SameBoy core recorded 180 DMG OAM
corruption events before the delay fix and zero afterward over 1,200 frames.
All events came from the startup counter's `DEC BC` instructions. Local
evidence is in `build/native-dmg-fix/`.

## Audio comparison

The audio requirement is strict equality of signed 16-bit stereo PCM at
48 kHz, from reset with zero offsets, separately for CGB-E and DMG-B.
No gain adjustment, resampling, tolerance, or inferred alignment is accepted.
Actual song activity is required; matching boot audio alone cannot pass.

Using the existing prepared-save manifest:

```sh
python3 tools/verify_native_audio.py \
  --match 'triac/*' \
  --match 'wow/WOW_v682-v901:0' \
  --match 'kashiwa/kashiwa_v91C+:0' \
  --seconds 20 \
  --output build/audio-ab/native-verification-final-native
```

For each song, the verifier runs LSDj with its normalized save and real Start
input, collects fresh model-specific startup snapshots, builds the native
player, and captures both players with the same emulator. It preserves
commands, input and ROM hashes, recordings, write traces, screenshots, first
PCM differences, and song-relative write diagnostics. It exits nonzero on
any failure. `--native-rom PATH` checks a prebuilt native ROM against exactly
one selected case.

**Audio equivalence still fails: zero of six comparisons are bit-exact.**
The 20-second runs cover TRIAC, WOW, and KASHIWA separately on CGB-E and DMG-B.
Each compares 960,000 stereo sample frames at 48 kHz from reset, with zero
offsets. All six now match the source entry timestamp, startup timer state,
LCD phase, and total count of song APU writes. WOW keeps playing without the
previous stack failure. Subsequent write timing and some register values still
diverge; the first PCM differences are one least-significant bit, which fails
the strict requirement.

| Save | CGB first different sample | DMG first different sample |
| --- | ---: | ---: |
| TRIAC | 222,893 (4.643604 s) | 207,830 (4.329792 s) |
| WOW | 247,271 (5.151479 s) | 256,220 (5.337917 s) |
| KASHIWA | 179,232 (3.734000 s) | 169,921 (3.540021 s) |

Sample indices are zero-based. Full commands, hashes, settings, startup
alignment passes, and first bus/PCM differences are retained in
`build/audio-ab/native-verification-final-native/report.json`. Startup alignment
does not resolve the remaining interrupt/foreground timing differences and
must not be reported as bit-exact native playback.

The separate legacy `test_waterfall.py` checks the old recorded player's
cycle constants. Its five initialization-cycle failures and DMG width-80
assembly failure are pre-existing: that test uses the unchanged
`src/exact_ui.asm` and `src/waterfall.asm`, not the native files modified here.

## Optional bend connections

`--connect-pitch-bends` enables vertical connections between successive
legato/pitch-bend points for PU1, PU2, and WAV on both models. The default
omits the connector code and state. A feature-disabled TRIAC build is
byte-identical to commit `f3b7349`, with SHA-256
`8eb107b4603c4c254ab0b3047ccd9d9d265b513793e031188190473585d24ee6`.
The local comparison is recorded in `build/bend-feature/default-off-check.json`.

All 20 native regression tests pass. The new SameBoy tests check ascending,
descending, and full-height spans; instant legato; note restarts and inactive
channels; all 12 ring positions and eight pixel phases; and all three tonal
voices on CGB and DMG. NOI remains discrete. Drawing three full-height spans
with the LCD enabled still services the 4,096-cycle audio timer, with no
observed interrupt-service gap exceeding 4,300 CPU cycles. Rendering can
take multiple frames; scrolling waits for completion while music continues.

The same six 20-second audio comparisons were repeated with
`--connect-pitch-bends --output build/audio-ab/native-bend-verification`.
All six match startup timing and total song APU-write counts, and none
reports the previous stack or memory warnings. Strict PCM equality still
fails in all six. First differing sample indices remain as listed above
except WOW on DMG, which first differs at 256,225. The enabled renderer can
affect the remaining interrupt timing differences; it is not evidence of
bit-exact playback. Full results and source/ROM hashes are in
`build/audio-ab/native-bend-verification/report.json`.

## CGB channel colors

CGB now assigns cyan to PU1, pink to PU2, green to WAV, and yellow to NOI.
Channel labels and optional bend connections use the same colors. DMG keeps
the monochrome pixel renderer and omits channel labels.

A CGB background tile can hold three note colors plus its background.
Four palettes cover the possible three-channel subsets. When a tile's
palette changes, its existing pixel indices are translated to preserve
channel identity. If all four channels enter one tile, NOI is removed until
that tile is recycled, as requested. Exact pixel overlaps also prioritize
PU1, PU2, WAV, then NOI. Both scrolling maps receive matching attributes,
and reused columns clear their channel metadata.

All 26 native tests pass. Six new SameBoy tests inspect actual CGB pixels,
all channel insertion and overlap orders, palette changes on both maps,
column reuse, matching labels, and colored bend spans. A live-LCD stress
test confirms audio interrupts continue during three full-height colored
spans, with no service gap exceeding 4,300 CPU cycles for a 4,096-cycle
timer. Every observed interrupt sees VRAM bank 0 restored. The existing
1,200-frame tests still cover all ring positions, both screen halves,
OAM preservation, and the unchanged monochrome bitplanes on DMG.

Fresh 20-second comparisons cover TRIAC, WOW, and KASHIWA on both models,
with bend connections disabled and enabled. All twelve match startup
timing and total song APU-write counts, but none passes strict PCM equality.
The color renderer changes foreground/interrupt timing and does not resolve
the existing audio-equivalence failure. Reports with source and ROM hashes:

- `build/audio-ab/native-color-verification/report.json`
- `build/audio-ab/native-color-bend-verification/report.json`

## CGB rendering cost

Profiling commit `5269e6b` showed that CGB colors used 2.2–2.5 times the
display CPU cycles per committed frame of the monochrome renderer. Most
extra work was map preparation: each cell read its tile number from VRAM,
switched banks to read channel metadata, and switched again to write the
palette attribute.

The optimized renderer reads tile numbers from the existing ROM map table,
prepares one attribute row in WRAM, and copies each map row with two
16-byte CGB DMA transfers. Hidden-column clearing uses the same bounded
transfers. Each block restores VRAM bank 0 and releases interrupts before
the next block. The shared music engine does not write DMA registers.
Pixel drawing skips unchanged metadata and empty-tile remapping, compares
palette codes directly for overlap priority, and accesses each bitplane
pair with one VRAM availability check. Tempo and channel text only redraw
when their displayed values change.

The comparison uses CGB-E at double speed, measuring seconds 8–20 after
reset for TRIAC, WOW, and KASHIWA, with bends both disabled and enabled.
A read-only SameBoy instruction callback measures elapsed CPU ticks,
including VRAM waits and DMA stalls; completed WX commits count screen
updates. IRQ entry cycles are attributed to the preceding instruction.
Across all six runs, display cycles per committed frame fall **57.9–61.7%**.
The resulting screen throughput is approximately the former monochrome
renderer's throughput. Results with bends enabled:

| Song | Display cycles/update before → after | Screen updates/s before → after | Idle CPU after |
| --- | ---: | ---: | ---: |
| TRIAC | 84,751 → 35,530 | 48.50 → 55.67 | 50.7% |
| WOW | 82,029 → 34,513 | 40.67 → 53.42 | 45.2% |
| KASHIWA | 77,601 → 31,848 | 57.25 → 58.42 | 60.7% |

Local evidence is retained in `build/color-performance/final.json`, with
ROM/source hashes, commands, traces, and the read-only profiling harness
alongside it. This measures these song intervals, not worst-case hardware
performance or an audio-equivalence guarantee.

All 31 native tests pass. New DMA checks inspect actual VRAM and displayed
colors for all twelve ring positions, both maps, every row, and every clear
stage. With a deliberately heavy audio interrupt, 1,872 IRQs continue
during map preparation; the maximum measured gap is 4,252 CPU cycles for
the 4,096-cycle timer, and every IRQ sees VRAM bank 0. Status tests check
correct text and zero VRAM writes for repeated values on both hardware
models. Color identity, crowded-tile behavior, and the DMG display checks
continue to pass.

Both six-case 20-second audio matrices were repeated after optimization.
Startup times and total song APU-write counts match, but all twelve strict
PCM comparisons still fail the existing audio-equivalence requirement.
Full results are in:

- `build/audio-ab/native-color-optimized-verification/report.json`
- `build/audio-ab/native-color-optimized-bend-verification/report.json`

## Optional narrow lower pitch range

`--low-range` combines the requested three display changes: omit NOI pixels,
discard original pitch rows 0–71, and double rows 72–143 to fill the screen.
The visible range is C1–B6, with B6 at the top. The plot narrows from 80 to
40 pixels to reduce its tile allocation; vertical doubling alone would not
free tiles. Original pitch samples remain available to the bend detector
and CGB readouts, and all four audio channels continue playing. Optional
bend connections clip at the upper boundary before their rows are doubled.

The ring shrinks from twelve to seven columns (216 to 126 canvas tiles).
Two additional tiles provide the enlarged keyboard, leaving **88 free tile
slots / 1,408 bytes at bank-0 $8a80–$8fff**. CGB needs only PU1, PU2, and
WAV in the plot, so one fixed palette replaces channel metadata, palette
remapping, and attribute transfers. This also frees the former 216-byte
bank-1 metadata region. Each map row now needs one short CGB DMA transfer.

All 36 native tests pass. Five new tests exercise actual DMG and CGB pixels,
boundary pitches, both directions of clipped bends, every tonal insertion
order, all seven ring columns, and 1,200-frame scrolling. They inspect all
18 map rows, the enlarged keyboard sprites, and canaries in every freed
tile and the unused CGB metadata region. Noise audio trigger writes continue.
Live-LCD full-height joins allow the 4,096-cycle audio timer to run with no
observed interrupt-service gap over 4,300 CPU cycles. With the flag disabled,
a rebuilt TRIAC bend-enabled ROM is byte-identical to commit `34d91bf`.

The same read-only profiling method and seconds 8–20 described above compare
this mode to `34d91bf`, with bends both off and on for all three songs:

| Hardware | Reduction in display CPU cycles/update | Screen updates/s, bends enabled | Idle CPU, bends enabled |
| --- | ---: | ---: | ---: |
| CGB-E | 59.7–66.2% | 53.4–58.4 | 58.2–74.0% |
| DMG-B | 16.4–19.6% | 26.3–53.6 | 24.5–40.3% |

These are measured song intervals, not worst-case guarantees. DMG still
spends most display time waiting for safe VRAM access. Evidence, commands,
source/ROM hashes, and traces remain in `build/color-performance/`, with
the summary at `low-range-comparison.json`. Locally generated test ROMs
and screenshots are in `build/low-range/`.

Fresh 20-second LSDj comparisons cover TRIAC, WOW, and KASHIWA on both
models, with bends off and on. All twelve match startup timing and total
song APU-write counts, and none reports the previous stack/memory warnings.
**All twelve still fail strict PCM equality.** The display improvement
does not resolve the existing native audio-equivalence issue. Reports:

- `build/audio-ab/native-low-range-verification/report.json`
- `build/audio-ab/native-low-range-bend-verification/report.json`
