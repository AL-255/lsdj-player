#!/usr/bin/env python3
"""Build the local-only browser startup emulator from pinned, open SameBoy code.

Requires Emscripten and RGBDS. The generated module embeds only SameBoy's
open-source replacement boot ROMs; no LSDj ROM or save data are build inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REVISION = "c458e7c5d2d350fb37a1931c40da9f758d28d240"
EXPORTS = ["malloc", "free", "lsdj_init", "lsdj_destroy", "lsdj_run_frames",
           "lsdj_set_buttons", "lsdj_set_auto_buttons", "lsdj_read", "lsdj_pc",
           "lsdj_rom_bank", "lsdj_ticks", "lsdj_error", "lsdj_metadata",
           "lsdj_snapshot_wram", "lsdj_snapshot_hram", "lsdj_snapshot_song", "lsdj_snapshot_io"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_revision(source: Path) -> str:
    """Verify a checkout or the corresponding-source archive's hash manifest."""
    if (source / ".git").exists():
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    else:
        manifest_path = source / "SOURCE_MANIFEST.json"
        if not manifest_path.is_file():
            raise SystemExit("SameBoy needs a pinned Git checkout or its exported SOURCE_MANIFEST.json.")
        manifest = json.loads(manifest_path.read_text())
        revision = manifest.get("revision")
        files = manifest.get("files", {})
        required = {"Core/gb.c", "BootROMs/cgb_boot.asm", "BootROMs/dmg_boot.asm", "Makefile", "version.mk", "LICENSE"}
        if not required <= files.keys():
            raise SystemExit("Incomplete exported SameBoy source manifest")
        for name, expected in files.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or (source / name).is_symlink():
                raise SystemExit(f"Unsafe exported SameBoy source path: {name}")
            if not (source / name).is_file() or sha256(source / name) != expected:
                raise SystemExit(f"Exported SameBoy source hash mismatch: {name}")
        compiled = {str(path.relative_to(source)) for path in (source / "Core").rglob("*") if path.is_file()}
        if not compiled <= files.keys():
            raise SystemExit("Unlisted files in exported SameBoy Core")
    if revision != REVISION:
        raise SystemExit(f"SameBoy must be pinned to {REVISION}; found {revision}")
    return revision


def build(source: Path, output: Path, emcc: str) -> dict:
    source, output = source.resolve(), output.resolve()
    if not (source / "Core/gb.c").is_file():
        raise SystemExit("Clone https://github.com/LIJI32/SameBoy.git to reference/SameBoy first.")
    revision = source_revision(source)
    for command in (emcc, "rgbasm", "rgblink", "rgbgfx", "make"):
        if not shutil.which(command):
            raise SystemExit(f"Missing build tool: {command}")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build/web-emulator"
    work.mkdir(parents=True, exist_ok=True)
    # Homebrew's SDK cache is read-only in restricted workspaces. Reuse its
    # installed sysroot in a local cache instead of modifying the toolchain.
    environment = os.environ.copy()
    if "EM_CACHE" not in environment:
        local_cache = work / "emscripten-cache"
        if not local_cache.exists():
            config = Path(shutil.which(emcc)).resolve().parent / "em-config"
            if config.is_file():
                installed_cache = Path(subprocess.check_output([str(config), "CACHE"], text=True).strip())
                if installed_cache.is_dir():
                    shutil.copytree(installed_cache, local_cache)
            local_cache.mkdir(parents=True, exist_ok=True)
        environment["EM_CACHE"] = str(local_cache)
    boots = {model: source / f"build/bin/BootROMs/{model}_boot.bin" for model in ("cgb", "dmg")}
    subprocess.run(["make", "-j4", *(str(path.relative_to(source)) for path in boots.values())],
                   cwd=source, check=True)
    header = []
    for model, path in boots.items():
        values = ",".join(f"0x{value:02x}" for value in path.read_bytes())
        header.append(f"static const unsigned char sameboy_{model}_boot[] = {{{values}}};")
    (work / "sameboy_boot_data.h").write_text("\n".join(header) + "\n")
    flags = ["-O3", "-std=gnu11", "-D_GNU_SOURCE", "-DGB_INTERNAL", "-DGB_DISABLE_TIMEKEEPING",
             f"-ffile-prefix-map={ROOT}=.", f"-ffile-prefix-map={source}=./reference/SameBoy",
             '-DGB_VERSION="1.0.3"', '-DGB_COPYRIGHT_YEAR="2026"', f'-DSAMEBOY_REVISION="{revision}"',
             "-I", str(source), "-I", str(source / "Core"), "-I", str(work),
             "-Wno-multichar", "-Wno-deprecated-declarations",
             "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sEXPORT_NAME=createLsdjEmulator",
             "-sENVIRONMENT=web,worker,node", "-sALLOW_MEMORY_GROWTH=1",
             "-sINITIAL_MEMORY=33554432", "-sSTACK_SIZE=1048576", "-sNO_EXIT_RUNTIME=1",
             "-sEXPORTED_FUNCTIONS=" + json.dumps(["_" + name for name in EXPORTS]),
             '-sEXPORTED_RUNTIME_METHODS=["UTF8ToString","HEAPU8"]']
    subprocess.run([emcc, *flags, str(ROOT / "web/emulator.c"),
                    *(str(path) for path in sorted((source / "Core").glob("*.c"))),
                    "-lm", "-o", str(output)], cwd=ROOT, env=environment, check=True)
    license_path = output.parent / "SameBoy.LICENSE.txt"
    shutil.copyfile(source / "LICENSE", license_path)
    metadata = {"sameboy_revision": revision, "source_url": "https://github.com/LIJI32/SameBoy",
                "module": output.name, "wasm": output.with_suffix(".wasm").name,
                "module_sha256": sha256(output), "wasm_sha256": sha256(output.with_suffix(".wasm")),
                "compiler": subprocess.check_output([emcc, "--version"], text=True).splitlines()[0],
                "boot_roms": {model: {"sha256": sha256(path), "bytes": path.stat().st_size}
                              for model, path in boots.items()},
                "license": license_path.name, "contains_lsdj_rom_or_song_data": False,
                "exports": EXPORTS, "host_source_sha256": sha256(ROOT / "web/emulator.c"),
                "build_source_sha256": sha256(Path(__file__).resolve())}
    output.with_suffix(".build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "reference/SameBoy")
    parser.add_argument("--output", type=Path, default=ROOT / "web/generated/emulator.mjs")
    parser.add_argument("--emcc", default=os.environ.get("EMCC", "emcc"))
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.emcc), indent=2))


if __name__ == "__main__":
    main()
