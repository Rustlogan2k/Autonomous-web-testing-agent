"""The testing report a person reads: Markdown for the repository, HTML to open or send.

Three kinds of result are kept visibly apart, because they carry different weight:

1. **Verified findings** -- reproduced in independent fresh sessions and minimized. Each has
   a step-by-step reproduction and a screenshot captured by replaying that reproduction.
2. **Unverified candidates** -- something fired once and did not reproduce, or depends on
   timing. Listed so nothing is hidden, labelled so nothing is overclaimed.
3. **Qualitative observations** -- a language model's reading of outcome pages, kept only
   when its quoted evidence is on the page. Not verified, never counted.

The report also states what the run could not do (no language model, no pretrained
weights), because a clean report from a degraded run is otherwise indistinguishable from a
clean report from a full one.
"""

from __future__ import annotations

import html
from pathlib import Path

from .review import describe_action
from .session import SessionResult, embed_png

_KIND_TITLES = {
    "console_error": "Uncaught script error",
    "http_error": "Failed request",
    "document_http_error": "Page failed to load",
    "broken_navigation": "Navigation control does nothing",
    "slow_response": "Page never settles",
}


#: Most specific first; a grouped finding takes the title of its highest-ranked signal.
_PRIORITY = ("document_http_error", "http_error", "broken_navigation", "console_error",
             "slow_response")


def _steps(trace: list[dict]) -> list[str]:
    return [describe_action(step) for step in trace if step]


def verified_findings(result: SessionResult) -> list[dict]:
    """Verified findings, with signals that share one root cause reported once.

    A link to a missing page fires three triggers at once -- the failed document request,
    the console's resource error and the navigation that went nowhere -- and each verifies
    independently. Listing them as three bugs triples the count for one defect, which was
    measured on the demo application. Signals whose minimized reproductions are identical
    and that surface on the same page are one finding, reported with all of its signals.
    """
    import json

    items = []
    groups: dict[tuple[str, str], dict] = {}
    for number, finding in enumerate(result.triggers.get("findings", [])):
        if not finding.get("verified"):
            continue
        key = (finding["page"], json.dumps(finding["minimized_trace"], sort_keys=True))
        signal = f"{_KIND_TITLES.get(finding['kind'], finding['kind'])}: {finding['detail']}"
        group = groups.get(key)
        if group is not None:
            group["signals"].append(signal)
            # The group is named after its most specific signal: a page that failed to load
            # explains the console error it logged, not the other way round.
            if _PRIORITY.index(finding["kind"]) < _PRIORITY.index(group["kind"]):
                group["kind"] = finding["kind"]
                group["title"] = _KIND_TITLES.get(finding["kind"], finding["kind"])
            group["first_seen_at"] = min(group["first_seen_at"], finding["first_seen_at"])
            group["occurrences"] += finding["occurrences"]
            continue
        group = {
            "key": f"trigger_{number}", "title": _KIND_TITLES.get(finding["kind"], finding["kind"]),
            "kind": finding["kind"],
            "where": finding["page"], "detail": finding["detail"], "signals": [signal],
            "source": "deterministic trigger", "first_seen_at": finding["first_seen_at"],
            "occurrences": finding["occurrences"],
            "reproduction": finding["minimized_trace"],
            "original_length": finding["minimization"].get("original_length"),
        }
        groups[key] = group
        items.append(group)
    for group in items:
        if len(group["signals"]) > 1:
            group["detail"] = "; ".join(group["signals"])
    for fault in (result.seeded or {}).get("faults", []):
        found = fault.get("finding")
        if found and found.get("confirmed"):
            items.append({
                "key": f"seeded_{fault['fault_id']}", "title": f"Seeded fault {fault['fault_id']}",
                "where": (found.get("candidate_verdict") or {}).get("results", [{}])[0].get("step_url", ""),
                "detail": "; ".join(r.get("detail", "") for r in
                                    (found.get("candidate_verdict") or {}).get("results", [])),
                "source": ("seeded-fault oracle; control: "
                           + (f"passed against the {(found.get('control') or {}).get('against')}"
                              if (found.get("control") or {}).get("checked")
                              else "none available (no clean build to compare against)")),
                "first_seen_at": fault["verified_at"], "occurrences": fault["candidates"],
                "reproduction": found["minimized_trace"],
                "original_length": (found.get("minimization") or {}).get("original_length"),
                "signals": [],
            })
    for number, finding in enumerate((result.spec or {}).get("findings", [])):
        if not finding.get("verified"):
            continue
        judgement = finding.get("judgement") or {}
        items.append({
            "key": f"spec_{number}", "title": judgement.get("title") or finding["description"],
            "where": finding["page"],
            "detail": f"{finding['description']} Observed: {finding['details']}",
            "source": (f"LLM-inferred rule {finding['rule_id']} ({finding['kind']}); judged a bug "
                       f"(confidence {judgement.get('confidence', '?')}): {judgement.get('explanation', '')}"),
            "first_seen_at": finding["first_seen_at"], "occurrences": 1,
            "reproduction": finding["minimized_trace"],
            "original_length": (finding.get("minimization") or {}).get("original_length"),
            "signals": [],
        })
    return items


