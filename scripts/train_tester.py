"""Train the product tester's weights: the learning agent pretrained on every benchmark app.

    python scripts/train_tester.py                      # -> models/tester/tester_all_apps.pt
    python scripts/train_tester.py --steps-per-app 1500 --seed 0

This is the configuration of the transfer experiment's `dqn_pretrained` arm with no
application held out: Double DQN, n-step prioritized replay, dueling action-conditioned
head, count-decayed novelty plus the LLM progress potential as shaping, interleaved across
every application in the suite. `scripts/test_app.py` and the web UI load it, attach a
residual head and keep learning on whatever they are pointed at.

What that means for claims: on a *benchmark* application this model is in-distribution --
it trained there -- so a demo on one says nothing about generalization. The transfer claim
rests on the leave-one-application-out experiment, and the product's held-out evidence is
the Harbor & Pine demo app and the live-site run, neither of which it trained on.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import torch  # noqa: E402

from web_testing_agent.benchmark import EnvSettings, discover, serve_app  # noqa: E402
from web_testing_agent.experiments.transfer import git_state  # noqa: E402
from web_testing_agent.reward.progress import ProgressScorer  # noqa: E402
from web_testing_agent.rl import AgentConfig, QAgent, RewardComposer, RewardConfig, RewardLog  # noqa: E402
from web_testing_agent.rl.train import pretrain  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "models" / "tester" / "tester_all_apps.pt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps-per-app", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--llm-weight", type=float, default=1.0)
    parser.add_argument("--llm-offline", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    torch.set_num_threads(2)

    apps = discover()
    total = args.steps_per_app * len(apps)
    scorer = ProgressScorer(offline=args.llm_offline)
    scorer.preflight()
    log = RewardLog(args.out.with_suffix(".reward_log.jsonl"))
    agent = QAgent(AgentConfig(seed=args.seed, total_steps=total, epsilon_decay_steps=total // 2),
                   rewarder=RewardComposer(RewardConfig(llm_weight=args.llm_weight), scorer, log))
    settings = EnvSettings(max_steps=40, settle_quiet_ms=80)
    started = time.time()

    def progress(app: str, episode: int, row: dict) -> None:
        if episode % 5 == 0:
            print(f"{app:16s} episode {episode:3d} steps {row['steps']:5d} states "
                  f"{row['unique_states']:3d} eps {row['epsilon']:.2f} loss {row['recent_loss']}",
                  flush=True)

    with contextlib.ExitStack() as stack:
        urls = {app.app_id: stack.enter_context(serve_app(app, builds=("buggy",)))["buggy"]
                for app in apps}
        report = pretrain(agent, apps, urls, steps_per_app=args.steps_per_app,
                          settings=settings, on_episode=progress)
    log.close()
    meta = {"apps": [a.app_id for a in apps], "report": report.to_dict(), "git": git_state(),
            "llm": scorer.config(), "wall_clock_s": round(time.time() - started, 1)}
    agent.save(args.out, meta=meta)
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"saved {args.out} ({report.wall_clock_s:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
