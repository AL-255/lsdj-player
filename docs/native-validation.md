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
