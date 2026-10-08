"""Rebuild `e2e_suite/vendor/`: the pinned database drivers the suite-v2 apps import.

    python scripts/vendor_drivers.py                       # download, verify, prune, install
    python scripts/vendor_drivers.py --check               # offline: the tree equals the manifest
    python scripts/vendor_drivers.py --check --containers  # + imports in the slim images, no network

The apps run where nothing can be installed (the product builds with no network), so every
build carries its drivers in `vendor/`, the way a lockfile pins a dependency. This script is
the only way that tree changes:

* **Python** (`vendor/python/`, put on `sys.path`): pymongo + dnspython for the MongoDB apps,
  pg8000 + scramp + asn1crypto + python-dateutil + six for the PostgreSQL apps. pymongo ships
  only platform wheels; its compiled `bson._cbson` and `pymongo._cmessage` (and their C
  sources) are dropped, and both packages fall back to their pure-Python code
  (`has_c()` is False), so one tree serves every platform and interpreter.
* **Node** (`vendor/node/node_modules/`, loaded with `createRequire`): mongodb and pg with
  their dependency trees, laid out flat as npm would. Optional and peer dependencies
  (pg-cloudflare, kerberos, snappy, ...) are not vendored.

Every artifact is pinned by version **and** hash (PyPI sha256, npm sha512 integrity), so a
rebuild either reproduces the tree byte for byte or fails. Pruning removes only what no
supported runtime loads: compiled binaries, source maps, type declarations, TypeScript
sources and the browser / React Native builds. Licence files are kept, and the build fails
if a package has none. `vendor/manifest.json` records each package's version, artifact
hash, licence, what was removed and the sha256 of every installed file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR = REPO_ROOT / "e2e_suite" / "vendor"
MANIFEST = VENDOR / "manifest.json"
FORMAT = 1
TIMEOUT_S = 60


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class NpmPackage:
    name: str
    version: str
    integrity: str  # npm's Subresource Integrity string, sha512

    @property
    def tarball_url(self) -> str:
        return (f"https://registry.npmjs.org/{self.name}/-/"
                f"{self.name.rsplit('/', 1)[-1]}-{self.version}.tgz")


PYTHON_PINS = (
    # pymongo publishes no pure wheel. Any wheel's .py files are the same sources; this one
    # matches the Docker images (CPython 3.12, Linux x86-64).
    Wheel("pymongo", "4.18.2",
          "pymongo-4.18.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64"
          ".manylinux_2_28_x86_64.whl",
          "ecde9e735e1f86559e0a71a5bbec671e59a35549bfd8c60968d9f032b05013de"),
    Wheel("dnspython", "2.8.0", "dnspython-2.8.0-py3-none-any.whl",
          "01d9bbc4a2d76bf0db7c1f729812ded6d912bd318d3b1cf81d30c0f845dbf3af"),
    Wheel("pg8000", "1.31.5", "pg8000-1.31.5-py3-none-any.whl",
          "0af2c1926b153307639868d2ee5cef6cd3a7d07448e12736989b10e1d491e201"),
    Wheel("scramp", "1.4.17", "scramp-1.4.17-py3-none-any.whl",
          "a4e3fd2e8169461a28a13777a166d3da94274454f0714a7d3023fee124474ac8"),
    Wheel("asn1crypto", "1.5.1", "asn1crypto-1.5.1-py2.py3-none-any.whl",
          "db4e40728b728508912cbb3d44f19ce188f218e9eba635821bb4b68564f8fd67"),
    Wheel("python-dateutil", "2.9.0.post0", "python_dateutil-2.9.0.post0-py2.py3-none-any.whl",
          "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427"),
    Wheel("six", "1.17.0", "six-1.17.0-py2.py3-none-any.whl",
          "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274"),
)

NODE_PINS = (
    NpmPackage("mongodb", "7.7.0", "sha512-XG1xXEqyouAV8GCb3PuRaujofeXKpAJAPvXYWZgHl6Dz4QHSJ1nRLl0+GpqaLTwKvC0RFE9truOur34RNFY1nw=="),
    NpmPackage("bson", "7.3.3", "sha512-oz3LwE3qGEhTCSyhzQRLY3Bj1NLOsjFMf6tcmY5J8M+3R7EVcmoBfSHWvjUK3yXCLDUsGpIuB3aQL4nDZ1EV/g=="),
    NpmPackage("@mongodb-js/saslprep", "1.6.0", "sha512-19jGXfpctLDJUELLUZGJ5BllcLT7rHdF+gTTaN2jzqu0bGkUhpdcYtgK2Vux+g3zuBAa0TM/W666QMIjYhYujw=="),
    NpmPackage("sparse-bitfield", "3.0.3", "sha512-kvzhi7vqKTfkh0PZU+2D2PIllw2ymqJKujUcyPMd9Y75Nv4nPbGJZXNhxsgdQab2BmlDct1YnfQCguEvHr7VsQ=="),
    NpmPackage("memory-pager", "1.5.0", "sha512-ZS4Bp4r/Zoeq6+NLJpP+0Zzm0pR8whtGPf1XExKLJBAczGMnSi3It14OiNCStjQjM6NU1okjQGSxgEZN8eBYKg=="),
    NpmPackage("mongodb-connection-string-url", "7.0.2", "sha512-ZoS07RoFqpKYQwAk59qmrx8+jJHNHU30UjlU96QktiGn1ltvDr+vCznLX5DiUBLEpMAHatHNWV1nM/74ul66kA=="),
    NpmPackage("whatwg-url", "14.2.0", "sha512-De72GdQZzNTUBBChsXueQUnPKDkg/5A5zp7pFDuQAj5UFoENpiACU0wlCvzpAGnTkj++ihpKwKyYewn/XNUbKw=="),
    NpmPackage("tr46", "5.1.1", "sha512-hdF5ZgjTqgAntKkklYw0R03MG2x/bSzTtkxmIRw/sTNV8YXsCJ1tfLAX23lhxhHJlEf3CRCOCGGWw3vI3GaSPw=="),
    NpmPackage("punycode", "2.3.1", "sha512-vYt7UD1U9Wg6138shLtLOvdAu+8DsC/ilFtEVHcH+wydcSpNE20AfSOduf6MkRFahL5FY7X1oU7nKVZFtfq8Fg=="),
    NpmPackage("webidl-conversions", "7.0.0", "sha512-VwddBukDzu71offAQR975unBIGqfKZpM+8ZX6ySk8nYhVoo5CYaZyzt3YBvYtRtO+aoGlqxPg/B87NGVZ/fu6g=="),
    NpmPackage("@types/whatwg-url", "13.0.0", "sha512-N8WXpbE6Wgri7KUSvrmQcqrMllKZ9uxkYWMt+mCSGwNc0Hsw9VQTW7ApqI4XNrx6/SaM2QQJCzMPDEXE058s+Q=="),
    NpmPackage("@types/webidl-conversions", "7.0.3", "sha512-CiJJvcRtIgzadHCYXw7dqEnMNRjhGZlYK05Mj9OyktqV8uVT8fD2BFOB7S1uwBE3Kj2Z+4UyPmFw/Ixgw/LAlA=="),
    NpmPackage("pg", "8.23.1", "sha512-aL96AHANtWjPLDOLqnhx+ngp9+UK7ETEU8VJrDCGvsSSi/mGLcWYsS6Herg7lmaBJe4uwrfqsa7gTEFaSizDoQ=="),
    NpmPackage("pg-connection-string", "2.14.1", "sha512-qR3kGNPBLpCNtz0evbKA0Y/MRFXwSSdT+pTJvYp/bXTcReZbvX1kzF0IyTc1QnxqF7AZbOeBhNL8R5mYQZV/MA=="),
    NpmPackage("pg-int8", "1.0.1", "sha512-WCtabS6t3c8SkpDBUlb1kjOs7l66xsGdKpIPZsg4wR+B3+u9UAum2odSsF9tnvxg80h4ZxLWMy4pRjOsFIqQpw=="),
    NpmPackage("pg-pool", "3.14.0", "sha512-gKtPkFdQPU3DksooVLi9LsjZxrsBUZIpa+7aVx+LV5pNh0KzP4Zleud2po+ConrxbuXGBJ6Hfer6hdgpIBpBaw=="),
    NpmPackage("pg-protocol", "1.16.1", "sha512-p9VOFMiHB/ZbJATetbg+99PxssTVSQRnyuPSQ67mN1+1KBOjZaZ83ZQzltnxPhJwSsC3nwVjJ10DVJlerbFzLg=="),
    NpmPackage("pg-types", "2.2.0", "sha512-qTAAlrEsl8s4OiEQY69wDvcMIdQN6wdz5ojQiOy6YRMuynxenON0O5oCpJI6lshc6scgAY8qvJ2On/p+CXY0GA=="),
    NpmPackage("pgpass", "1.0.5", "sha512-FdW9r/jQZhSeohs1Z3sI1yxFQNFvMcnmfuj4WBMUTxOrAyLMaTcE1aAMBiTlbMNaXvBCQuVi0R7hd8udDSP7ug=="),
    NpmPackage("postgres-array", "2.0.0", "sha512-VpZrUqU5A69eQyW2c5CA1jtLecCsN2U/bD6VilrFDWq5+5UIEVO7nazS3TEcHf1zuPYO/sqGvUvW62g86RXZuA=="),
    NpmPackage("postgres-bytea", "1.0.1", "sha512-5+5HqXnsZPE65IJZSMkZtURARZelel2oXUEO8rH83VS/hxH5vv1uHquPg5wZs8yMAfdv971IU+kcPUczi7NVBQ=="),
    NpmPackage("postgres-date", "1.0.7", "sha512-suDmjLVQg78nMK2UZ454hAG+OAW+HQPZ6n++TNDUX+L0+uUlLywnoxJKDou51Zm+zTCjrCl0Nq6J9C5hP9vK/Q=="),
    NpmPackage("postgres-interval", "1.2.0", "sha512-9ZhXKM/rw350N1ovuWHbGxnGh/SNJ4cnxHiM0rxE4VN41wsg8P8zWn9hv/buK00RP4WvlOyr/RBDiptyxVbkZQ=="),
    NpmPackage("split2", "4.2.0", "sha512-UcjcJOWknrNkF6PLX83qcHM6KHgVKNkV62Y8a5uYDVv9ydGQVwAHMKqHdJje1VTWpljG0WYpCDhrCdAOYH4TWg=="),
    NpmPackage("xtend", "4.0.2", "sha512-LKYU1iAXJXUgAXn9URjiu+MWhyUXHsvfp7mcuYm9dSUKK0/CjtrUwFAxD82/mCWbtLsGjFIad0wIsod4zrTAEQ=="),
)

#: Compiled extension modules and the C sources they are built from.
PYTHON_DROP_SUFFIXES = (".so", ".pyd", ".dylib", ".dll", ".c", ".h")
#: Source maps, type declarations, TypeScript sources and native addons: nothing Node loads.
NODE_DROP_SUFFIXES = (".map", ".d.ts", ".d.mts", ".d.cts", ".ts", ".node", ".so", ".dll",
                      ".dylib", ".exe")
#: Builds for other runtimes, reached only through the `browser` / `react-native` export
#: conditions, which Node never selects. Listed exactly, so a pin bump that renames one fails.
NODE_DROP_FILES = {
    "bson": ("lib/bson.bundle.js", "lib/bson.mjs", "lib/bson.rn.cjs"),
    "@mongodb-js/saslprep": ("dist/browser.js", "dist/code-points-data-browser.js"),
}
#: Data files that are binary by nature and loaded at runtime (dateutil's zoneinfo archive).
BINARY_DATA = {"python/dateutil/zoneinfo/dateutil-zoneinfo.tar.gz"}
#: Interpreters the tree must support: the host's 3.11 and the images' 3.12; Node 22.
PYTHON_TARGETS = ((3, 11, 0), (3, 12, 0))
NODE_TARGET = (22, 0, 0)
_LICENCE = re.compile(r"^(licen[cs]e|copying|notice)([.-].*)?$", re.IGNORECASE)

#: The offline import test, for the host and for `python:3.12-slim`. argv[1]: vendor/python.
PYTHON_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import bson, dns.version, pg8000.native, pymongo
from bson.decimal128 import Decimal128
doc = {"n": 1, "name": "Mei", "price": Decimal128("12.50"), "lines": [{"k": True, "v": None}]}
assert bson.decode(bson.encode(doc)) == doc, "BSON round trip"
client = pymongo.MongoClient("mongodb://127.0.0.1:9/", connect=False, serverSelectionTimeoutMS=50)
client.close()
print(json.dumps({"python": sys.version.split()[0], "pymongo": pymongo.version,
                  "pymongo_has_c": pymongo.has_c(), "bson_has_c": bson.has_c(),
                  "dnspython": dns.version.version, "pg8000": "ok", "bson_round_trip": "ok"}))
sys.exit(1 if pymongo.has_c() or bson.has_c() else 0)
"""
#: The same for Node, through `createRequire` as the apps load it. argv[2]: vendor/node.
NODE_PROBE = r"""
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";
const root = process.argv[2];
const load = createRequire(path.join(root, "noop.js"));
const { BSON, MongoClient, Decimal128 } = load("mongodb");
const pg = load("pg");
const doc = { n: 1, name: "Mei", price: Decimal128.fromString("12.50"), lines: [{ k: true, v: null }] };
const back = BSON.deserialize(BSON.serialize(doc));
if (back.name !== "Mei" || back.price.toString() !== "12.50" || back.lines[0].k !== true) {
  throw new Error("BSON round trip");
}
new MongoClient("mongodb://127.0.0.1:9/", { serverSelectionTimeoutMS: 50 });
new pg.Pool({ connectionString: "postgresql://u:p@127.0.0.1:9/d" });
const version = (name) => JSON.parse(readFileSync(path.join(root, "node_modules", name, "package.json"), "utf8")).version;
console.log(JSON.stringify({ node: process.versions.node, mongodb: version("mongodb"),
  bson: version("bson"), pg: version("pg"), loaded: "ok", bson_round_trip: "ok" }));
"""


