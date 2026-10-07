# Browser ROM exporter

The static site in `web/` runs entirely on the user's device. It accepts a
user-supplied LSDj 9.4.2 ROM and a 64 or 128 KiB save, lets the user choose a
saved project or working memory, and downloads a standalone player ROM.
Custom sample kits already installed in the supplied ROM are preserved.
No server receives the ROM, save, song data, or generated player.

Both lower-range mode and connected pitch bends are optional. The resulting
ROM includes separate DMG and CGB rendering paths. Audio equivalence remains
experimental: matching startup state is not a bit-exact PCM guarantee.

## Local preview and verification

The checked-in assets are ready to serve; no package installation is needed:

```sh
python3 -m http.server 8000 --directory web
node --test tests/test_web*.mjs
python3 tools/check_web_assets.py
```

Open `http://localhost:8000`. ES modules and WebAssembly workers require HTTP
or HTTPS; opening `index.html` directly from disk is unsupported.

The converter first checks the supplied ROM's playback-engine banks. Header
and kit edits are permitted, but other engine versions are rejected. A local
SameBoy WebAssembly worker starts the selected song independently on DMG-B
and CGB-E, captures the startup state, and injects it into the player. It then
checks and calibrates each model's song-entry tick, timer state, and LCD phase.
Conversion fails visibly if either hardware model cannot be prepared.

The page uses the computer's light/dark preference and standard file inputs.
Its example image is an unmodified 160×144 SameBoy CGB framebuffer of TRIAC,
captured after 20 seconds with the lower-range view and discrete pitch points.
The PNG is displayed at 2× size with nearest-neighbor pixels; its capture
provenance and checksum are in `web/player-cgb.json`.

The release was exercised with TRIAC, WOW, and KASHIWA in all four display
configurations. All 12 exports aligned on both models, and all 24 independent
20-second playback checks matched song-entry time and song APU-write counts.
All 24 still failed strict PCM equality. Sixteen browser-module tests cover
save parsing, corruption rejection, ROM compatibility, patch boundaries, kit
preservation, delay encoding, and calibration. Parser parity covers 102 local
files and 223 selected songs. Local evidence is in `build/web-export/` and
`build/web-emulator/verification/`; no test input is part of the published site.

## Rebuilding public assets

Requires RGBDS, Python 3.10+, Emscripten, make, and the pinned SameBoy checkout:

```sh
git submodule update --init reference/SameBoy
python3 tools/build_web_emulator.py
python3 tools/build_web_templates.py
python3 tools/package_web_source.py
python3 tools/check_web_assets.py
```

The emulator embeds SameBoy's open-source replacement boot ROMs. Template
compilation uses synthetic zero-filled ROM, song, and startup buffers, and
exports only this project's bootstrap, display, font, and renderer patches.
It never reads an LSDj ROM or a real save. Compatibility information consists
only of engine-bank hashes. No tracker ROM, recovered engine code, song,
sample kit, or private startup snapshot is published.

The downloadable source archive includes the code needed to rebuild the
public assets and its license notices. Rebuild it whenever published source
changes. The asset checker rejects stale templates or emulator binaries.

## GitHub Pages

The `Publish ROM exporter` workflow tests the browser modules and verifies
generated assets, then deploys **only `web/`** using GitHub's Pages artifact
service. The repository's Pages source must be set to GitHub Actions.
Pushes to `main` affecting the site, player sources, or build tools redeploy
the site. A workflow-dispatch run can also publish the current version.

GitHub Pages must be available on the repository's account plan. The workflow
does not change repository visibility and does not publish the repository's
history or local test inputs.
