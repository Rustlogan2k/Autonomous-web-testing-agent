"""The dynamic LLM reward: a language model's assessment of functional progress, per state.

**What the model is asked, and what it is not.** It is shown a compact text snapshot of the
page the agent has reached and asked one thing: *how far into completing a task is the
user here?* -- on an anchored 0-10 scale running from "landing page, nothing entered" to
"a task has been committed and its outcome is displayed". It is never asked whether the
page is buggy. Bug verdicts are the oracle's job on the benchmark and the judge's job on a
real application; keeping them out of the reward is what stops the agent from being paid
for whatever a language model can be talked into calling a defect.

**Why progress, specifically.** A functional fault becomes *observable* where an
application shows an outcome: a confirmation, a receipt, a saved record, a computed total.
Those states sit at the end of workflows, behind forms and gates, and a novelty bonus alone
values them no higher than a privacy-policy page reached for the first time. Progress is
the quantity that separates the two, it is something a language model can judge from a
page's text on an application it has never seen, and it needs no knowledge of where any
fault is.

**How it becomes a reward** is in `rl.reward`: as a potential. This module only produces
the potential `Phi(s) in [0, 1]`.

**It is dynamic.** Nothing is precomputed per application. A state is scored the first
time any agent reaches it, from its live content -- including what the agent itself typed
into the form -- and an application nobody has seen before is scored the same way as one
in the benchmark.

**It is reproducible as far as a language model allows.** Temperature 0, a fixed seed, a
pinned local model, structured output, and above all a content-addressed cache: a given
snapshot is judged once and the judgment is reused by every later visit, every seed and
every arm. That is also what keeps the potential a *fixed function of the state*, which
the shaping guarantee requires; an uncached model that answered 6 on one visit and 7 on
the next would not be a potential at all.

**It fails safe.** A call that errors, times out or returns something unparseable yields
no judgment, and the reward composer treats a missing potential as "no shaping for this
transition". A broken model can remove the signal; it cannot inject one.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from ..utils.logging import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "progress-v3"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
# 127.0.0.1, not "localhost": on Windows `localhost` resolves to ::1 first and the
# connection falls back to IPv4 only after a timeout. Measured: 2.04 s per request through
# `localhost` against 0.01 s through 127.0.0.1 -- two thirds of every judgment's latency.
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "llm_cache"
#: Overrides the judgment cache location (a mounted volume in a container, for instance).
CACHE_ENV_VAR = "WTA_LLM_CACHE"

STAGES: tuple[str, ...] = (
    "landing_or_information", "browsing", "task_opened", "task_in_progress",
    "ready_to_commit", "outcome_displayed",
)

SYSTEM_PROMPT = """\
You assess how far a user has progressed through a task in a web application, given a \
text snapshot of the page they are on. You are NOT looking for bugs and you do not judge \
whether the application is correct. You only judge progress.

Rate progress with an integer from 0 to 10 using these anchors:

0  = a landing page, a navigation hub, or a purely informational page (help, policy, \
about, contact). Nothing has been entered or chosen.
2  = browsing: a list, catalogue, table or search of items. No specific item has been \
opened for action.
4  = a specific item, record or task form is open, but untouched: its fields are empty \
or at their defaults.
6  = a task is under way: some fields are filled or options chosen but it is not yet \
committed; or the application is asking the user to correct their input.
8  = ready to commit: a review or summary step that shows the user's own entries back to \
them before the final action, or a form whose required fields are all filled.
10 = a task has been committed and its outcome is displayed: a confirmation, receipt, \
reference number, saved record or applied change that reflects what the user did.

Many tasks take several steps: a checkout, a booking, a sign-up or a wizard, often with \
a step indicator ("Step 2 of 4", a numbered list of stages, or a STEP line in the \
snapshot). Judge progress through the WHOLE task, not how complete the current page is: \
a later step is further along than any earlier step, however filled that earlier step \
was. Spread the steps over 4-8 in order: the first step 4-5, middle steps 5-7, the last \
step before the final action 7-8, the review of the whole task 8.