class VendorError(RuntimeError):
    pass


@dataclass
class Installed:
    """One package as installed: what it came from, what was kept and what was dropped."""

    ecosystem: str
    name: str
    version: str
    artifact: str
    artifact_hash: str
    licence: str
    licence_files: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # vendor-relative POSIX paths
    requires: dict[str, str] = field(default_factory=dict)
    runtime: str = ""  # Requires-Python / engines.node


# -- downloading -------------------------------------------------------------------------------


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "wta-vendor-drivers/1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - pinned
        return response.read()


def fetch_wheel(pin: Wheel) -> bytes:
    index = json.loads(_fetch(f"https://pypi.org/pypi/{pin.name}/{pin.version}/json"))
    entry = next((f for f in index["urls"] if f["filename"] == pin.filename), None)
    if entry is None:
        raise VendorError(f"{pin.name} {pin.version}: PyPI lists no {pin.filename}")
    if entry["digests"]["sha256"] != pin.sha256:
        raise VendorError(f"{pin.filename}: PyPI's digest differs from the pinned sha256")
    data = _fetch(entry["url"])
    if hashlib.sha256(data).hexdigest() != pin.sha256:
        raise VendorError(f"{pin.filename}: downloaded bytes do not match the pinned sha256")
    return data


def fetch_npm(pin: NpmPackage) -> bytes:
    algorithm, _, expected = pin.integrity.partition("-")
    if algorithm != "sha512":
        raise VendorError(f"{pin.name}: integrity must be sha512")
    data = _fetch(pin.tarball_url)
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
        raise VendorError(f"{pin.name}@{pin.version}: tarball does not match the pinned integrity")
    return data