def _spec_sections(result: SessionResult) -> tuple[list[dict], list[dict], dict]:
    spec = result.spec or {}
    others = [f for f in spec.get("findings", []) if not f.get("verified")]
    return spec.get("rules", []), others, spec


def _agent_label(result) -> str:  # noqa: ANN001
    """The agent's name and, when known, how it was configured."""
    name, mode = result.agent.get("agent"), result.agent.get("mode")
    return f"{name} ({mode})" if name and mode else str(name or mode or "unknown")


def render_markdown(result: SessionResult) -> str:
    hunt = result.hunt
    verified = verified_findings(result)
    candidates = [f for f in result.triggers.get("findings", []) if not f.get("verified")]
    concerns = [r for r in result.reviews if r.get("concern")]
    lines = [
        f"# Test report: {result.app_name}",
        "",
        f"- Target: {result.target}",
        f"- Started: {result.started_at}; wall clock {result.wall_clock_s:.0f} s",
        f"- Agent: {_agent_label(result)}"
        + (f" (weights: `{Path(result.agent['checkpoint']).name}`)" if result.agent.get("checkpoint") else ""),
        f"- Interactions: {hunt.get('steps', 0)} in {hunt.get('episodes', 0)} episodes; "
        f"{hunt.get('unique_states', 0)} distinct page states reached",
        "",
        "## Summary",
        "",
        f"- **{len(verified)} verified finding(s)**: reproduced in fresh sessions and minimized",
        f"- {len(candidates)} unverified candidate(s)",
        f"- {len(concerns)} qualitative observation(s) from the LLM review (not verified)",
    ]
    if result.degradations:
        lines += ["", "**This run was degraded:**", ""] + [f"- {d}" for d in result.degradations]

    lines += ["", "## Verified findings", ""]
    if not verified:
        lines.append("None. No trigger or seeded fault reproduced within the budget.")
    for number, item in enumerate(verified, 1):
        lines += [f"### {number}. {item['title']}", "",
                  f"- Where: `{item['where']}`", f"- Detail: {item['detail']}",
                  f"- Source: {item['source']}",
                  f"- First observed after {item['first_seen_at']} interactions; "
                  f"seen {item['occurrences']} time(s)",
                  f"- Reproduction ({len(item['reproduction'])} steps, minimized from "
                  f"{item['original_length']}):", ""]
        lines += [f"  {i}. {s}" for i, s in enumerate(_steps(item["reproduction"]), 1)]
        shot = (result.evidence.get(item["key"]) or {}).get("screenshot")
        if shot:
            lines += ["", f"  Evidence: `{shot}`"]
        lines.append("")

    lines += ["## Unverified candidates", ""]
    if not candidates:
        lines.append("None.")
    for finding in candidates:
        lines.append(f"- {_KIND_TITLES.get(finding['kind'], finding['kind'])} on `{finding['page']}`: "
                     f"{finding['detail']} — {finding['status'].replace('_', ' ')} "
                     f"(replays: {finding['replays']})")

    lines += ["", "## Qualitative observations (LLM review of outcome pages; not verified)", ""]
    if not result.reviews:
        lines.append("No outcome pages were reviewed.")
    for review in result.reviews:
        mark = "concern" if review.get("concern") else "no concern"
        lines.append(f"- `{review['url'].split('/', 3)[-1]}` — **{mark}**: {review['observation']}"
                     + (f' (quote: "{review["evidence"]}")' if review.get("evidence") else "")
                     + ("" if review.get("grounded") or not review.get("evidence")
                        else " — discarded: quote not found on the page"))

    rules, others, spec = _spec_sections(result)
    if result.spec is not None:
        lines += ["", "## Silent-bug finder (LLM-inferred rules)", ""]
        if not rules:
            lines.append("No rules were written" + (f": {spec['dropped_rules'][0]}" if spec.get("dropped_rules") else "."))
        for rule in rules:
            lines.append(f"- **{rule['id']}** ({rule['kind']}): {rule['description']} "
                         f"(checked {rule.get('applied', 0)} times, violated {rule.get('violated', 0)})")
        if spec.get("intent_files"):
            lines.append(f"- Intended behaviour read from: {', '.join(spec['intent_files'])}")
        for finding in others:
            judgement = finding.get("judgement") or {}
            reason = judgement.get("explanation", "")
            if finding["status"] == "needs_review":
                lines.append(f"- **Needs your review:** {judgement.get('title') or finding['description']} "
                             f"on `{finding['page']}`. The judge called it intended but could not quote evidence. "
                             f"Reproduction: {', '.join(_steps(finding.get('minimized_trace') or []))}")
                continue
            lines.append(f"- Not reported: rule {finding['rule_id']} on `{finding['page']}`, "
                         f"{finding['status'].replace('_', ' ')}" + (f": {reason}" if reason else "")
                         + (f' (quote: "{judgement["evidence_quote"]}")' if judgement.get("evidence_quote") else ""))

    agent = result.agent.get("summary") or {}
    reward = agent.get("reward") or {}
    lines += ["", "## How the agent explored", "",
              f"- Action mix: {hunt.get('action_types', {})}",
              f"- State-changing actions: {hunt.get('state_changing_actions', 0)} of {hunt.get('steps', 0)}",
              f"- Coverage curve (interactions, distinct states): {hunt.get('coverage_curve', [])}"]
    if reward:
        scorer = reward.get("scorer") or {}
        lines += [f"- LLM progress reward: {reward.get('shaped_steps', 0)} shaped steps, total "
                  f"{reward.get('total_shaping', 0)}; {scorer.get('llm_calls', 0)} model calls, "
                  f"cache hit rate {scorer.get('hit_rate')}"]
    return "\n".join(lines) + "\n"


