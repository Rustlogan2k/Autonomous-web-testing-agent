"""Probe sets for the contextual diagnostic's frozen-weight evaluation.

Pre-registration §4(a): for every variant, a fixed set of probe states reached by scripted
traces -- every prefix of each fault's witness and of the benign walks, plus detours into
states where the state-dependent labels are useless -- with the ground-truth effect of
every available action. Built once, on the clean build, and committed before any arm is
pretrained or evaluated.

What a probe state stores is exactly what the learned arms' feature extractor reads: the
env `info` of every step from reset to the probe state, and the action taken at each step.
The evaluator rebuilds the features from that history with a fresh extractor, so a probe's
Q-values are a pure function of the weights. Each history is recorded as the first episode
of a fresh env, because some episode-context slots count visits across the whole run and
would otherwise depend on the order the probes were built in.

Effect labels come from execution, not from the generator's intent. From a freshly
replayed prefix, each available action is taken once and the app's own progress counter
(`window.__ctxProgress()`: requirements met × 10 + stage position) is read before and
after: **progress** if it rises, **regress** if it falls, **no-op** otherwise; an action on
a control listed in `window.__ctxDecoys` is **decoy** whatever it did.

    python -m web_testing_agent.ctxbench.probes [--apps ...] [--workers N]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from ..benchmark.suite import EnvSettings, SuiteApp, discover, load_app, make_env, serve_app
from ..envs.types import ActionSpec, ActionType
from ..evaluation.scripted import ScriptedPolicy
from ..experiments.transfer import git_state
from .generate import OUT_ROOT, VARIANTS, _step, traces, variant_id
from .runner import CtxConfig

PROBE_DIR = OUT_ROOT / "probes"
PROBE_VERSION = "ctx-probes-1"
#: The env the arms run in (`runner.CtxConfig`, i.e. the primary's settings). It must be
#: the same: episode-context slot 0 is the step fraction through a `max_steps` episode, so
#: a probe recorded under another episode length would encode states no arm ever saw.
_RUN = CtxConfig()
SETTINGS = EnvSettings(max_steps=_RUN.episode_steps, settle_quiet_ms=_RUN.settle_quiet_ms,
                       capture_screenshots=False)
EFFECTS = ("progress", "no-op", "regress", "decoy")


# -- the scripted walks --------------------------------------------------------------------

def detours(structure: str) -> dict[str, list]:
    """Walks into states where a state-dependent label is useless.

    "Add" once the selection is full and "Edit" on the review are already on the benign
    walk and the CTX-2 witness; these add the two validation gates with invalid input:
    "Save" with an empty return date, and the contact "Save" with a malformed email.
    """
    walk = traces(structure)["benign"][0]
    date = next(i for i, s in enumerate(walk) if s["id"] == "return-date")
    email = next(i for i, s in enumerate(walk) if s["id"] == "contact-email")
    return {
        "detour-date": walk[:date] + [_step("TYPE", "return-date", category="empty_string")],
        "detour-email": walk[:email] + [_step("TYPE", "contact-email", category="type_mismatch")],
    }


def walks(structure: str) -> dict[str, list]:
    base = traces(structure)
    out = {"CTX-1": base["CTX-1"], "CTX-2": base["CTX-2"]}
    out.update({f"benign-{i}": trace for i, trace in enumerate(base["benign"])})
    out.update(detours(structure))
    return out


# -- serialization -------------------------------------------------------------------------

def spec_to_dict(spec: ActionSpec) -> dict:
    return {"index": spec.index, "action_type": spec.action_type.value, "selector": spec.selector,
            "element_id": spec.element_id, "params": spec.params, "description": spec.description}


def spec_from_dict(row: dict) -> ActionSpec:
    return ActionSpec(index=row["index"], action_type=ActionType(row["action_type"]),
                      selector=row["selector"], element_id=row["element_id"],
                      params=dict(row["params"]), description=row["description"])


def _identity(spec: ActionSpec) -> str:
    return json.dumps(spec_to_dict(spec), sort_keys=True)


def info_to_dict(info: dict) -> dict:
    """The parts of an env `info` that `FeatureExtractor.encode` and `.record` read."""
    page = info.get("page") or {}
    return {
        "action_specs": [spec_to_dict(s) for s in info.get("action_specs") or []],
        "state_key": info.get("state_key", ""),
        "page": {"url": page.get("url", ""), "html": page.get("html", "")},
        "newly_revealed": [list(entry) for entry in info.get("newly_revealed") or []],
        "episode_context": np.asarray(info.get("episode_context", []), dtype=np.float64).tolist(),
    }


def info_from_dict(row: dict) -> dict:
    return {
        "action_specs": [spec_from_dict(s) for s in row["action_specs"]],
        "state_key": row["state_key"],
        "page": dict(row["page"]),
        "newly_revealed": [tuple(entry) for entry in row["newly_revealed"]],
        "episode_context": np.asarray(row["episode_context"], dtype=np.float32),
    }


# -- execution -----------------------------------------------------------------------------

class ReplayError(RuntimeError):
    pass


def _page(env):
    return env._browser.page  # noqa: SLF001 - the probe builder reads the app's own ground truth


def _progress(env) -> int | None:
    try:
        value = _page(env).evaluate("() => (window.__ctxProgress ? window.__ctxProgress() : null)")
    except Exception:  # noqa: BLE001 - a page that cannot be read has no progress value
        return None
    return None if value is None else int(value)


def _app_state(env) -> str:
    page = _page(env)
    storage = page.evaluate("() => sessionStorage.getItem('ctx-request') || ''")
    return f"{page.url}\n{storage}"


def _decoys(env) -> set[str]:
    try:
        return set(_page(env).evaluate("() => window.__ctxDecoys || []"))
    except Exception:  # noqa: BLE001
        return set()


def _replay(env, steps: list[dict]) -> tuple[list[dict], list[int], dict]:
    """Reset and take `steps`, resolving each the way the oracle's replays do.

    Returns the infos (reset first) and the action indices taken. A step that does not
    resolve is an error: a probe reached by a different walk than the one named is not
    the probe the generator meant.
    """
    _, info = env.reset()
    infos, taken = [info], []
    for number, step in enumerate(steps):
        index = ScriptedPolicy._match(step, info.get("action_specs") or [])  # noqa: SLF001
        if index is None:
            raise ReplayError(f"step {number} {step} did not resolve at {(info.get('page') or {}).get('url')}")
        _, _, terminated, truncated, info = env.step(index)
        if terminated or truncated:
            raise ReplayError(f"episode ended at step {number} of a {len(steps)}-step prefix")
        infos.append(info)
        taken.append(index)
    return infos, taken, info


def reachable_length(url: str, steps: list[dict]) -> int:
    """How many of `steps` resolve, in order, on the build at `url`.

    A fault's witness can run past what the correct application allows: CTX-2's ends by
    sending a request whose malformed email the clean build refuses to save, so its final
    "Send request" never appears there. The probe states are the clean build's, so a walk
    contributes the prefixes it actually reaches and its truncation is recorded.
    """
    env = make_env(url, SETTINGS)
    try:
        _, info = env.reset()
        for number, step in enumerate(steps):
            index = ScriptedPolicy._match(step, info.get("action_specs") or [])  # noqa: SLF001
            if index is None:
                return number
            _, _, terminated, truncated, info = env.step(index)
            if terminated or truncated:
                return number + 1
        return len(steps)
    finally:
        env.close()


def _probe_state(app: SuiteApp, url: str, trace_name: str, prefix: list[dict],
                 known: set[str] | None = None) -> dict | None:
    """Record one probe state's history and label every action available in it.

    Returns None, without labelling, when the prefix reaches an app state in `known`.
    """
    if len(prefix) + 1 >= SETTINGS.max_steps:
        raise ReplayError(f"a {len(prefix)}-step prefix does not fit a {SETTINGS.max_steps}-step episode")
    env = make_env(url, SETTINGS)
    try:
        infos, taken, last = _replay(env, prefix)
        app_state = _app_state(env)
        if known is not None and app_state in known:
            return None
        before = _progress(env)
        if before is None:
            raise ReplayError(f"no progress counter at {_page(env).url}")
        decoys = _decoys(env)
        specs: list[ActionSpec] = list(last.get("action_specs") or [])
        identities = [_identity(s) for s in specs]

        actions = []
        for spec in specs:
            _, _, replayed = _replay(env, prefix)
            current = [_identity(s) for s in replayed.get("action_specs") or []]
            if current != identities or _app_state(env) != app_state:
                raise ReplayError(f"{trace_name}[:{len(prefix)}] did not replay to the same state")
            _, _, terminated, truncated, after_info = env.step(spec.index)
            after = _progress(env)
            selector_id = (spec.selector or "").removeprefix('[id="').removesuffix('"]')
            if selector_id in decoys:
                effect = "decoy"
            elif after is None or after < before:
                effect = "regress"
            elif after > before:
                effect = "progress"
            else:
                effect = "no-op"
            actions.append({**spec_to_dict(spec), "effect": effect, "progress_after": after,
                            "url_after": (after_info.get("page") or {}).get("url", ""),
                            "episode_ended": bool(terminated or truncated)})
        return {
            "trace": trace_name, "prefix_length": len(prefix), "prefix": prefix,
            "url": (last.get("page") or {}).get("url", ""),
            "app_state": app_state, "progress": before,
            "history": {"infos": [info_to_dict(i) for i in infos], "actions": taken},
            "actions": actions,
        }
    finally:
        env.close()


def variant(app_id: str) -> dict:
    for row in VARIANTS:
        if variant_id(row) == app_id:
            return dict(row)
    raise KeyError(f"{app_id} is not a ctxbench variant")


def build_variant(app_id: str, root: Path = OUT_ROOT) -> dict:
    """Every probe state of one variant, deduplicated by app state (shortest prefix kept)."""
    app = load_app(app_id, root)
    structure = variant(app_id)["structure"]
    started = time.monotonic()
    git = git_state()  # at the start: the code that builds the probes, not whatever is later
    states: list[dict] = []
    truncated: dict[str, dict] = {}
    with serve_app(app, ("clean",)) as urls:
        candidates = []
        for name, trace in walks(structure).items():
            reach = reachable_length(urls["clean"], trace)
            if reach < len(trace):
                truncated[name] = {"length": len(trace), "reachable": reach,
                                   "first_unreachable_step": trace[reach]}
            candidates += [(length, name, trace[:length]) for length in range(reach + 1)]
        # Shortest first, so the kept history for a shared state is the most direct one.
        candidates.sort(key=lambda c: (c[0], c[1]))

        seen_prefixes: set[str] = set()
        known: set[str] = set()
        for _length, name, prefix in candidates:
            key = json.dumps(prefix, sort_keys=True)
            if key in seen_prefixes:  # identical prefixes reach identical states
                continue
            seen_prefixes.add(key)
            state = _probe_state(app, urls["clean"], name, prefix, known)
            if state is None:
                continue
            known.add(state["app_state"])
            states.append(state)
    for number, state in enumerate(states):
        state["probe_id"] = f"{app_id}:{number:03d}"
    return {
        "version": PROBE_VERSION, "app_id": app_id, "build": "clean",
        "variant": variant(app_id),
        "env_settings": SETTINGS.to_dict(),
        "git": git,
        "truncated_walks": truncated,
        "wall_clock_s": round(time.monotonic() - started, 1),
        "states": states,
    }


def summarize(probes: dict) -> dict:
    counts = {effect: 0 for effect in EFFECTS}
    for state in probes["states"]:
        for action in state["actions"]:
            counts[action["effect"]] += 1
    with_progress = sum(1 for s in probes["states"] if any(a["effect"] == "progress" for a in s["actions"]))
    with_decoy = sum(1 for s in probes["states"] if any(a["effect"] == "decoy" for a in s["actions"]))
    return {"app_id": probes["app_id"], "states": len(probes["states"]),
            "states_with_progress_action": with_progress, "states_with_decoy": with_decoy,
            "actions_by_effect": counts, "wall_clock_s": probes["wall_clock_s"]}


def write(probes: dict, out_dir: Path = PROBE_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{probes['app_id']}.json.gz"
    payload = json.dumps(probes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    # mtime=0: the same probes give the same bytes, so the committed hash means something.
    with gzip.GzipFile(path, "wb", mtime=0) as handle:
        handle.write(payload)
    return path


def load(app_id: str, out_dir: Path = PROBE_DIR) -> dict:
    with gzip.open(out_dir / f"{app_id}.json.gz", "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def _build_and_write(app_id: str) -> dict:
    probes = build_variant(app_id)
    path = write(probes)
    summary = summarize(probes)
    summary["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apps", nargs="*", help="variant ids (default: all)")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    app_ids = args.apps or [app.app_id for app in discover(OUT_ROOT)]

    summaries, failures = [], {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_build_and_write, app_id): app_id for app_id in app_ids}
        for future in as_completed(futures):
            app_id = futures[future]
            try:
                summary = future.result()
            except Exception as exc:  # noqa: BLE001 - one variant's failure is reported, not fatal
                failures[app_id] = f"{type(exc).__name__}: {exc}"
                print(f"!! {app_id}: {failures[app_id]}", flush=True)
                continue
            summaries.append(summary)
            print(f"== {app_id}: {summary['states']} states, {summary['actions_by_effect']} "
                  f"({summary['wall_clock_s']} s)", flush=True)

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    index_path = PROBE_DIR / "index.json"
    existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    existing.update({s["app_id"]: s for s in summaries})
    index_path.write_text(json.dumps(dict(sorted(existing.items())), indent=2) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