# -- unpacking and pruning ---------------------------------------------------------------------


def _safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise VendorError(f"unsafe archive path {name!r}")
    return path


def _write(root: Path, relative: PurePosixPath, data: bytes) -> None:
    target = root.joinpath(*relative.parts)
    if target.exists():
        raise VendorError(f"two packages install {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _metadata(text: str) -> dict[str, list[str]]:
    headers = HeaderParser().parsestr(text, headersonly=True)
    return {key: headers.get_all(key) or [] for key in set(headers.keys())}


def unpack_wheel(pin: Wheel, data: bytes, staged: Path) -> Installed:
    archive = zipfile.ZipFile(io.BytesIO(data))
    dist_info = next(n.split("/")[0] for n in archive.namelist() if ".dist-info/" in n)
    meta = _metadata(archive.read(f"{dist_info}/METADATA").decode("utf-8"))
    if meta.get("Version", [""])[0] != pin.version:
        raise VendorError(f"{pin.filename}: METADATA says version {meta.get('Version')}")
    installed = Installed(
        "python", pin.name, pin.version, pin.filename, f"sha256:{pin.sha256}",
        licence=((meta.get("License-Expression") or meta.get("License") or ["?"])[0]
                 .splitlines()[0][:80]),
        runtime=(meta.get("Requires-Python") or [""])[0])
    for requirement in meta.get("Requires-Dist", []):
        spec, _, marker = requirement.partition(";")
        if "extra" in marker:
            continue  # optional features (snappy, kerberos, ocsp, ...) are not vendored
        if marker.strip():
            raise VendorError(f"{pin.name}: requirement {requirement!r} has a marker this "
                              f"script does not evaluate; decide it by hand")
        match = re.match(r"\s*([A-Za-z0-9._-]+)\s*\(?([^)]*)\)?", spec)
        installed.requires[_normalise(match.group(1))] = match.group(2).strip()
    kept: list[tuple[PurePosixPath, bytes]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        relative = _safe_relative(info.filename)
        if relative.name.endswith(PYTHON_DROP_SUFFIXES):
            installed.removed.append(str(relative))
            continue
        kept.append((relative, archive.read(info)))
    if installed.removed:
        record = f"{dist_info}/RECORD"
        kept = [(path, _without_record_lines(body, installed.removed) if str(path) == record
                 else body) for path, body in kept]
    for relative, body in kept:
        _write(staged, relative, body)
        installed.files.append(f"python/{relative}")
        if relative.parts[0] == dist_info and _LICENCE.match(relative.name):
            installed.licence_files.append(f"python/{relative}")
    return installed


def _without_record_lines(record: bytes, removed: list[str]) -> bytes:
    """RECORD lists every file of the wheel; drop the lines of the files not installed."""
    gone = set(removed)
    lines = record.decode("utf-8").splitlines(keepends=True)
    return "".join(line for line in lines if line.split(",", 1)[0] not in gone).encode("utf-8")


def unpack_npm(pin: NpmPackage, data: bytes, staged: Path) -> Installed:
    package_root = PurePosixPath("node_modules", *pin.name.split("/"))
    explicit = set(NODE_DROP_FILES.get(pin.name, ()))
    removed, kept = [], []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise VendorError(f"{pin.name}: {member.name} is a link or device")
            inner = _safe_relative(member.name)
            relative = PurePosixPath(*inner.parts[1:])  # npm packs under package/ (or <name>/)
            body = archive.extractfile(member).read()
            if relative.name.endswith(NODE_DROP_SUFFIXES) or str(relative) in explicit:
                removed.append(str(relative))
                explicit.discard(str(relative))
                continue
            kept.append((relative, body))
    if explicit:
        raise VendorError(f"{pin.name}: files listed for removal not in the package: "
                          f"{sorted(explicit)}")
    manifest = json.loads(dict(kept)[PurePosixPath("package.json")])
    if manifest.get("name") != pin.name or manifest.get("version") != pin.version:
        raise VendorError(f"{pin.name}@{pin.version}: package.json says "
                          f"{manifest.get('name')}@{manifest.get('version')}")
    installed = Installed("node", pin.name, pin.version, pin.tarball_url.rsplit("/", 1)[-1],
                          pin.integrity, licence=str(manifest.get("license", "?")),
                          removed=sorted(removed), requires=dict(manifest.get("dependencies", {})),
                          runtime=str((manifest.get("engines") or {}).get("node", "")))
    for relative, body in kept:
        target = package_root / relative
        _write(staged, target, body)
        installed.files.append(f"node/{target}")
        if len(relative.parts) == 1 and _LICENCE.match(relative.name):
            installed.licence_files.append(f"node/{target}")
    if not installed.licence_files:
        # A few small packages (pgpass) carry their licence text in the README instead.
        for relative, body in kept:
            if (len(relative.parts) == 1 and relative.name.lower().startswith("readme")
                    and re.search(rb"^#+\s*licen[cs]e", body, re.IGNORECASE | re.MULTILINE)):
                installed.licence_files.append(f"node/{package_root / relative}#licence")
    return installed


# -- version ranges (the subsets PEP 440 and npm semver this tree uses) -------------------------


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _numbers(version: str, width: int = 3) -> tuple[int, ...]:
    parts = [int(p) for p in re.findall(r"\d+", version.split("+")[0].split("-")[0])[:width]]
    return tuple(parts + [0] * (width - len(parts)))


def pep440_satisfied(version: tuple[int, ...], specifier: str) -> bool:
    for clause in filter(None, (c.strip() for c in specifier.split(","))):
        match = re.fullmatch(r"(>=|<=|==|!=|>|<|~=)\s*([0-9][0-9.*a-z]*)", clause)
        if not match:
            raise VendorError(f"unsupported Python version specifier {clause!r}")
        operator, wanted = match.groups()
        if wanted.endswith(".*"):
            prefix = _numbers(wanted[:-2], width=wanted.count("."))
            equal = version[:len(prefix)] == prefix
            if (operator == "==") != equal:
                return False
            continue
        other = _numbers(wanted)
        compatible = _numbers(wanted.rsplit(".", 1)[0], width=max(wanted.count("."), 1))
        ok = {">=": version >= other, "<=": version <= other, "==": version == other,
              "!=": version != other, ">": version > other, "<": version < other,
              "~=": version >= other and version[:len(compatible)] == compatible}[operator]
        if not ok:
            return False
    return True


def semver_satisfied(version: tuple[int, ...], spec: str) -> bool:
    for alternative in spec.split("||"):
        clauses = re.findall(r"(\^|~|>=|<=|>|<|=)?\s*([0-9x*][0-9x.*]*)", alternative.strip())
        if not alternative.strip() or alternative.strip() in ("*", "x"):
            return True
        if all(_semver_clause(version, operator, wanted) for operator, wanted in clauses):
            return True
    return False


def _semver_clause(version: tuple[int, ...], operator: str, wanted: str) -> bool:
    fixed = [int(p) for p in wanted.split(".") if p.isdigit()]
    base = tuple(fixed + [0] * (3 - len(fixed)))
    if operator in ("", "=") and len(fixed) < 3:  # 1.x, 1.2.x
        return version[:len(fixed)] == tuple(fixed)
    if operator == "^":
        significant = next((i for i, n in enumerate(base) if n), 2)
        return version >= base and version[:significant + 1] == base[:significant + 1]
    if operator == "~":
        return version >= base and version[:2] == base[:2]
    return {"": version == base, "=": version == base, ">=": version >= base,
            "<=": version <= base, ">": version > base, "<": version < base}[operator]


def check_closure(packages: list[Installed]) -> list[str]:
    """Every runtime dependency is vendored at a version its dependant accepts, and every
    package supports the runtimes the suite targets."""
    problems = []
    for ecosystem in ("python", "node"):
        group = [p for p in packages if p.ecosystem == ecosystem]
        key = _normalise if ecosystem == "python" else (lambda n: n)
        versions = {key(p.name): p.version for p in group}
        for package in group:
            for name, spec in package.requires.items():
                have = versions.get(key(name))
                if have is None:
                    problems.append(f"{package.name} needs {name} {spec}, which is not vendored")
                    continue
                satisfied = (pep440_satisfied(_numbers(have), spec) if ecosystem == "python"
                             else semver_satisfied(_numbers(have), spec))
                if not satisfied:
                    problems.append(f"{package.name} needs {name} {spec}; vendored {have}")
            if package.runtime:
                targets = PYTHON_TARGETS if ecosystem == "python" else (NODE_TARGET,)
                for target in targets:
                    ok = (pep440_satisfied(target, package.runtime) if ecosystem == "python"
                          else semver_satisfied(target, package.runtime))
                    if not ok:
                        problems.append(f"{package.name} requires {ecosystem} {package.runtime}; "
                                        f"the suite runs {'.'.join(map(str, target))}")
            if not package.licence_files:
                problems.append(f"{package.name}: no licence file")
    return problems


# -- the tree on disk --------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_files(root: Path) -> dict[str, str]:
    """`{relative POSIX path: sha256}` of the vendored files under `root` (python/, node/),
    ignoring bytecode caches the interpreters may have written."""
    out = {}
    for language in ("python", "node"):
        base = root / language
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                out[path.relative_to(root).as_posix()] = _sha256(path)
    return out


def binary_files(root: Path, files: dict[str, str]) -> list[str]:
    """Files that look binary (a NUL byte early on), other than the declared data files."""
    found = []
    for name in files:
        if name not in BINARY_DATA and b"\0" in (root / name).read_bytes()[:8192]:
            found.append(name)
    return found


def _tree_digest(files: dict[str, str]) -> str:
    lines = "".join(f"{name}\0{digest}\n" for name, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _probe_scripts(directory: Path) -> None:
    (directory / "probe.py").write_text(PYTHON_PROBE, encoding="utf-8")
    (directory / "probe.mjs").write_text(NODE_PROBE, encoding="utf-8")


def _probe_result(label: str, completed: subprocess.CompletedProcess) -> dict:
    if completed.returncode != 0:
        raise VendorError(f"{label}: import test failed:\n{completed.stdout}{completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def smoke_test(root: Path) -> dict:
    """Import both trees on this machine, as the apps import them, writing no bytecode."""
    results: dict = {}
    with tempfile.TemporaryDirectory(prefix="wta-vendor-probe-") as probes:
        _probe_scripts(Path(probes))
        results["host python"] = _probe_result("host python", subprocess.run(
            [sys.executable, "-B", "-I", str(Path(probes, "probe.py")), str(root / "python")],
            capture_output=True, text=True, timeout=120, check=False))
        node = shutil.which("node")
        if node is None:
            results["host node"] = "skipped: node is not on PATH"
        else:
            results["host node"] = _probe_result("host node", subprocess.run(
                [node, str(Path(probes, "probe.mjs")), str(root / "node")],
                capture_output=True, text=True, timeout=120, check=False))
    return results


def container_test(root: Path) -> dict:
    """The same import test inside the images the apps' Dockerfiles use, with no network."""
    if shutil.which("docker") is None:
        raise VendorError("docker is not on PATH")
    results = {}
    with tempfile.TemporaryDirectory(prefix="wta-vendor-probe-") as probes:
        _probe_scripts(Path(probes))
        mounts = ["--mount", f"type=bind,source={root},target=/vendor,readonly",
                  "--mount", f"type=bind,source={probes},target=/probe,readonly"]
        for image, command in (
                ("python:3.12-slim", ["python", "-B", "/probe/probe.py", "/vendor/python"]),
                ("node:22-slim", ["node", "/probe/probe.mjs", "/vendor/node"])):
            results[image] = _probe_result(image, subprocess.run(
                ["docker", "run", "--rm", "--network", "none", *mounts, image, *command],
                capture_output=True, text=True, timeout=300, check=False))
    return results


# -- commands ----------------------------------------------------------------------------------


def build(*, keep: bool) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="wta-vendor-"))
    staged = workdir / "vendor"
    try:
        packages: list[Installed] = []
        for pin in PYTHON_PINS:
            print(f"  python  {pin.name} {pin.version}", flush=True)
            packages.append(unpack_wheel(pin, fetch_wheel(pin), staged / "python"))
        for pin in NODE_PINS:
            print(f"  node    {pin.name}@{pin.version}", flush=True)
            packages.append(unpack_npm(pin, fetch_npm(pin), staged / "node"))
        problems = check_closure(packages)
        files = tree_files(staged)
        problems += [f"binary file kept: {name}" for name in binary_files(staged, files)]
        if problems:
            raise VendorError("the staged tree is not admissible:\n  " + "\n  ".join(problems))
        smoke = smoke_test(staged)
        for label, result in smoke.items():
            print(f"  {label}: {json.dumps(result)}")
        for language in ("python", "node"):
            shutil.rmtree(VENDOR / language, ignore_errors=True)
            shutil.copytree(staged / language, VENDOR / language)
        installed = tree_files(VENDOR)
        if installed != files:
            raise VendorError("the installed tree differs from the staged one")
        manifest = _manifest(packages, installed)
        MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=False) + "\n",
                            encoding="utf-8", newline="\n")
        _print_summary(manifest)
        return 0
    finally:
        if keep:
            print(f"  staging kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _manifest(packages: list[Installed], files: dict[str, str]) -> dict:
    sizes = {name: (VENDOR / name).stat().st_size for name in files}
    entries = []
    for package in packages:
        own = {name: files[name] for name in package.files}
        entries.append({
            "ecosystem": package.ecosystem, "name": package.name, "version": package.version,
            "artifact": package.artifact, "artifact_hash": package.artifact_hash,
            "licence": package.licence, "licence_files": package.licence_files,
            "requires": package.requires, "runtime": package.runtime,
            "removed": package.removed, "file_count": len(own),
            "bytes": sum(sizes[name] for name in own), "tree_sha256": _tree_digest(own),
        })
    totals = {}
    for language in ("python", "node"):
        own = {n: d for n, d in files.items() if n.startswith(f"{language}/")}
        totals[language] = {"file_count": len(own), "bytes": sum(sizes[n] for n in own),
                            "tree_sha256": _tree_digest(own)}
    return {"format": FORMAT, "generator": "scripts/vendor_drivers.py",
            "note": "Rebuilt only by scripts/vendor_drivers.py; verify with --check.",
            "totals": totals, "packages": entries, "files": files}


def _print_summary(manifest: dict) -> None:
    for entry in manifest["packages"]:
        print(f"  {entry['ecosystem']:6} {entry['name']:30} {entry['version']:12} "
              f"{entry['file_count']:4} files {entry['bytes'] / 1e6:6.2f} MB  "
              f"removed {len(entry['removed'])}")
    for language, total in manifest["totals"].items():
        print(f"  {language}: {total['file_count']} files, {total['bytes'] / 1e6:.2f} MB, "
              f"tree {total['tree_sha256'][:16]}")


def check(*, containers: bool) -> int:
    if not MANIFEST.is_file():
        print(f"no {MANIFEST.relative_to(REPO_ROOT)}; run without --check first")
        return 1
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    actual = tree_files(VENDOR)
    missing = sorted(set(recorded) - set(actual))
    extra = sorted(set(actual) - set(recorded))
    changed = sorted(n for n in set(recorded) & set(actual) if recorded[n] != actual[n])
    for label, names in (("missing", missing), ("unexpected", extra), ("changed", changed)):
        for name in names[:20]:
            print(f"  {label}: {name}")
    if missing or extra or changed:
        print(f"vendor tree differs from the manifest: {len(missing)} missing, "
              f"{len(extra)} unexpected, {len(changed)} changed")
        return 1
    print(f"vendor tree matches the manifest ({len(actual)} files)")
    for label, result in smoke_test(VENDOR).items():
        print(f"  {label}: {json.dumps(result)}")
    if containers:
        for image, result in container_test(VENDOR).items():
            print(f"  {image} (--network none): {json.dumps(result)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify the installed tree offline instead of rebuilding it")
    parser.add_argument("--containers", action="store_true",
                        help="with --check: also import in python:3.12-slim and node:22-slim")
    parser.add_argument("--keep", action="store_true", help="keep the staging directory")
    args = parser.parse_args()
    try:
        return check(containers=args.containers) if args.check else build(keep=args.keep)
    except VendorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