def render_html(result: SessionResult, out_dir: Path) -> str:
    """A standalone page: styles inline, screenshots embedded, opens from disk anywhere."""
    body = []
    esc = html.escape
    verified = verified_findings(result)
    hunt = result.hunt
    candidates = [f for f in result.triggers.get("findings", []) if not f.get("verified")]
    body.append(f"<h1>Test report: {esc(result.app_name)}</h1>")
    body.append(f"<p class=meta>{esc(result.target)} · {esc(result.started_at)} · "
                f"{hunt.get('steps', 0)} interactions · {hunt.get('unique_states', 0)} states · "
                f"agent: {esc(_agent_label(result))}</p>")
    body.append("<div class=tiles>"
                f"<div><b>{len(verified)}</b>verified findings</div>"
                f"<div><b>{len(candidates)}</b>unverified candidates</div>"
                f"<div><b>{sum(1 for r in result.reviews if r.get('concern'))}</b>LLM observations</div>"
                "</div>")
    if result.degradations:
        body.append("<div class=warn><b>Degraded run</b><ul>"
                    + "".join(f"<li>{esc(d)}</li>" for d in result.degradations) + "</ul></div>")
    body.append("<h2>Verified findings</h2>")
    if not verified:
        body.append("<p>None within the budget.</p>")
    for number, item in enumerate(verified, 1):
        steps = "".join(f"<li>{esc(s)}</li>" for s in _steps(item["reproduction"]))
        shot = (result.evidence.get(item["key"]) or {}).get("screenshot")
        image = (f'<img alt="state after the reproduction" src="{embed_png(Path(out_dir) / shot)}">'
                 if shot and (Path(out_dir) / shot).is_file() else "")
        body.append(f"<section class=finding><h3>{number}. {esc(item['title'])}</h3>"
                    f"<p><code>{esc(item['where'])}</code> — {esc(item['detail'])}</p>"
                    f"<p class=meta>{esc(item['source'])}; first observed after "
                    f"{item['first_seen_at']} interactions</p>"
                    f"<p>Reproduction ({len(item['reproduction'])} steps, minimized from "
                    f"{item['original_length']}):</p><ol>{steps}</ol>{image}</section>")
    body.append("<h2>Unverified candidates</h2>")
    body.append("<ul>" + ("".join(
        f"<li>{esc(_KIND_TITLES.get(f['kind'], f['kind']))} on <code>{esc(f['page'])}</code>: "
        f"{esc(f['detail'])} — {esc(f['status'].replace('_', ' '))}</li>" for f in candidates)
        or "<li>None.</li>") + "</ul>")
    body.append("<h2>Qualitative observations <small>(LLM review; not verified)</small></h2><ul>")
    for review in result.reviews:
        body.append(f"<li><b>{'concern' if review.get('concern') else 'no concern'}</b> "
                    f"<code>{esc(review['url'].split('/', 3)[-1])}</code>: "
                    f"{esc(review['observation'])}"
                    + (f" <q>{esc(review['evidence'])}</q>" if review.get("evidence") else "")
                    + "</li>")
    if not result.reviews:
        body.append("<li>No outcome pages were reviewed.</li>")
    body.append("</ul>")
    rules, others, spec = _spec_sections(result)
    if result.spec is not None:
        body.append("<h2>Silent-bug finder <small>(LLM-inferred rules)</small></h2><ul>")
        for rule in rules:
            body.append(f"<li><b>{esc(rule['id'])}</b> ({esc(rule['kind'])}): {esc(rule['description'])} "
                        f"<span class=meta>checked {rule.get('applied', 0)}, violated {rule.get('violated', 0)}</span></li>")
        if not rules:
            body.append("<li>No rules were written.</li>")
        for finding in others:
            judgement = finding.get("judgement") or {}
            reason = judgement.get("explanation", "")
            if finding["status"] == "needs_review":
                body.append(f"<li><b>Needs your review:</b> {esc(judgement.get('title') or finding['description'])} "
                            f"on <code>{esc(finding['page'])}</code>: judged intended without quotable evidence</li>")
                continue
            body.append(f"<li>Not reported: rule {esc(finding['rule_id'])} on <code>{esc(finding['page'])}</code>, "
                        f"{esc(finding['status'].replace('_', ' '))}{(': ' + esc(reason)) if reason else ''}</li>")
        body.append("</ul>")
    style = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#e3e0d8;--accent:#1f5f8b;--warn:#8a4b08;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#7cb4dc;--warn:#e0a458;--card:#1f1f1c}}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:1.6rem;margin:0 0 4px}h2{margin-top:2rem;border-bottom:1px solid var(--line);padding-bottom:4px}
.meta{color:var(--muted);font-size:.9rem}.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.tiles div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:150px;color:var(--muted)}
.tiles b{display:block;font-size:1.6rem;color:var(--fg)}.warn{border-left:4px solid var(--warn);padding:4px 12px;background:var(--card)}
.finding{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 16px 12px;margin:12px 0}
.finding img{max-width:100%;border:1px solid var(--line);border-radius:4px}code{font-size:.9em}"""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Test report</title><style>{style}</style></head><body>"
            + "".join(body) + "</body></html>")


def write_report(result: SessionResult, out_dir: Path) -> dict[str, Path]:
    from .session import save_result

    out_dir = Path(out_dir)
    paths = {"json": save_result(result, out_dir)}
    paths["markdown"] = out_dir / "report.md"
    paths["markdown"].write_text(render_markdown(result), encoding="utf-8")
    paths["html"] = out_dir / "report.html"
    paths["html"].write_text(render_html(result, out_dir), encoding="utf-8")
    return paths
