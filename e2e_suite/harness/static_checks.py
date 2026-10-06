"""Checks that need no server and no browser."""

from __future__ import annotations

import re
from pathlib import Path

from .manifest import AppManifest

SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".json", ".svg"}
#: Any absolute URL that is not this machine. Comments count too: a URL in a comment is a
#: URL someone may later fetch.
_EXTERNAL_URL = re.compile(r"\b(?:https?|wss?)://(?!127\.0\.0\.1|localhost|0\.0\.0\.0)[\w.-]+",
                           re.IGNORECASE)
_NONDETERMINISM = {
    ".py": re.compile(r"\b(random\.|uuid\.uuid[14]|datetime\.now\(|datetime\.utcnow\(|"
                      r"time\.time\(\)|date\.today\(\))"),
    ".js": re.compile(r"\b(Math\.random|Date\.now\(|new Date\(\s*\)|crypto\.randomUUID|"
                      r"performance\.now)"),
}
_NONDETERMINISM[".mjs"] = _NONDETERMINISM[".js"]
#: How source code asks for a label: `t("key")` / `t('key')` in Python and JavaScript.
_VOCAB_USE = re.compile(r"""\bt\(\s*["']([A-Za-z0-9_.-]+)["']\s*[,)]""")
GENERIC_PROGRESS_WORDS = {"continue", "next", "submit"}


def _sources(app: AppManifest) -> list[Path]:
    return [p for p in (app.root / "src").rglob("*")
            if p.is_file() and p.suffix in SOURCE_SUFFIXES and "__pycache__" not in p.parts]


def external_urls(app: AppManifest) -> list[str]:
    found = []
    for path in _sources(app) + [app.root / "Dockerfile"]:
        if path.is_file():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _EXTERNAL_URL.search(line) and "wta-allow-url" not in line:
                    found.append(f"{path.relative_to(app.root)}:{number}: {line.strip()[:100]}")
    return found


def nondeterminism(app: AppManifest) -> list[str]:
    found = []
    for path in _sources(app):
        pattern = _NONDETERMINISM.get(path.suffix)
        if pattern is None:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "wta-clock" not in line:
                found.append(f"{path.relative_to(app.root)}:{number}: {line.strip()[:100]}")
    return found


def vocabulary(app: AppManifest) -> dict:
    """Completeness of both profiles against what the source asks for, and how they differ."""
    default, alt = app.vocab("default"), app.vocab("alt")
    used: set[str] = set()
    for path in _sources(app):
        used.update(_VOCAB_USE.findall(path.read_text(encoding="utf-8")))
    problems = []
    if set(default) != set(alt):
        problems.append(f"profiles have different keys: only default "
                        f"{sorted(set(default) - set(alt))}, only alt {sorted(set(alt) - set(default))}")
    for profile, table in (("default", default), ("alt", alt)):
        missing = sorted(used - set(table))
        if missing:
            problems.append(f"{profile} lacks keys used by the source: {missing}")
        empty = sorted(k for k, v in table.items() if not str(v).strip())
        if empty:
            problems.append(f"{profile} has empty labels: {empty}")
    action_keys = sorted(k for k in default if k.startswith("act."))
    same = sorted(k for k in action_keys if str(default[k]).strip().lower()
                  == str(alt.get(k, "")).strip().lower())
    if same:
        problems.append(f"action labels identical in both profiles: {same}")
    progress = app.data.get("vocabulary", {}).get("progress_keys", [])
    generic = sorted(f"{profile}:{k}={table[k]!r}" for profile, table in
                     (("default", default), ("alt", alt)) for k in progress
                     if k in table and str(table[k]).strip().lower() in GENERIC_PROGRESS_WORDS)
    if generic:
        problems.append(f"progress controls use generic words: {generic}")
    unused = sorted(set(default) - used)
    return {
        "keys": len(default), "keys_used_by_source": len(used & set(default)),
        "action_keys": len(action_keys), "unused_keys": unused, "problems": problems,
        "default_action_labels": {k: default[k] for k in action_keys},
        "alt_action_labels": {k: alt[k] for k in action_keys},
    }


def _trace_steps(app: AppManifest) -> list[tuple[str, dict]]:
    d = app.data
    out = []
    for w in d.get("workflows", []):
        out += [(f"workflow {w['name']}", s) for s in w.get("trace", [])]
    for s in d.get("state_dependent", []):
        for side in ("useless", "useful"):
            part = s.get(side) or {}
            out += [(f"state_dependent {s['name']}.{side}", st)
                    for st in part.get("trace", []) + [part.get("action") or {}]]
    for decoy in d.get("decoys", []):
        out += [(f"decoy {decoy['name']}", st)
                for st in decoy.get("trace", []) + [decoy.get("decoy") or {}]]
    for f in d.get("faults", []):
        out += [(f"{f['fault_id']}.trigger_trace", st) for st in f.get("trigger_trace", [])]
    for i, trace in enumerate(d.get("benign_traces", [])):
        out += [(f"benign_traces[{i}]", st) for st in trace]
    return out


def trace_ids_exist(app: AppManifest) -> list[str]:
    """Every DOM id a trace names must appear literally in the source."""
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in _sources(app))
    problems = []
    for where, step in _trace_steps(app):
        ident = step.get("id")
        if not ident or ident in corpus:
            continue
        # A generated id (`edit-${p.id}`, `f"cancel-{t.id}"`): its fixed prefix must appear
        # in the source; the browser checks prove the generated id actually resolves.
        prefixes = [ident[:i + 1] for i, ch in enumerate(ident) if ch == "-"]
        if not any(prefix in corpus for prefix in prefixes):
            problems.append(f"{where}: id {ident!r} never appears in src/")
    return sorted(set(problems))


def run_all(app: AppManifest) -> dict:
    urls, clocks, vocab, ids = external_urls(app), nondeterminism(app), vocabulary(app), \
        trace_ids_exist(app)
    return {
        "no_external_urls": {"passed": not urls, "detail": urls[:10]},
        "deterministic_source": {"passed": not clocks, "detail": clocks[:10]},
        "vocabulary_complete": {"passed": not vocab["problems"], "detail": vocab["problems"],
                                "summary": {k: v for k, v in vocab.items() if k != "problems"}},
        "trace_ids_exist": {"passed": not ids, "detail": ids[:20]},
    }
