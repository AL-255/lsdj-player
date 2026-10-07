#!/usr/bin/env python3
"""Package the browser exporter's corresponding open source deterministically.

The allowlist contains project source and the pinned SameBoy core/boot sources.
ROMs, saves, recordings, private snapshots, Git metadata and build outputs are
never scanned or added to the archive.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "c458e7c5d2d350fb37a1931c40da9f758d28d240"
ARCHIVE_ROOT = "lsdj-player-source"
SCREENSHOT_VARIANTS = ("full-points", "full-bends", "low-points", "low-bends")
SAMEBOY_ROOTS = ("Core", "BootROMs", "LICENSE", "Makefile", "version.mk")
TOOL_ROOTS = ("build_web_emulator.py", "build_web_templates.py", "build_native_player.py",
              "package_web_source.py", "check_web_assets.py", "save_format.py")
FORBIDDEN_SUFFIXES = {".gb", ".gbc", ".sav", ".lsdsng", ".lsdprj", ".kit", ".bin",
                      ".wav", ".ppm", ".mp4", ".wasm", ".o", ".a", ".dylib", ".zip", ".7z"}
README = '''# LSDj Player browser exporter: corresponding source

This archive contains the exact project source for the public browser exporter,
its native display/bootstrap patches, and the pinned SameBoy core and replacement
boot-ROM source used by its WebAssembly startup capture. It contains no LSDj ROM,
user save, song, sample kit, recovered interpreter, or private startup snapshot.

Project code is GPL-2.0-or-later (LICENSE). SameBoy is Expat-licensed
(reference/SameBoy/LICENSE). The published browser credits are web/LICENSES.txt.
SOURCE_MANIFEST.json records all source-file SHA-256 hashes. SameBoy's exported
SOURCE_MANIFEST.json records its pinned upstream revision and source hashes;
no Git checkout is required to rebuild this archive.

web/player-cgb-*.png are the website's four native-resolution CGB emulator
screenshots, one per display option combination. Their PNG hashes and shared
capture provenance are recorded in web/player-cgb.json.

## Build the public website

Install Python 3.10 or later, GNU Make, a host C compiler, RGBDS (including
rgbasm, rgblink, rgbfix and rgbgfx), and Emscripten. The published build used
Emscripten 6.0.5 and SameBoy 1.0.3 at revision
c458e7c5d2d350fb37a1931c40da9f758d28d240.

From this directory, run:

    python3 tools/build_web_templates.py
    python3 tools/build_web_emulator.py
    python3 tools/package_web_source.py
    python3 tools/check_web_assets.py
    python3 -m http.server 8000 --directory web

Open http://localhost:8000/. The public patch templates are compiled exclusively
from zero-filled synthetic inputs. The WebAssembly module embeds SameBoy's free
replacement boot ROMs compiled from the included assembly. Neither build command
requires LSDj or a song. A user supplies those inputs locally in their browser
when exporting a player; no upload service is used.

The native engine remains experimental: generated-player audio has not yet
passed strict bit-exact comparison against LSDj. This source archive makes no
additional claim about playback equivalence.
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts and ".git" not in path.parts
            and "__pycache__" not in path.parts and path.suffix.lower() not in FORBIDDEN_SUFFIXES)


def python_dependencies(root: Path) -> set[str]:
    """Follow every local Python import transitively, including imports in functions."""
    pending = list(TOOL_ROOTS)
    result = set()
    while pending:
        filename = pending.pop()
        relative = "tools/" + filename
        if relative in result:
            continue
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Missing builder dependency: {relative}")
        result.add(relative)
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                local = name.split(".")[0] + ".py"
                if (root / "tools" / local).is_file():
                    pending.append(local)
    return result


def project_files(root: Path) -> dict[str, bytes]:
    names = {"LICENSE", "THIRD_PARTY.md", "src/exact_ui.asm", "src/waterfall.asm",
             "web/player-cgb.json"}
    names.update(f"web/player-cgb-{variant}.png" for variant in SCREENSHOT_VARIANTS)
    names |= python_dependencies(root)
    names.update(str(path.relative_to(root)) for path in (root / "src/native").glob("*.asm"))
    names.update(str(path.relative_to(root)) for path in (root / "web").iterdir()
                 if path.is_file() and path.suffix in {".js", ".mjs", ".css", ".html", ".c", ".txt", ".json"})
    names.update(str(path.relative_to(root)) for path in (root / "tests").glob("test_web*.mjs"))
    for optional in ("web/.nojekyll", "docs/web-release.md", "docs/native-validation.md", ".github/workflows/pages.yml"):
        if (root / optional).is_file():
            names.add(optional)
    result = {}
    for name in sorted(names):
        path = root / name
        if not safe_name(name) or path.is_symlink() or not path.is_file():
            raise ValueError(f"Unsafe or missing source entry: {name}")
        result[name] = path.read_bytes()
    return result


def sameboy_files(root: Path) -> dict[str, bytes]:
    source = root / "reference/SameBoy"
    result = {}
    if (source / ".git").exists():
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if revision != REVISION:
            raise ValueError(f"Expected pinned SameBoy revision {REVISION}; found {revision}")
        records = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", "--full-tree",
                                           REVISION, "--", *SAMEBOY_ROOTS]).split(b"\0")
        for record in filter(None, records):
            attributes, raw_name = record.split(b"\t", 1)
            mode, kind, expected = attributes.decode().split()
            name = raw_name.decode()
            if kind != "blob" or mode not in ("100644", "100755") or not safe_name(name):
                raise ValueError(f"Unsafe upstream entry: {name}")
            path = source / name
            if path.is_symlink():
                raise ValueError(f"Upstream source is a symlink: {name}")
            data = path.read_bytes()
            actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if actual != expected:
                raise ValueError(f"Upstream source differs from pinned commit: {name}")
            result[name] = data
    else:
        manifest = json.loads((source / "SOURCE_MANIFEST.json").read_text())
        if manifest.get("revision") != REVISION:
            raise ValueError("Exported SameBoy source has the wrong revision")
        for name, expected in manifest["files"].items():
            if not safe_name(name) or (source / name).is_symlink():
                raise ValueError(f"Unsafe exported upstream entry: {name}")
            data = (source / name).read_bytes()
            if digest(data) != expected:
                raise ValueError(f"Exported SameBoy source hash mismatch: {name}")
            result[name] = data
    required = {"Core/gb.c", "BootROMs/cgb_boot.asm", "BootROMs/dmg_boot.asm", "Makefile", "version.mk", "LICENSE"}
    if not required <= result.keys():
        raise ValueError("Incomplete upstream build sources")
    manifest = {"revision": REVISION, "files": {name: digest(data) for name, data in sorted(result.items())}}
    result["SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return {"reference/SameBoy/" + name: data for name, data in result.items()}


def package(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    project = project_files(root)
    upstream = sameboy_files(root)
    entries = {**project, **upstream, "README.md": README.encode()}
    manifest = {"format": 1, "sameboy_revision": REVISION,
                "files": {name: digest(data) for name, data in sorted(entries.items())}}
    entries["SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for name, data in sorted(entries.items()):
                    if not safe_name(name):
                        raise ValueError(f"Forbidden source archive path: {name}")
                    member = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
                    member.size, member.mode, member.mtime = len(data), 0o644, 0
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    archive.addfile(member, io.BytesIO(data))
    metadata = {"format": 1, "archive": output.name, "sha256": digest(output.read_bytes()),
                "bytes": output.stat().st_size, "file_count": len(entries),
                "source_bytes": sum(map(len, entries.values())), "sameboy_revision": REVISION,
                "contains_lsdj_rom_or_song_data": False,
                "projectSourceSha256": {name: digest(data) for name, data in sorted(project.items())},
                "archiveSourceSha256": {name: digest(data) for name, data in sorted(entries.items())}}
    output.with_name("player-source.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "web/generated/player-source.tar.gz")
    args = parser.parse_args()
    result = package(args.root, args.output)
    print(json.dumps({key: value for key, value in result.items() if not key.endswith("Sha256")}, indent=2))


if __name__ == "__main__":
    main()