Use the odd numbers when a page sits between two anchors. Judge only from the snapshot. \
A page is not further along merely because it has many links or a lot of text.

Answer with JSON only: {"reason": "<at most 12 words>", "stage": "<one of: \
landing_or_information, browsing, task_opened, task_in_progress, ready_to_commit, \
outcome_displayed>", "progress": <integer 0-10>}"""

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # The reason comes first so the score is conditioned on it, not the reverse.
        "reason": {"type": "string"},
        "stage": {"type": "string", "enum": list(STAGES)},
        "progress": {"type": "integer", "minimum": 0, "maximum": 10},
    },
    "required": ["reason", "stage", "progress"],
}


# -- the page snapshot -------------------------------------------------------------------


class _DigestParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    _HEADINGS = frozenset({"h1", "h2", "h3"})
    _VALUELESS = frozenset({"hidden", "file", "image", "reset"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: list[str] = []
        self.text: list[str] = []
        self.fields: list[str] = []
        self.controls: list[str] = []
        self._skip_depth = 0
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._select: str | None = None
        self._select_value = ""
        self._textarea: str | None = None
        # The element marked as the current step of a multi-step flow (WAI-ARIA
        # `aria-current="step"`), and how deep inside it the parser is.
        self.current_step = ""
        self._step_tag: str | None = None
        self._step_depth = 0
        self._step_buffer: list[str] = []

    @staticmethod
    def _describe(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return "empty"
        if len(value) <= 24:
            return f'"{value}"'
        return f"long text ({len(value)} chars)"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        hidden = attributes.get("data-hidden") == "true"
        if self._step_tag is not None and tag == self._step_tag:
            self._step_depth += 1
        elif (attributes.get("aria-current") == "step" and not hidden
              and self._step_tag is None and not self.current_step):
            self._step_tag, self._step_depth, self._step_buffer = tag, 1, []
        name = attributes.get("name") or attributes.get("id") or tag
        if tag == "title":
            self._capture, self._buffer = "title", []
        elif tag in self._HEADINGS:
            self._capture, self._buffer = "heading", []
        elif tag in ("button", "a") and not hidden:
            self._capture, self._buffer = "control", []
        elif tag == "input" and not hidden:
            kind = (attributes.get("type") or "text").lower()
            if kind in ("checkbox", "radio"):
                state = "on" if "checked" in attributes else "off"
                self.fields.append(f"{name} [{kind}] = {state}")
            elif kind in ("button", "submit"):
                self.controls.append((attributes.get("value") or name).strip()[:60])
            elif kind not in self._VALUELESS:
                self.fields.append(f"{name} [{kind}] = {self._describe(attributes.get('value') or '')}")
        elif tag == "textarea" and not hidden:
            self._textarea, self._buffer = name, []
            self._capture = "textarea"
        elif tag == "select" and not hidden:
            self._select, self._select_value = name, ""
        elif tag == "option" and self._select is not None and "selected" in attributes:
            self._select_value = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if self._step_tag is not None and tag == self._step_tag:
            self._step_depth -= 1
            if self._step_depth == 0:
                self.current_step = " ".join("".join(self._step_buffer).split())[:60]
                self._step_tag = None
        text = " ".join("".join(self._buffer).split())
        if tag == "title" and self._capture == "title":
            self.title = text[:120]
        elif tag in self._HEADINGS and self._capture == "heading":
            if text:
                self.headings.append(text[:80])
        elif tag in ("button", "a") and self._capture == "control":
            if text:
                self.controls.append(text[:60])
        elif tag == "textarea" and self._capture == "textarea":
            self.fields.append(f"{self._textarea} [textarea] = {self._describe(text)}")
            self._textarea = None
        elif tag == "select" and self._select is not None:
            self.fields.append(f"{self._select} [select] = {self._describe(self._select_value)}")
            self._select = None
            return
        else:
            return
        self._capture, self._buffer = None, []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._step_tag is not None:
            self._step_buffer.append(data)
        if self._capture is not None:
            self._buffer.append(data)
        if self._capture != "textarea" and data.strip():
            self.text.append(data)


def _collapse_runs(text: str, limit: int) -> str:
    """Whitespace collapsed, long single-character runs shortened, cut at `limit`.

    The agent can type 2,048 identical characters into a field, and an application that
    echoes them would otherwise fill the whole snapshot with one letter.
    """
    words = []
    for word in text.split():
        if len(word) > 40 and len(set(word)) <= 2:
            word = f"{word[:6]}...({len(word)} chars)"
        words.append(word)
    collapsed = " ".join(words)
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + " ..."


def page_digest(url: str, html: str, visible_text: str | None = None) -> str:
    """The text snapshot a progress judgment is made from, and is cached under.

    Host and port are dropped: a locally served fixture gets a fresh port every run, and
    including it would make every judgment a cache miss. Typed values appear only as
    "empty", a short literal, or a length -- enough to tell a filled form from an empty
    one, without letting two pages that differ in one keystroke cost two model calls.

    `visible_text` is the browser's rendered text. When supplied it replaces the text
    recovered from the markup, which cannot tell a CSS-hidden panel from a shown one.
    """
    parser = _DigestParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a snapshot of malformed markup is still a snapshot
        pass
    parts = urlsplit(url or "")
    location = parts.path.rsplit("/", 1)[-1] or "/"
    if parts.query:
        location += "?" + parts.query[:60]
    if parts.fragment:
        location += "#" + parts.fragment[:40]
    lines = [f"PAGE: {location}", f"TITLE: {parser.title or '(none)'}"]
    if parser.headings:
        lines.append("HEADINGS: " + " | ".join(parser.headings[:6]))
    if parser.current_step:
        lines.append(f"STEP: {parser.current_step} (marked as the current step)")
    body = visible_text if visible_text is not None else " ".join(parser.text)
    lines.append("TEXT: " + _collapse_runs(body, 900))
    if parser.fields:
        lines.append("FORM FIELDS: " + "; ".join(parser.fields[:20]))
    if parser.controls:
        lines.append("CONTROLS: " + " | ".join(dict.fromkeys(parser.controls[:30])))
    return "\n".join(lines)


# -- judgments and their cache --------------------------------------------------------------


@dataclass(slots=True)
class Judgment:
    """One progress assessment. `value` is the potential, in [0, 1]."""

    key: str
    progress: int
    stage: str = ""
    reason: str = ""
    #: Every individual vote when more than one was taken; `progress` is their median.
    votes: list[int] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0
    digest: str = ""
    #: Where this came from on *this* lookup: "cache" or "llm". Never stored.
    source: str = "llm"

    @property
    def value(self) -> float:
        return self.progress / 10.0

    @property
    def spread(self) -> int:
        return (max(self.votes) - min(self.votes)) if self.votes else 0

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("source", None)
        return row


def cache_dir() -> Path:
    override = os.environ.get(CACHE_ENV_VAR)
    return Path(override) if override else _DEFAULT_CACHE


class JudgmentCache:
    """Content-addressed store of progress judgments, shared by every process.

    Same shape as the label store: each process appends to its own shard and reads
    everyone's, so there is nothing to lock. `compact()` merges the shards into one
    deterministic file, which is what gets committed and shipped with an experiment so a
    machine with no language model can replay the exact judgments.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else cache_dir()
        self._rows: dict[str, dict] = {}
        self._loaded = 0
        # Bytes of each file already read, so a refresh reads only what was appended.
        self._offsets: dict[Path, int] = {}
        self.reload()

    def _files(self) -> list[Path]:
        return sorted(self.directory.glob("progress*.jsonl")) if self.directory.is_dir() else []

    def reload(self) -> None:
        """Read every row appended to any file since the last read.

        Incremental: each file is read from where the previous read stopped, and only up
        to its last complete line, so a row another process is half-way through writing
        is picked up whole on a later read. Cheap enough to call on every cache miss,
        which is what lets concurrent workers share judgments while they run: measured
        before this, six workers exploring the same applications spent 25% of the GPU's
        time re-judging pages another worker had already judged.
        """
        for path in self._files():
            start = self._offsets.get(path, 0)
            try:
                with path.open("rb") as handle:
                    handle.seek(start)
                    data = handle.read()
            except OSError as exc:
                logger.debug("judgment cache {} unreadable: {}", path.name, exc)
                continue
            if start and len(data) == 0 and path.stat().st_size < start:
                self._offsets[path] = 0  # replaced (compacted) since the last read
                continue
            complete = data.rfind(b"\n") + 1
            for line in data[:complete].decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    self._rows.setdefault(row["key"], row)
                except (ValueError, KeyError) as exc:
                    logger.debug("judgment cache {}: skipped a bad row: {}", path.name, exc)
            self._offsets[path] = start + complete
        self._loaded = len(self._rows)

    def get(self, key: str, *, refresh: bool = False) -> Judgment | None:
        row = self._rows.get(key)
        if row is None and refresh:
            self.reload()
            row = self._rows.get(key)
        if row is None:
            return None
        return Judgment(source="cache", **{k: v for k, v in row.items()
                                           if k in Judgment.__slots__ and k != "source"})

    def put(self, judgment: Judgment) -> None:
        row = judgment.to_row()
        self._rows[judgment.key] = row
        self.directory.mkdir(parents=True, exist_ok=True)
        shard = self.directory / f"progress.shard.{os.getpid()}.jsonl"
        with shard.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def record_miss(self, key: str, digest: str, context: str) -> None:
        """Log a snapshot an offline run needed and the cache lacked, once per process.

        An offline worker cannot ask the model, but it can say what it would have asked:
        `scripts/warm_caches.py --from-misses` scores these on a machine with the model, so
        the next run finds them cached. Without this, a cold region of the state space
        would stay cold however many times the experiment is repeated.
        """
        seen = getattr(self, "_missed", None)
        if seen is None:
            seen = self._missed = set()
        if key in seen:
            return
        seen.add(key)
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f"misses.{os.getpid()}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "digest": digest, "context": context},
                                    ensure_ascii=False) + "\n")

    def compact(self, name: str = "progress.jsonl") -> Path:
        """Merge every shard into one key-sorted file and remove the merged shards.

        Safe while other processes are still writing: a shard that gained rows after the
        merge read it, or that its writer holds open (Windows refuses the delete), is
        kept, and the next compaction or any reader picks it up.
        """
        self.reload()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        temporary = target.with_suffix(".jsonl.tmp")
        merged = set(self._rows)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for key in sorted(self._rows):
                handle.write(json.dumps(self._rows[key], ensure_ascii=False) + "\n")
        temporary.replace(target)
        for shard in self.directory.glob("progress.shard.*.jsonl"):
            try:
                keys = {json.loads(line)["key"] for line in
                        shard.read_text(encoding="utf-8").splitlines() if line.strip()}
            except (OSError, ValueError, KeyError):
                continue
            if keys <= merged:
                try:
                    shard.unlink(missing_ok=True)
                except OSError:
                    pass
        return target

    def __len__(self) -> int:
        return len(self._rows)


