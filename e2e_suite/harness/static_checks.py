"""Checks that need no server and no browser."""

from __future__ import annotations

import re
from pathlib import Path

from .manifest import AppManifest

SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".json", ".svg", ".sql"}
#: Any absolute URL that is not this machine. Comments count too: a URL in a comment is a
#: URL someone may later fetch.
_EXTERNAL_URL = re.compile(r"\b(?:https?|wss?)://(?!127\.0\.0\.1|localhost|0\.0\.0\.0)[\w.-]+",
                           re.IGNORECASE)
_NONDETERMINISM = {
    ".py": re.compile(r"\b(random\.|uuid\.uuid[14]|datetime\.now\(|datetime\.utcnow\(|"
                      r"time\.time\(\)|date\.today\(\)|secrets\.|os\.urandom|ObjectId\()"),
    ".js": re.compile(r"\b(Math\.random|Date\.now\(|new Date\(\s*\)|crypto\.randomUUID|"
                      r"performance\.now|randomBytes|getRandomValues|ObjectId\()"),
}
_NONDETERMINISM[".mjs"] = _NONDETERMINISM[".js"]
#: The database's own clock, randomness and server-side automation (suite v2): anywhere in
#: application code or SQL. Business time comes from the app's clock (WTA_FIXED_NOW), ids
#: from counters, and every write from application code that a fault patch can reach.
_DATABASE_NONDETERMINISM = re.compile(
    r"\b(current_timestamp|clock_timestamp|statement_timestamp|transaction_timestamp|"
    r"gen_random_uuid|uuid_generate_v\w*)\b|\$\$NOW|\$currentDate|\$rand\b|\$sample\b|"
    r"\bCREATE\s+(OR\s+REPLACE\s+)?(TRIGGER|RULE)\b", re.IGNORECASE)
#: Short SQL clock and random calls, which are also ordinary words in code (`now()` may be
#: the app's own clock): flagged in .sql files, and in code only on lines that are SQL.
_SQL_CLOCK = re.compile(r"\b(now|random)\s*\(|\bcurrent_(date|time)\b|\blocaltime(stamp)?\b",
                        re.IGNORECASE)
_SQL_LINE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|VALUES|DEFAULT|WHERE|RETURNING)\b")
#: A database URL written into the source instead of read from the environment.
_DATABASE_URL = re.compile(r"\b(postgres(?:ql)?|mongodb(?:\+srv)?)://", re.IGNORECASE)
_URL_VARIABLE = {"postgres": "DATABASE_URL", "mongodb": "MONGODB_URL"}
_COPIES_VENDOR = re.compile(r"^\s*COPY\s+(?:--\S+\s+)*vendor\b", re.IGNORECASE | re.MULTILINE)
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
        is_sql = path.suffix == ".sql"
        own = _NONDETERMINISM.get(path.suffix)
        if own is None and not is_sql:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            sql_clock = _SQL_CLOCK.search(line) and (is_sql or _SQL_LINE.search(line))
            if ((own and own.search(line)) or _DATABASE_NONDETERMINISM.search(line) or sql_clock) \
                    and "wta-clock" not in line:
                found.append(f"{path.relative_to(app.root)}:{number}: {line.strip()[:100]}")
    return found


def database_url_from_environment(app: AppManifest) -> list[str]:
    """A database app reads its URL from the environment (`DATABASE_URL` / `MONGODB_URL`)
    and never writes one into its source or Dockerfile."""
    found = []
    for path in _sources(app) + [app.root / "Dockerfile"]:
        if path.is_file():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _DATABASE_URL.search(line):
                    found.append(f"{path.relative_to(app.root)}:{number}: {line.strip()[:100]}")
    variable = _URL_VARIABLE.get(app.database)
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in _sources(app))
    if variable and variable not in corpus:
        found.append(f"src/ never reads {variable}")
    return found


def dockerfile_copies_vendor(app: AppManifest) -> list[str]:
    """The image must contain the vendored drivers the build carries (`vendor/<language>`)."""
    if not app.has_dockerfile:
        return []
    text = (app.root / "Dockerfile").read_text(encoding="utf-8")
    return [] if _COPIES_VENDOR.search(text) else ["the Dockerfile does not COPY vendor/"]


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
    out = {
        "no_external_urls": {"passed": not urls, "detail": urls[:10]},
        "deterministic_source": {"passed": not clocks, "detail": clocks[:10]},
        "vocabulary_complete": {"passed": not vocab["problems"], "detail": vocab["problems"],
                                "summary": {k: v for k, v in vocab.items() if k != "problems"}},
        "trace_ids_exist": {"passed": not ids, "detail": ids[:20]},
    }
    if app.database:
        db_urls, vendor = database_url_from_environment(app), dockerfile_copies_vendor(app)
        out["database_url_from_environment"] = {"passed": not db_urls, "detail": db_urls[:10]}
        out["dockerfile_copies_vendor"] = {"passed": not vendor, "detail": vendor}
    return out
