#!/usr/bin/env python3
"""Download the Defense Mechanism LSDj corpus (Python standard library + 7-Zip).

The default source list is the complete set of downloadable save/project and kit
links on https://defensemech.com/songs/ as of 2026-10-07. Cover-image links count.
Use --refresh to discover the current catalog instead. Files and their generated
source/license/hash manifest go in tests/corpus/, which should remain untracked.

Songs: Defense Mechanism, CC BY-SA 4.0 unless otherwise noted in the archive.
https://creativecommons.org/licenses/by-sa/4.0/
The site's song license does not explicitly cover its standalone kit downloads;
those are preserved as local playback/test assets with their license unspecified.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen


CATALOG_URL = "https://defensemech.com/songs/"
SNAPSHOT_DATE = "2026-10-07"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
BOTB_URL = "https://battleofthebits.com/barracks/Profile/DefenseMechanism/Browser"
ASSET_SUFFIXES = {".7z", ".zip", ".sav", ".lsdprj", ".lsdsng", ".kit"}
ARCHIVE_SUFFIXES = {".7z", ".zip"}
SAVE_SUFFIXES = {".sav", ".lsdprj", ".lsdsng"}
# SHA-256 pins make repeat downloads reproduce this snapshot. The empty pin on
# indigo.7z records a broken source link: it redirects to an HTML catalog page.
SOURCES = [
    ("song", "retrospectacles.7z", "c2aeb6ee9eeb81a8d88f8c4f35ee570de38c39e5d3a85fe3c84204840b721e0c"),
    ("song", "rodentquotient.7z", "cd72bbf45d1c8867c0aaf68040ffb4cf295a41c5ea7f17b9798683babe7aa5f9"),
    ("song", "slycology.7z", "2b0662de0c8fc5a4837cf5b5917f6f8317758d40939f0abd173936bff31fdd87"),
    ("song", "sunburst.7z", "74cbb891db588c1c71004bc24df80d23034366169aad9cbca381bf829d3f8469"),
    ("song", "continuo_v939.lsdprj", "e770a91d628250738d3eb3419cda2ecb9555903ff2acca9a5a46d11abb528d01"),
    ("song", "afterlife.7z", "2705729bec4889cc4a5fb15dcfa748a3c624d8ecd907afa2be540aafebbd8940"),
    ("song", "malfunktion.7z", "94eb8be814a8e7711e7e3a11cdac932acb5f808334ba06ec59e1b53e80594cdd"),
    ("song", "oceanworld.7z", "217b7b53de90b0d9d09130effb5af124c64ec84b5cc672d7c063c88e0c55860e"),
    ("song", "indigo.7z", ""),
    ("song", "kashiwa.7z", "11e1457d4ddad9d55c979ffcf2cd2e62f491542133630a5d03a75af53070b572"),
    ("song", "infinite.7z", "a85e404bbdcf04de3dda3ec6576ffe7a4a41936e879c7e8aba1d2375749ece25"),
    ("song", "superscience.7z", "5918460f4961189f0fc443bf8ffe80845366958333d4f29bd385166169580611"),
    ("song", "significant.7z", "f14a3949c14eb63b9f6c730e2314191b10ea767d07bfaec900bac9bc5a98d042"),
    ("song", "barkanew.7z", "0f6e4ed0a8aef2fa0b014bb8138257076287e625d6b1fd4dafa1a508e20131b1"),
    ("song", "moonmission.7z", "19e3efd7bf7852af359215ad585c443cfaa8103de862142ae7c3677dfb047ae8"),
    ("song", "dontstop.7z", "c2f01a4eb30255886667b33819e0cffc3fdfee17bda034564b49512ea9f69c69"),
    ("song", "bassitch.7z", "f313cf430e42ae4adddc05d999f12ea10da078e439239fb06a67747c1716519c"),
    ("song", "beatjuice.7z", "1bfc25e67e04effec212f856467fd46f3f4873eefe0a79b0e554e19027ed2891"),
    ("song", "fruitpunch.7z", "009356e390def57fa913c1739fc55bc53b369424c311f065bcd6be536287e031"),
    ("song", "wow.7z", "2eb66ac8342e3ef9780f625f3ae78925668dae53b31d79f0025c16c9512ffd8a"),
    ("song", "superskypop.7z", "9b7f545fc10c48da14bc45d7934552327fa9815ed346d37aac059c9162be407d"),
    ("song", "triac.7z", "9e9d92e42b4bc5231dffc47f436e0c3a78bf14693f074f8ec01b58cfe873f840"),
    ("song", "trainjam.7z", "506dd38dc2452ef7e4ad7a805ec20b0c2a3751123059d878b65fe9baa6c865c6"),
    ("song", "revision.7z", "05a35a3895b3033dee7d08493a2664c0b51be8038a44fd6616e49a69fb146bff"),
    ("song", "DM_botb.7z", "6dfc9c3e320246565abb494635431fe8bbc0484dc8760f1e422a2614d38d47a6"),
    ("song", "wb2020.7z", "e747ee5a712ce9e4a8102b01aa447bd85c2dfed10d261aaa4276eb834022df70"),
    ("template", "LSDJtemplates.7z", "955bccbc0d1da7b8790d94b7059c6c5d818ebc5499609c2229ba9e81df4030d6"),
    ("tutorial", "kicks-v9.2+.lsdprj", "ead932b12417e829c3d8f3e0fbfc8e948744016c3e085015152c0f9a9905fd5d"),
    ("kit", "UTKIT.kit", "11c306e514689dea84bedef96a12c258aa31be09b776460ea7f63d6818a2eec6"),
    ("kit", "DM-AMENkits.zip", "0b856b030ed7394be24d041488346c6f6e6e2b769dca5e08b41dff3e12c15ae8"),
    ("kit", "CHORDS.kit", "6fb4ecb04588d0e59bf2dd8ec8302de36da4787aaa7e43ad00be5469918471fc"),
    ("kit", "APOLLOkits.zip", "67b9bb62c5a1d710d5210228fefdde6654f2d01ff877071344b6dc9b7891bf0f"),
    ("kit", "STRINGkits.zip", "fa43c5fc6fb5a8449385197983a7ea60b451b70b4cd52ab5271803b2dc23c4c4"),
    ("kit", "DONT.kit", "767a8d0e6ba33251461e9de4a29e8f03a0357f7bb1343a1915ec3c980c2b40c6"),
    ("kit", "ROCDATBIT.kit", "9a4013b4d3a0b6feccc95902f89012f71ee6d07bd69fa72e66546d70e86ca38b"),
    ("kit", "STRANGE.kit", "7d8fc1c67746647da4f5f593c65b7758a25410a8fff1dee456e80359dcae5d6b"),
    ("kit", "AMAZING.kit", "71576bb986dac85444a0e5923bc2e818ca252113591ddce0bf244f995365978a"),
    ("kit", "LouisCole.7z", "ad85d54138ca27d3fbd6a9313b040e2249644c365758280a5c0bc2243ce58db5"),
    ("kit", "SUNNEXO.kit", "3212c6965ffd483b0504093b54dc4717e59142b63785f97b0daee06b17c6cc6c"),
]


class CatalogParser(HTMLParser):
    """Read real hrefs, including links whose only text is a cover image."""

    def __init__(self) -> None:
        super().__init__()
        self.category = "song"
        self.sources: list[tuple[str, str, str]] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text.startswith("LSDj Template files"):
            self.category = "template"
        elif text.startswith("Kick tutorial LSDPRJ file"):
            self.category = "tutorial"
        elif text.startswith("Here you can also find the LSDj kits"):
            self.category = "kit"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urljoin(CATALOG_URL, href)
        if Path(urlsplit(url).path).suffix.lower() not in ASSET_SUFFIXES:
            return
        if urlsplit(url).hostname != "defensemech.com":
            raise ValueError(f"Unexpected offsite asset URL: {url}")
        entry = (self.category, url, "")
        if entry not in self.sources:
            self.sources.append(entry)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_asset(path: Path, url: str) -> None:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix not in ASSET_SUFFIXES:
        return
    with path.open("rb") as stream:
        header = stream.read(512)
    if header.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError("Asset link returned an HTML page instead of the requested save/archive")
    if suffix == ".7z" and not header.startswith(b"7z\xbc\xaf'\x1c"):
        raise ValueError("Asset does not have a 7-Zip archive signature")
    if suffix == ".zip" and not header.startswith(b"PK"):
        raise ValueError("Asset does not have a ZIP archive signature")


def download(url: str, path: Path, expected_sha256: str = "") -> None:
    if path.is_file() and (not expected_sha256 or sha256(path) == expected_sha256):
        try:
            validate_asset(path, url)
            return
        except ValueError:
            path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(quote(url, safe=":/?&=+%"), headers={
        "User-Agent": "lsdj-player-test-corpus/1.0 (+https://defensemech.com/songs/)"
    })
    partial = path.with_name(path.name + ".part")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as response, partial.open("wb") as output:
                if response.headers.get_content_type() == "text/html" and Path(urlsplit(url).path).suffix.lower() in ASSET_SUFFIXES:
                    raise ValueError(f"Asset link redirects to HTML ({response.url})")
                shutil.copyfileobj(response, output)
            validate_asset(partial, url)
            actual = sha256(partial)
            if expected_sha256 and actual != expected_sha256:
                raise ValueError(f"Changed source {url}: expected {expected_sha256}, got {actual}. "
                                 "Use --refresh to intentionally fetch the current catalog.")
            partial.replace(path)
            return
        except Exception as error:
            partial.unlink(missing_ok=True)
            if attempt == 2 or isinstance(error, ValueError):
                raise
            time.sleep(attempt + 1)


def extract_archive(archiver: str, archive: Path, destination: Path) -> None:
    listing = subprocess.run([archiver, "l", "-slt", "-ba", str(archive)],
                             check=True, capture_output=True, text=True).stdout
    for line in listing.splitlines():
        if line.startswith("Path = "):
            name = line.removeprefix("Path = ")
            # Check both separators/drives even when running on a POSIX host.
            windows = PureWindowsPath(name)
            unix = PurePosixPath(name.replace("\\", "/"))
            if windows.drive or windows.is_absolute() or unix.is_absolute() or ".." in unix.parts:
                raise ValueError(f"Unsafe archive member in {archive}: {name}")
        if line.startswith(("Symbolic Link = ", "Hard Link = ")) or re.match(r"Attributes = .* l", line):
            raise ValueError(f"Archive links are unsupported: {archive}")
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run([archiver, "x", "-y", f"-o{destination}", str(archive)],
                   check=True, capture_output=True, text=True)


def inventory(root: Path, category: str, source_url: str, corpus: Path) -> list[dict]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected symbolic link: {path}")
        suffix = path.suffix.lower()
        version = re.findall(r"(?i)(?:^|[_\s-])v([0-9][0-9a-z.+]*(?:-v[0-9a-z.+]+)?)", path.stem)
        files.append({
            "path": path.relative_to(corpus).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "type": suffix.lstrip(".") or "other",
            "category": category,
            "source_url": source_url,
            "version_hints": version,
        })
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destination", type=Path,
                        default=Path(__file__).resolve().parents[1] / "tests" / "corpus")
    parser.add_argument("--refresh", action="store_true", help="Discover current links instead of using the pinned snapshot")
    parser.add_argument("--songs-only", action="store_true", help="Omit the separate kit downloads (archives still include their own kits)")
    parser.add_argument("--list", action="store_true", help="Print URLs and categories, without downloading")
    parser.add_argument("--7z", dest="archiver", help="Path to 7zz or 7z")
    args = parser.parse_args()
    sources = [(category, urljoin(CATALOG_URL, name), checksum) for category, name, checksum in SOURCES]
    corpus = args.destination.expanduser().resolve()
    catalog_html = None
    if args.refresh:
        with tempfile.TemporaryDirectory() as temporary:
            html = Path(temporary) / "catalog.html"
            download(CATALOG_URL, html)
            catalog = CatalogParser()
            catalog_html = html.read_text(encoding="utf-8")
            catalog.feed(catalog_html)
            sources = catalog.sources
            if not sources:
                raise ValueError("No downloadable assets found in the catalog")
    if args.songs_only:
        sources = [source for source in sources if source[0] != "kit"]
    if args.list:
        for category, url, _ in sources:
            print(f"{category:8} {url}")
        return 0
    corpus.mkdir(parents=True, exist_ok=True)
    if catalog_html is not None:
        (corpus / "catalog.html").write_text(catalog_html, encoding="utf-8")
    archiver = args.archiver or shutil.which("7zz") or shutil.which("7z") or shutil.which("7za")
    if not archiver:
        parser.error("7-Zip is required to extract .7z files; install 7zz or pass --7z /path/to/7zz")
    files: list[dict] = []
    downloads: list[dict] = []
    failures: list[dict] = []
    for category, url, checksum in sources:
        name = unquote(Path(urlsplit(url).path).name)
        archive = corpus / "downloads" / name
        try:
            print(f"[{category}] {name}", flush=True)
            # --refresh deliberately bypasses pins, including pre-existing downloads.
            if args.refresh:
                archive.unlink(missing_ok=True)
            download(url, archive, checksum)
            # Extract into a fresh staging directory to avoid stale archive members.
            with tempfile.TemporaryDirectory(prefix=".extract-", dir=corpus) as staging:
                staged = Path(staging)
                if archive.suffix.lower() in ARCHIVE_SUFFIXES:
                    extract_archive(archiver, archive, staged)
                else:
                    shutil.copyfile(archive, staged / name)
                destination = corpus / "files" / archive.stem
                if destination.exists():
                    shutil.rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(destination)
                files.extend(inventory(destination, category, url, corpus))
            downloads.append({"url": url, "category": category,
                              "path": archive.relative_to(corpus).as_posix(),
                              "bytes": archive.stat().st_size, "sha256": sha256(archive)})
        except Exception as error:
            failures.append({"url": url, "error": str(error)})
            print(f"FAILED: {url}: {error}", file=sys.stderr, flush=True)
    counts = Counter(file["type"] for file in files)
    manifest = {
        "catalog_url": CATALOG_URL,
        "snapshot_date": None if args.refresh else SNAPSHOT_DATE,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "DEFENSE MECHANISM (Defense Mechanism), https://defensemech.com/",
        "song_license": "CC BY-SA 4.0 unless otherwise noted in individual archives",
        "song_license_url": LICENSE_URL,
        "standalone_kit_license": "Not explicitly specified by the catalog; inspect original archive notices",
        "scope": "All direct save, project, archive, and optional kit links from the catalog, including cover-image links",
        "external_links": [{"url": BOTB_URL,
                            "status": "Not included: profile blocks guest access (HTTP 403) and requires login; checked on 2026-10-07"}],
        "downloads": downloads,
        "files": files,
        "counts": dict(sorted(counts.items())),
        "save_project_files": sum(counts[suffix.lstrip(".")] for suffix in SAVE_SUFFIXES),
        "failures": failures,
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Downloaded {len(downloads)}/{len(sources)} sources; "
          f"{manifest['save_project_files']} saves/projects; types: {dict(sorted(counts.items()))}")
    print(f"Manifest: {corpus / 'manifest.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
