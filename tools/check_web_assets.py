#!/usr/bin/env python3
"""Verify public assets and corresponding source without private inputs/submodules."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import unquote, urlsplit

from package_web_source import ARCHIVE_ROOT, REVISION, SCREENSHOT_VARIANTS, project_files, safe_name

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {
    "index.html", "app.js", "converter.js", "worker.js", "player-core.js", "save-format.js",
    "style.css", "emulator.c", "compatibility.json", "LICENSES.txt", "VALIDATION.txt",
    "player-cgb.json",
    "generated/templates.json", "generated/emulator.mjs", "generated/emulator.wasm",
    "generated/emulator.build.json", "generated/SameBoy.LICENSE.txt",
    "generated/player-source.tar.gz", "generated/player-source.json",
} | {f"player-cgb-{variant}.png" for variant in SCREENSHOT_VARIANTS}
PATCH_RANGES = ((0x42, 0x45), (0x100, 0x104), (0xb54, 0x1306), (0x14000, 0x18000))
HEX_HASH = re.compile(r"[0-9a-f]{64}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def json_file(path):
    return json.loads(path.read_text())


def source_hashes(root, hashes, label):
    require(isinstance(hashes, dict) and hashes, f"Missing {label} source hashes")
    for name, expected in hashes.items():
        require(safe_name(name), f"Unsafe {label} source path: {name}")
        require(isinstance(expected, str) and HEX_HASH.fullmatch(expected), f"Invalid hash: {name}")
        path = root / name
        require(path.is_file() and not path.is_symlink(), f"Missing {label} source: {name}")
        require(sha256(path) == expected, f"Stale {label}: {name}; rebuild public assets")


def check_templates(root, web):
    data = json_file(web / "generated/templates.json")
    require(data.get("format") == 1 and data.get("slotSize") == 192, "Unsupported template format")
    source_hashes(root, data.get("sourceSha256"), "templates")
    require(set(data.get("templates", {})) == {"full-points", "full-bends", "low-points", "low-bends"},
            "Missing display template variants")
    for name, template in data["templates"].items():
        ranges = []
        for patch in template["patches"]:
            raw = base64.b64decode(patch["data"], validate=True)
            ranges.append((patch["offset"], patch["offset"] + len(raw)))
        require(tuple(ranges) == PATCH_RANGES, f"Unexpected or private engine patch range: {name}")
        def covered(offset, length=1):
            return any(start <= offset and offset + length <= end for start, end in ranges)
        require(set(template["delays"]) == {"SongCGB", "SongDMG", "LCDCGB", "LCDDMG"},
                f"Missing delay slot: {name}")
        for delay in template["delays"].values():
            require(delay["length"] == 192 and covered(delay["offset"], delay["length"]),
                    f"Invalid delay slot: {name}")
        require(set(template["timers"]) == {"dmg", "cgb"}, f"Missing hardware timer model: {name}")
        for timers in template["timers"].values():
            require(set(timers) == {"tima", "tma", "tac", "if"} and all(covered(x) for x in timers.values()),
                    f"Invalid timer patch: {name}")
        require(covered(template["titleOffset"], 8 * 16), f"Invalid title patch: {name}")
    return len(data["templates"])


def check_emulator(root, web):
    generated = web / "generated"
    data = json_file(generated / "emulator.build.json")
    require(data.get("sameboy_revision") == REVISION, "Unexpected SameBoy revision")
    require(data.get("contains_lsdj_rom_or_song_data") is False, "Emulator provenance flag missing")
    for kind, filename in (("module", "emulator.mjs"), ("wasm", "emulator.wasm")):
        require(data.get(kind) == filename, f"Unexpected emulator {kind} name")
        require(sha256(generated / filename) == data.get(kind + "_sha256"), f"Emulator {kind} hash mismatch")
    source_hashes(root, {"web/emulator.c": data.get("host_source_sha256"),
                         "tools/build_web_emulator.py": data.get("build_source_sha256")}, "emulator")
    require(data.get("license") == "SameBoy.LICENSE.txt", "Missing SameBoy license metadata")
    for model, size in (("dmg", 256), ("cgb", 2304)):
        boot = data.get("boot_roms", {}).get(model, {})
        require(boot.get("bytes") == size and isinstance(boot.get("sha256"), str)
                and HEX_HASH.fullmatch(boot["sha256"]), f"Invalid free boot ROM metadata: {model}")
    required_exports = {"lsdj_init", "lsdj_run_frames", "lsdj_metadata", "lsdj_snapshot_wram",
                        "lsdj_snapshot_hram", "lsdj_snapshot_song", "lsdj_snapshot_io"}
    require(required_exports <= set(data.get("exports", [])), "Missing emulator conversion API")
    return data


def check_screenshot(web):
    data = json_file(web / "player-cgb.json")
    require(data.get("format") == 2, "Invalid player screenshot metadata")
    require(data.get("model") == "cgb" and data.get("source_type") == "emulator-framebuffer",
            "Player screenshot must identify its CGB emulator capture")
    require(data.get("sameboy_revision") == REVISION, "Unexpected screenshot emulator revision")
    require(isinstance(data.get("song"), str) and data["song"].strip(), "Missing screenshot song title")
    require(type(data.get("requested_ticks_8mhz")) is int and data["requested_ticks_8mhz"] > 0,
            "Missing screenshot capture time")
    variants = data.get("variants")
    require(isinstance(variants, dict) and set(variants) == set(SCREENSHOT_VARIANTS),
            "Player screenshots must cover all four display option combinations")
    for name, variant in variants.items():
        require(isinstance(variant, dict), f"Invalid player screenshot metadata: {name}")
        filename = f"player-cgb-{name}.png"
        require(variant.get("image") == filename, f"Invalid player screenshot image: {name}")
        require(variant.get("low_range") is name.startswith("low-")
                and variant.get("connect_pitch_bends") is name.endswith("-bends"),
                f"Player screenshot options do not match its variant: {name}")
        require((variant.get("width"), variant.get("height")) == (160, 144),
                f"Player screenshot must retain the native 160×144 framebuffer: {name}")
        path = web / filename
        header = path.read_bytes()[:33]
        require(len(header) == 33 and header[:8] == b"\x89PNG\r\n\x1a\n"
                and header[8:16] == b"\x00\x00\x00\x0dIHDR", f"Invalid player screenshot PNG: {name}")
        dimensions = (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big"))
        require(dimensions == (160, 144), f"Player screenshot has been resized or cropped: {name}")
        require(sha256(path) == variant.get("sha256"),
                f"Player screenshot differs from its capture metadata: {name}")
    require(len({variant["sha256"] for variant in variants.values()}) == len(SCREENSHOT_VARIANTS),
            "Player screenshot variants must show distinct captures for their display options")


def check_archive(root, web):
    generated = web / "generated"
    data = json_file(generated / "player-source.json")
    path = generated / "player-source.tar.gz"
    require(data.get("format") == 1 and data.get("archive") == path.name, "Invalid source archive metadata")
    require(data.get("sameboy_revision") == REVISION and data.get("contains_lsdj_rom_or_song_data") is False,
            "Source archive provenance mismatch")
    require(path.stat().st_size == data.get("bytes") and sha256(path) == data.get("sha256"),
            "Source archive file hash mismatch")
    source_hashes(root, data.get("projectSourceSha256"), "source archive")
    current = {name: hashlib.sha256(content).hexdigest() for name, content in project_files(root).items()}
    require(current == data["projectSourceSha256"], "Source archive omits current public build/source files")
    expected = data.get("archiveSourceSha256", {})
    observed, retained, source_bytes = {}, {}, 0
    retain = {"SOURCE_MANIFEST.json", "reference/SameBoy/SOURCE_MANIFEST.json", "LICENSE",
              "reference/SameBoy/LICENSE", "web/LICENSES.txt", "README.md"}
    prefix = ARCHIVE_ROOT + "/"
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            require(member.name.startswith(prefix), f"Invalid archive root: {member.name}")
            name = member.name[len(prefix):]
            require(safe_name(name) and member.isfile() and name not in observed,
                    f"Unsafe or duplicate source member: {name}")
            require(member.mtime == 0 and member.uid == 0 and member.gid == 0 and member.mode == 0o644,
                    f"Nondeterministic source metadata: {name}")
            require(member.size <= 4 * 1024 * 1024, f"Unexpectedly large source member: {name}")
            contents = archive.extractfile(member).read()
            observed[name] = hashlib.sha256(contents).hexdigest()
            source_bytes += len(contents)
            if name in retain:
                retained[name] = contents
    require(observed == expected, "Source archive contents differ from manifest")
    require(len(observed) == data.get("file_count") and source_bytes == data.get("source_bytes"),
            "Source archive counts differ")
    require(retain <= retained.keys(), "Missing corresponding-source manifests/licenses/instructions")
    manifest = json.loads(retained["SOURCE_MANIFEST.json"])
    require(manifest.get("sameboy_revision") == REVISION and manifest.get("format") == 1,
            "Invalid internal source manifest")
    require(manifest.get("files") == {name: digest for name, digest in observed.items() if name != "SOURCE_MANIFEST.json"},
            "Internal source manifest mismatch")
    upstream = json.loads(retained["reference/SameBoy/SOURCE_MANIFEST.json"])
    require(upstream.get("revision") == REVISION, "Internal SameBoy revision mismatch")
    require(upstream.get("files") == {name.removeprefix("reference/SameBoy/"): digest
            for name, digest in observed.items() if name.startswith("reference/SameBoy/")
            and name != "reference/SameBoy/SOURCE_MANIFEST.json"}, "Internal SameBoy source manifest mismatch")
    require(retained["LICENSE"] == (root / "LICENSE").read_bytes(), "Project license differs in source archive")
    require(retained["reference/SameBoy/LICENSE"] == (generated / "SameBoy.LICENSE.txt").read_bytes(),
            "Published SameBoy license differs from corresponding source")
    notices = (web / "LICENSES.txt").read_bytes()
    require(retained["LICENSE"] in notices and retained["reference/SameBoy/LICENSE"] in notices,
            "Public license notices are incomplete")
    return data


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(value for key, value in attrs if key in ("src", "href") and value)


def check_static_links(web):
    parser = Links()
    parser.feed((web / "index.html").read_text())
    for url in parser.urls:
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path:
            continue
        target = (web / unquote(parts.path)).resolve()
        if target.is_dir():
            target = target / "index.html"
        require(target.is_relative_to(web.resolve()) and target.is_file(), f"Broken public page link: {url}")
    require(any(PurePosixPath(urlsplit(url).path) == PurePosixPath("generated/player-source.tar.gz")
                for url in parser.urls), "Missing public corresponding-source download link")


def check(root):
    root = root.resolve()
    web = root / "web"
    actual = {str(path.relative_to(web)) for path in web.rglob("*") if path.is_file()}
    require(actual - {".nojekyll"} == PUBLIC_FILES,
            "Unexpected/missing public assets: " + str(sorted((actual - {".nojekyll"}) ^ PUBLIC_FILES)))
    require(not any(path.is_symlink() for path in web.rglob("*")), "Public assets contain a symlink")
    variants = check_templates(root, web)
    check_emulator(root, web)
    check_screenshot(web)
    source = check_archive(root, web)
    check_static_links(web)
    return {"verified": True, "public_assets": len(actual), "template_variants": variants,
            "source_files": source["file_count"], "source_archive_bytes": source["bytes"],
            "sameboy_revision": REVISION}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.root), indent=2))
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError) as error:
        parser.exit(1, f"Asset verification failed: {error}\n")


if __name__ == "__main__":
    main()
