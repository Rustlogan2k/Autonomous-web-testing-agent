"""Run a command and record what its process tree costs: CPU seconds and memory.

    python scripts/ci/measure.py --out usage.json -- <command> [args...]

Used the same way around every tool on the CI runners (our agents and WebRLED), so the cost
figures are comparable. The tree is the command's process and all its descendants, which
includes the browser the tool drives and excludes the subject application (it runs in a
Docker container, a child of the Docker daemon, not of the command).

Every `--interval` seconds the sampler walks /proc for the tree and records, per process,
its latest CPU time (utime + stime) and the tree's total resident memory. CPU of a process
that has exited is kept at its last sample, so the total misses at most one interval of each
short-lived process; the getrusage figure of the reaped children is recorded beside it as a
cross-check. Linux only (it reads /proc). The command's exit status is passed through.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path

CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_BYTES = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _children() -> dict[int, list[int]]:
    tree: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fields = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        tree.setdefault(int(fields[1]), []).append(int(entry))
    return tree


def _descendants(root: int) -> list[int]:
    tree, found, stack = _children(), [], [root]
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(tree.get(pid, ()))
    return found


def _sample(pid: int) -> tuple[float, int] | None:
    """(CPU seconds, resident bytes) of one process, or None once it has gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return None
    # After the command name: state is fields[0]; utime, stime are stat fields 14, 15 and
    # rss is field 24 (1-based, counting pid and comm), i.e. indexes 11, 12 and 21 here.
    cpu = (int(fields[11]) + int(fields[12])) / CLOCK_TICKS
    return cpu, int(fields[21]) * PAGE_BYTES


class TreeSampler:
    def __init__(self, root: int, interval: float) -> None:
        self.root, self.interval = root, interval
        self.cpu: dict[int, float] = {}
        self.peak_rss = 0
        self.rss_sum = 0
        self.samples = 0
        self.peak_processes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def sample(self) -> None:
        rss, alive = 0, 0
        for pid in _descendants(self.root):
            measured = _sample(pid)
            if measured is None:
                continue
            cpu, resident = measured
            # CPU time only grows; a pid reused within one run would be undercounted, which
            # within one tool's tree over an hour is negligible.
            self.cpu[pid] = max(cpu, self.cpu.get(pid, 0.0))
            rss += resident
            alive += 1
        self.peak_rss = max(self.peak_rss, rss)
        self.rss_sum += rss
        self.samples += 1
        self.peak_processes = max(self.peak_processes, alive)

    def summary(self) -> dict:
        mib = 1024 * 1024
        return {
            "cpu_s": round(sum(self.cpu.values()), 1),
            "peak_rss_mib": round(self.peak_rss / mib, 1),
            "mean_rss_mib": round(self.rss_sum / max(1, self.samples) / mib, 1),
            "peak_processes": self.peak_processes,
            "samples": self.samples,
            "interval_s": self.interval,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("no command given")
    started = time.monotonic()
    process = subprocess.Popen(command)
    sampler = TreeSampler(process.pid, arguments.interval)
    sampler.start()
    status = process.wait()
    sampler.stop()
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    record = {
        "command": command,
        "exit_status": status,
        "wall_s": round(time.monotonic() - started, 1),
        **sampler.summary(),
        "reaped_children_cpu_s": round(children.ru_utime + children.ru_stime, 1),
        "cpu_count": os.cpu_count(),
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(f"measure: {json.dumps(record)}", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
