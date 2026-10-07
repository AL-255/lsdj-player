#!/usr/bin/env python3
"""Build the pinned official SameBoy core and deterministic CGB/DMG capture tool."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REVISION = "c458e7c5d2d350fb37a1931c40da9f758d28d240"


def observation_sources(source: Path, directory: Path) -> dict[str, Path]:
    """Copy pinned source with read-only capture calls; preserve core equations.

    The upstream checkout is never edited. The hooks observe CPU advance
    boundaries and the effective run size when pulse-1 sweep overflows.
    """
    directory.mkdir(parents=True, exist_ok=True)
    declaration = "\nvoid GB_capture_timing_event(GB_gameboy_t *, const char *, unsigned, unsigned);\n"

    def replace_once(text: str, old: str, new: str) -> str:
        if text.count(old) != 1:
            raise SystemExit("Pinned SameBoy observation insertion point changed")
        return text.replace(old, new)

    timing = (source / "Core/timing.c").read_text()
    timing = replace_once(timing, '#include "gb.h"', '#include "gb.h"' + declaration)
    timing = replace_once(timing, "void GB_advance_cycles(GB_gameboy_t *gb, uint8_t cycles)\n{",
                          "void GB_advance_cycles(GB_gameboy_t *gb, uint8_t cycles)\n{\n"
                          '    GB_capture_timing_event(gb, "advance_begin", cycles, 0);')
    timing = replace_once(timing, "    GB_apu_run(gb, false);\n    GB_display_run",
                          '    GB_capture_timing_event(gb, "advance_apu_begin", cycles, 0);\n'
                          "    GB_apu_run(gb, false);\n    GB_display_run")
    timing = replace_once(timing, "    rtc_run(gb, cycles);\n}",
                          "    rtc_run(gb, cycles);\n"
                          '    GB_capture_timing_event(gb, "advance_end", cycles, 0);\n}')

    apu = (source / "Core/apu.c").read_text()
    apu = replace_once(apu, '#include "gb.h"', '#include "gb.h"' + declaration)
    old = ("        gb->apu.is_active[GB_SQUARE_1] = false;\n"
           "        update_sample(gb, GB_SQUARE_1, 0, gb->apu.square_sweep_calculate_countdown * 2 - cycles);")
    apu = replace_once(apu, old,
                       '        GB_capture_timing_event(gb, "sweep_overflow", cycles, '
                       "gb->apu.square_sweep_calculate_countdown * 2 - cycles);\n" + old)
    paths = {"timing.c": directory / "timing.c", "apu.c": directory / "apu.c"}
    paths["timing.c"].write_text(timing)
    paths["apu.c"].write_text(apu)
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT / "reference/SameBoy")
    p.add_argument("--output", type=Path, default=ROOT / "build/sameboy-capture")
    p.add_argument("--cc", default=os.environ.get("CC", "clang"))
    p.add_argument("--observe-sweeps", action="store_true",
                   help="Enable read-only sweep-overflow/CPU-batch capture; leave upstream source unchanged")
    args = p.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if not (source / "Core/gb.c").exists():
        raise SystemExit("Clone https://github.com/LIJI32/SameBoy.git to reference/SameBoy first.")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit(f"SameBoy source must be pinned to {REVISION}; found {revision}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build the official replacement CGB/DMG boot ROMs with RGBDS. Do not disable the
    # debugger: its absolute 8 MHz counter timestamps writes inside instructions.
    boot = source / "build/bin/BootROMs/cgb_boot.bin"
    dmg_boot = source / "build/bin/BootROMs/dmg_boot.bin"
    subprocess.run(["make", "-j4", str(boot.relative_to(source)), str(dmg_boot.relative_to(source))], cwd=source, check=True)
    sources = sorted((source / "Core").glob("*.c"))
    observed = observation_sources(source, output.parent / (output.name + ".observer")) if args.observe_sweeps else {}
    sources = [observed.get(s.name, s) for s in sources]
    flags = ["-O3", "-std=gnu11", "-D_GNU_SOURCE", "-DGB_INTERNAL", "-DGB_DISABLE_TIMEKEEPING",
             '-DGB_VERSION="1.0.3"', '-DGB_COPYRIGHT_YEAR="2026"',
             f'-DSAMEBOY_REVISION="{revision}"', f'-DSAMEBOY_BOOT_ROM="{boot}"',
             f'-DSAMEBOY_DMG_BOOT_ROM="{dmg_boot}"',
             "-I", str(source), "-I", str(source / "Core"), "-Wno-multichar", "-Wno-deprecated-declarations"]
    if args.observe_sweeps:
        flags += ["-DSAMEBOY_OBSERVE_SWEEP"]
    # Preserve normal floating-point semantics for reproducible PCM.
    subprocess.run([args.cc, *flags, str(ROOT / "host/sameboy_capture.c"),
                    *(str(s) for s in sources), "-lm", "-o", str(output)], check=True)
    metadata = {"source": str(source), "revision": revision, "version": "1.0.3",
                "boot_rom": str(boot), "executable": str(output), "compiler": args.cc,
                "boot_rom_sha256": hashlib.sha256(boot.read_bytes()).hexdigest(),
                "dmg_boot_rom": str(dmg_boot), "dmg_boot_rom_sha256": hashlib.sha256(dmg_boot.read_bytes()).hexdigest(),
                "flags": flags, "model": "CGB-E", "timebase_hz": 8388608,
                "supported_models": ["CGB-E", "DMG-B"],
                "host_source_sha256": hashlib.sha256((ROOT / "host/sameboy_capture.c").read_bytes()).hexdigest(),
                "capture_features": ["audio_wav", "apu_writes", "apu_reads", "fixed_clock_pitch", "dmg_model", "completed_frame_screen"] +
                                    (["sweep_cpu_advances"] if args.observe_sweeps else []),
                "sweep_observation": args.observe_sweeps,
                "observation_sources": {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                                        for name, path in observed.items()}}
    output.with_suffix(".build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
