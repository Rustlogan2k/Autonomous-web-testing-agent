"""Pre-compute the LLM progress judgments and label vectors an experiment will ask for.

    python scripts/warm_caches.py --steps 1500            # every application in the suite
    python scripts/warm_caches.py --apps booking_desk --steps 800

Explores each application (both builds) with uniform-random and feature-search policies and
asks the progress scorer about every state reached, which fills `data/llm_cache/`, and
encodes every control label, which fills the label store. Then compacts the judgment
shards into `data/llm_cache/progress.jsonl`.

**Why this is legitimate for held-out applications.** The model is shown page snapshots
and nothing else: no fault, no answer key, no label. Every judgment made here is one an
agent would request online the first time it reached the state; computing it in advance
changes when the model is asked, not what it is asked. What it buys is time -- a dozen
experiment workers otherwise queue behind one GPU -- and the ability to run the experiment
on machines with no model at all (`run_transfer.py --llm-offline`).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from web_testing_agent.benchmark import EnvSettings, discover, make_env, serve_app  # noqa: E402
from web_testing_agent.envs.types import MAX_ACTIONS  # noqa: E402
from web_testing_agent.evaluation.rollout import RandomPolicy  # noqa: E402
from web_testing_agent.reward.progress import ProgressScorer  # noqa: E402
from web_testing_agent.rl import FeatureSearchPolicy  # noqa: E402
from web_testing_agent.rl.features import semantic_label  # noqa: E402
from web_testing_agent.rl.label_store import StoredLabelEncoder  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apps", nargs="*", default=None)
    parser.add_argument("--steps", type=int, default=1500, help="per policy per build")
    args = parser.parse_args()
    scorer = ProgressScorer()
    scorer.preflight()
    labels = StoredLabelEncoder()
    settings = EnvSettings(max_steps=40, settle_quiet_ms=80)
    apps = [a for a in discover() if not args.apps or a.app_id in args.apps]
    started = time.time()
    for app in apps:
        with serve_app(app) as urls:
            for build in ("buggy", "clean"):
                for name, policy in (("random", RandomPolicy(MAX_ACTIONS, seed=1, valid_only=True)),
                                     ("search", FeatureSearchPolicy(seed=1, features=None))):
                    env = make_env(urls[build], settings)
                    seen: set[str] = set()
                    try:
                        observation, info = env.reset()
                        policy.reset()
                        for _ in range(args.steps):
                            page = info.get("page") or {}
                            marker = f"{info.get('state_key')}:{hash(page.get('visible_text', ''))}"
                            if marker not in seen:
                                seen.add(marker)
                                scorer.potential(page.get("url", ""), page.get("html", ""),
                                                 context=app.manifest.name,
                                                 visible_text=page.get("visible_text"))
                                labels.encode_batch([semantic_label(s) for s in info.get("action_specs") or []])
                            before = info
                            observation, _r, terminated, truncated, info = env.step(policy.act(observation, info))
                            learn = getattr(policy, "observe_transition", None)
                            if callable(learn):
                                learn(before, 0, 0.0, info, terminated, truncated)
                            if terminated or truncated:
                                observation, info = env.reset()
                                policy.reset()
                    finally:
                        env.close()
                    print(f"{app.app_id}/{build}/{name}: {len(seen)} distinct snapshots; "
                          f"{scorer.stats.to_dict()}", flush=True)
    target = scorer.cache.compact()
    print(f"compacted {len(scorer.cache)} judgments -> {target}; {len(labels)} label vectors; "
          f"{time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
