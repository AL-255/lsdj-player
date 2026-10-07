# References and local inputs

The reference repositories are pinned Git submodules:

- [lsdpack](https://github.com/jkotlinski/lsdpack), `463036a7307d70f82e08818282c17bfd63022d02`, GPL-2.0-or-later: assembly/player reference.
- [SameBoy](https://github.com/LIJI32/SameBoy), `c458e7c5d2d350fb37a1931c40da9f758d28d240`, Expat: official emulator core used by the capture harness.
- [libLSDJ](https://github.com/stijnfrishert/liblsdj), `6023c4e48ad8280abacfddba60f2689e2442d79c`, MIT: save compression/format reference.
- [LSDPatch](https://github.com/jkotlinski/lsdpatch), `f6a2d01b0a1af1edfa897b36f0bdd6dcb57ecf81`, MIT: exported project/sample-kit reference.

Initialize them with `git submodule update --init --recursive`. The capture
harness observes normal emulator audio and hardware behavior. Optional observer
sources are generated locally without editing the pinned SameBoy checkout.

The project source is distributed under GPL-2.0-or-later; see `LICENSE`.
Original notices remain in shared display assembly.

LSDj ROMs are user-provided inputs and are not distributed here. The experimental
native builder recovers interpreter portions locally. Recovered assembly,
startup state, generated ROMs and other build artifacts are ignored by Git.

The test downloader retrieves songs from https://defensemech.com/songs/.
Defense Mechanism states that songs are CC BY-SA 4.0 unless otherwise noted.
Downloads and source/license/hash manifests remain in ignored `tests/corpus/`.
The song license does not explicitly cover standalone kit downloads; the script
records that their license is unspecified. Music rights remain with their authors.