# -- the scorer -------------------------------------------------------------------------------


@dataclass
class ScorerStats:
    lookups: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    failures: int = 0
    offline_misses: int = 0
    llm_seconds: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["llm_seconds"] = round(self.llm_seconds, 2)
        data["hit_rate"] = round(self.cache_hits / self.lookups, 4) if self.lookups else None
        return data


class ProgressScorer:
    """`Phi(s)` from a local language model, cached by content."""

    def __init__(self, *, model: str = DEFAULT_MODEL, host: str | None = None,
                 cache: JudgmentCache | None = None, votes: int = 1, seed: int = 7,
                 timeout_s: float = 120.0, offline: bool = False) -> None:
        self.model = model
        self.host = (host or DEFAULT_HOST).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.cache = cache if cache is not None else JudgmentCache()
        #: With more than one vote the first is greedy and the rest are sampled; the
        #: potential is their median and their spread is recorded as disagreement.
        self.votes = max(1, int(votes))
        self.seed = seed
        self.timeout_s = timeout_s
        #: Never call the model; a snapshot absent from the cache yields no judgment. For
        #: machines with no language model, replaying a shipped cache.
        self.offline = offline
        self.stats = ScorerStats()

    def config(self) -> dict:
        return {"model": self.model, "prompt_version": PROMPT_VERSION, "votes": self.votes,
                "seed": self.seed, "temperature": 0.0 if self.votes == 1 else "0.0 then 0.7",
                "offline": self.offline, "cache_entries": len(self.cache)}

    def key_for(self, digest: str, context: str = "") -> str:
        payload = "\x00".join((PROMPT_VERSION, self.model, str(self.votes), context, digest))
        return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:40]

    def judge_digest(self, digest: str, context: str = "") -> Judgment | None:
        """Score a snapshot that is already a digest (a logged offline miss)."""
        return self._judge(digest, context)

    def potential(self, url: str, html: str, *, context: str = "",
                  visible_text: str | None = None) -> Judgment | None:
        """The judgment for this page, from the cache or the model. None when unavailable.

        `context` is a short description of the application (its landing-page title), so
        the model reads a snapshot knowing what the product is. It is part of the cache
        key: the same snapshot under a different stated purpose is a different question.
        """
        return self._judge(page_digest(url, html, visible_text), context)

    def _judge(self, digest: str, context: str) -> Judgment | None:
        key = self.key_for(digest, context)
        self.stats.lookups += 1
        cached = self.cache.get(key, refresh=True)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        if self.offline:
            self.stats.offline_misses += 1
            self.cache.record_miss(key, digest, context)
            return None

        started = time.monotonic()
        votes: list[int] = []
        first: dict = {}
        for index in range(self.votes):
            answer = self._ask(digest, context, temperature=0.0 if index == 0 else 0.7,
                               seed=self.seed + index)
            if answer is None:
                continue
            votes.append(answer["progress"])
            first = first or answer
        elapsed = time.monotonic() - started
        self.stats.llm_calls += self.votes
        self.stats.llm_seconds += elapsed
        if not votes:
            self.stats.failures += 1
            return None

        judgment = Judgment(
            key=key, progress=int(round(statistics.median(votes))),
            stage=str(first.get("stage", "")), reason=str(first.get("reason", ""))[:240],
            votes=votes if self.votes > 1 else [], model=self.model,
            seconds=round(elapsed, 3), digest=digest)
        self.cache.put(judgment)
        return judgment

    def _ask(self, digest: str, context: str, *, temperature: float, seed: int) -> dict | None:
        user = (f"APPLICATION: {context}\n\n" if context else "") + digest
        request = {
            "model": self.model, "stream": False, "format": RESPONSE_SCHEMA,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": user}],
            "options": {"temperature": temperature, "seed": seed,
                        "num_ctx": 4096, "num_predict": 70},
        }
        body = json.dumps(request).encode("utf-8")
        http_request = urllib.request.Request(  # noqa: S310 - host is operator-configured
            self.host + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            content = ((payload.get("message") or {}).get("content") or "").strip()
            start, end = content.find("{"), content.rfind("}")
            parsed = json.loads(content[start:end + 1])
            progress = int(parsed["progress"])
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("progress judgment failed: {}: {}", type(exc).__name__, exc)
            return None
        if not 0 <= progress <= 10:
            logger.warning("progress judgment out of range: {}", progress)
            return None
        return {"progress": progress, "stage": parsed.get("stage", ""),
                "reason": parsed.get("reason", "")}

    def preflight(self) -> None:
        """Raise if the model cannot be reached. Call before a run that depends on it."""
        if self.offline:
            return
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=10) as response:  # noqa: S310
                tags = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RuntimeError(f"Ollama is not reachable at {self.host}: {exc}") from exc
        names = [m.get("name", "") for m in tags.get("models", [])]
        if self.model not in names:
            raise RuntimeError(f"model {self.model!r} is not installed (have: {names}); "
                               f"run `ollama pull {self.model}`")
