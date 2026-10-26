"""Rules a correct application must obey, checked against what a run actually observed.

A run is a list of records, one per step (`oracle.observed_record`): the page's URL and
HTML, the action that led to it, and any value the action entered. `evaluate(rule,
records, i)` checks one rule at step `i` and returns None when the rule does not apply
there, or an `Outcome` saying whether it held and with what values.

Six kinds, each a behaviour that needs no knowledge of the application's code:

* `arithmetic`  numbers shown on a page satisfy an expression (total = subtotal + tax;
                subtotal = sum of line prices; badge = number of rows)
* `echo`        a value the user entered appears unchanged on a later page
* `sorted`      after choosing a sort option, the list is in that order
* `counter`     an action changes a counter by a fixed amount (add to cart: badge + 1)
* `consistency` two values on one page agree, or a value shown on one page equals a value
                shown later on another (on a list page, the row of the item that was opened)
* `blocked`     submitting a form with a required field empty does not get through

Expressions are parsed, never `eval`ed: only numbers, operand names, arithmetic,
comparisons and `round`/`abs`/`min`/`max` are allowed.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import asdict, dataclass, field

from bs4 import Tag

from .pages import number_in, path_of, select_all, soup_of, template_of, text_of, visible_text

KINDS = ("arithmetic", "echo", "sorted", "counter", "consistency", "blocked")


@dataclass
class Rule:
    id: str
    kind: str
    description: str
    spec: dict
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Outcome:
    holds: bool
    details: dict = field(default_factory=dict)


# -- expressions ------------------------------------------------------------------------------

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Eq: "eq", ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max}


def _check_expression(expression: str) -> ast.Expression:
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
                raise ValueError(f"function not allowed: {ast.dump(node.func)}")
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
                                   ast.Name, ast.Constant, ast.Load, ast.USub, ast.UAdd, ast.And, ast.Or,
                                   *_BIN, *_CMP)):
            raise ValueError(f"not allowed in an expression: {type(node).__name__}")
    return tree


def _run(node, values: dict, tolerance: float):  # noqa: ANN001, ANN202
    if isinstance(node, ast.Expression):
        return _run(node.body, values, tolerance)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        value = _run(node.operand, values, tolerance)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        return _BIN[type(node.op)](_run(node.left, values, tolerance), _run(node.right, values, tolerance))
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*[_run(a, values, tolerance) for a in node.args])
    if isinstance(node, ast.BoolOp):
        results = [_run(v, values, tolerance) for v in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.Compare):
        left = _run(node.left, values, tolerance)
        for op, comparator in zip(node.ops, node.comparators):
            right = _run(comparator, values, tolerance)
            op_fn = _CMP[type(op)]
            ok = abs(left - right) <= tolerance if op_fn == "eq" else op_fn(left, right)
            if not ok:
                return False
            left = right
        return True
    raise ValueError(type(node).__name__)


# -- helpers over records ---------------------------------------------------------------------

def _page_matches(pattern: str | None, url: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, template_of(url)) is not None or re.search(pattern, path_of(url)) is not None
    except re.error:
        return False


def _label(step: dict | None) -> str:
    if not step:
        return ""
    return str(step.get("text") or step.get("id") or step.get("selector") or step.get("label") or "")


def _field_matches(want: str, step: dict | None, entered: dict) -> bool:
    if not want:
        return False
    want_l = want.lower().lstrip("#")
    keys = [k.lower() for k in entered] + [str((step or {}).get(k, "")).lower() for k in ("id", "selector", "text")]
    return any(k and (k == want_l or want_l in k or (len(k) > 2 and k in want_l)) for k in keys)


def _typed(records: list[dict], upto: int) -> list[tuple[int, dict, str]]:
    """(index, step, value) for every value entered before `upto`, newest last."""
    out = []
    for j in range(1, min(upto, len(records))):
        rec = records[j]
        for value in (rec.get("entered") or {}).values():
            out.append((j, rec.get("action") or {}, str(value)))
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w.@+-]", " ", (text or "").lower()).split())


def _numbers(html: str, selector: str) -> list[float]:
    return [n for n in (number_in(text_of(el)) for el in select_all(html, selector)) if n is not None]


def _operand(html: str, spec) -> float | None:  # noqa: ANN001
    if isinstance(spec, str):
        spec = {"selector": spec}
    agg = spec.get("agg", "first")
    if agg == "count":
        return float(len(select_all(html, spec["selector"])))
    values = _numbers(html, spec["selector"])
    if agg == "sum":
        return round(sum(values), 6) if values else None
    return values[0] if values else None


# -- the six kinds ----------------------------------------------------------------------------

def _arithmetic(spec: dict, records: list[dict], i: int) -> Outcome | None:
    rec = records[i]
    if not _page_matches(spec.get("page"), rec["url"]):
        return None
    values = {}
    for name, operand in (spec.get("operands") or {}).items():
        value = _operand(rec["html"], operand)
        if value is None:
            return None
        values[name] = value
    if not values:
        return None
    tree = _check_expression(spec["expression"])
    holds = bool(_run(tree, values, float(spec.get("tolerance", 0.01))))
    return Outcome(holds, {"values": values, "expression": spec["expression"], "page": path_of(rec["url"])})


def _echo(spec: dict, records: list[dict], i: int) -> Outcome | None:
    rec = records[i]
    if i == 0 or not _page_matches(spec.get("page"), rec["url"]):
        return None
    typed = [(j, step, v) for j, step, v in _typed(records, i + 1)
             if _field_matches(spec.get("field", ""), step, records[j].get("entered") or {})]
    if not typed:
        return None
    j, _step, value = typed[-1]
    if len(value.strip()) < 2 or j == i or path_of(records[j]["url"]) == path_of(rec["url"]):
        return None
    if not any((records[k].get("action") or {}).get("type") == "CLICK" for k in range(j + 1, i + 1)):
        return None
    where = " ".join(text_of(el) for el in select_all(rec["html"], spec["selector"])) if spec.get("selector") \
        else visible_text(rec["html"], 20000)
    if spec.get("selector") and not where:
        return None
    holds = _norm(value) in _norm(where)
    return Outcome(holds, {"entered": value, "shown": where[:200], "page": path_of(rec["url"])})


def _sorted(spec: dict, records: list[dict], i: int) -> Outcome | None:
    rec = records[i]
    step = rec.get("action") or {}
    if step.get("type") != "SELECT" or not _page_matches(spec.get("page"), rec["url"]):
        return None
    if not _field_matches(spec.get("control", ""), step, rec.get("entered") or {}):
        return None
    chosen = [str(v) for v in (rec.get("entered") or {}).values()] + [str((step.get("params") or {}).get("option", ""))]
    want = str(spec.get("option", "")).lower()
    if not any(c and c.lower() == want for c in chosen):
        return None
    els = select_all(rec["html"], spec["items"])
    if spec.get("key", "text") == "number":
        keys = [number_in(text_of(e)) for e in els]
        keys = [k for k in keys if k is not None]
    else:
        keys = [text_of(e).lower() for e in els]
    if len(keys) < 2:
        return None
    reverse = spec.get("order", "asc") == "desc"
    holds = keys == sorted(keys, reverse=reverse)
    return Outcome(holds, {"option": want, "order": spec.get("order", "asc"), "items": keys[:12]})


def _counter(spec: dict, records: list[dict], i: int) -> Outcome | None:
    if i == 0:
        return None
    rec, before = records[i], records[i - 1]
    step = rec.get("action") or {}
    if step.get("type") != "CLICK" or not _page_matches(spec.get("page"), before["url"]):
        return None
    try:
        if not re.search(spec["action"], _label(step), re.I):
            return None
    except re.error:
        return None
    a = _numbers(before["html"], spec["counter"])
    b = _numbers(rec["html"], spec["counter"])
    old, new = (a[0] if a else 0.0), (b[0] if b else 0.0)
    holds = abs((new - old) - float(spec.get("delta", 1))) < 1e-9
    return Outcome(holds, {"action": _label(step), "before": old, "after": new, "expected_delta": spec.get("delta", 1)})


def _same(spec: dict, x: str, y: str) -> bool | None:
    if spec.get("compare", "number") == "number":
        p, q = number_in(x), number_in(y)
        return None if p is None or q is None else abs(p - q) <= float(spec.get("tolerance", 0.01))
    return _norm(x) == _norm(y)


def _single(values: list[str]) -> str | None:
    """The one value a list of matches shows, or None when they disagree (ambiguous)."""
    return values[0] if values and all(_norm(v) == _norm(values[0]) for v in values) else None


def _row_of(html: str, elements: list[Tag], clicked: dict | None) -> Tag | None:
    """Of several matches on a list page, the one in the same row as the element clicked to leave it."""
    target = clicked and clicked.get("id") and soup_of(html).find(id=clicked["id"])
    if not target:
        return None
    for ancestor in [target, *target.parents]:
        inside = [e for e in elements if e is ancestor or ancestor in e.parents]
        if len(inside) == 1:
            return inside[0]
        if len(inside) > 1:
            return None
    return None


def _consistency(spec: dict, records: list[dict], i: int) -> Outcome | None:
    a, b = spec.get("a") or {}, spec.get("b") or {}
    rec = records[i]
    if not _page_matches(b.get("page"), rec["url"]):
        return None
    current = [text_of(e) for e in select_all(rec["html"], b.get("selector", ""))]
    if not current:
        return None
    # Both values on this page: compare them here (row by row when both sides list several).
    here = select_all(rec["html"], a.get("selector", "")) if _page_matches(a.get("page"), rec["url"]) else []
    if here:
        mine = [text_of(e) for e in here]
        pairs = list(zip(mine, current)) if len(mine) == len(current) else \
            [(x, y) for x, y in [(_single(mine), _single(current))] if x is not None and y is not None]
        for x, y in pairs:
            holds = _same(spec, x, y)
            if holds is False:
                return Outcome(False, {"a": x[:120], "b": y[:120], "page": path_of(rec["url"])})
        return Outcome(True, {"page": path_of(rec["url"])}) if pairs else None
    now = _single(current)
    if now is None:
        return None
    for j in range(i - 1, -1, -1):
        prev = records[j]
        if path_of(prev["url"]) == path_of(rec["url"]) or not _page_matches(a.get("page"), prev["url"]):
            continue
        found = select_all(prev["html"], a.get("selector", ""))
        if not found:
            continue
        # A list page shows one value per row: take the row of the item that was opened.
        row = found[0] if len(found) == 1 else _row_of(prev["html"], found, records[j + 1].get("action"))
        earlier = text_of(row) if row is not None else _single([text_of(e) for e in found])
        if earlier is None:
            return None
        holds = _same(spec, earlier, now)
        if holds is None:
            return None
        return Outcome(holds, {"earlier": earlier[:120], "earlier_page": path_of(prev["url"]),
                               "now": now[:120], "page": path_of(rec["url"])})
    return None


def _blocked(spec: dict, records: list[dict], i: int) -> Outcome | None:
    if i == 0:
        return None
    rec, before = records[i], records[i - 1]
    step = rec.get("action") or {}
    if step.get("type") != "CLICK" or not _page_matches(spec.get("form_page"), before["url"]):
        return None
    try:
        if not re.search(spec.get("submit", "."), _label(step), re.I):
            return None
    except re.error:
        return None
    start = i - 1
    while start > 0 and path_of(records[start - 1]["url"]) == path_of(before["url"]):
        start -= 1
    filled = [v for j, s, v in _typed(records, i) if j >= start
              and _field_matches(spec.get("field", ""), s, records[j].get("entered") or {})]
    if filled and filled[-1].strip():
        return None
    reached = _page_matches(spec.get("forbidden_page"), rec["url"]) and path_of(rec["url"]) != path_of(before["url"])
    return Outcome(not reached, {"field": spec.get("field"), "reached": path_of(rec["url"])})


_EVALUATORS = {"arithmetic": _arithmetic, "echo": _echo, "sorted": _sorted, "counter": _counter,
               "consistency": _consistency, "blocked": _blocked}


def evaluate(rule: Rule, records: list[dict], i: int) -> Outcome | None:
    """The rule at step `i`: None when it does not apply, otherwise whether it held."""
    try:
        return _EVALUATORS[rule.kind](rule.spec, records, i)
    except (KeyError, ValueError, TypeError, ZeroDivisionError, SyntaxError):
        return None


def violations(rule: Rule, records: list[dict]) -> list[tuple[int, Outcome]]:
    out = []
    for i in range(len(records)):
        result = evaluate(rule, records, i)
        if result is not None and not result.holds:
            out.append((i, result))
    return out


def parse_rules(raw: list[dict]) -> tuple[list[Rule], list[str]]:
    """Rules from an LLM's JSON, with the reason each malformed one was dropped."""
    rules, dropped = [], []
    for n, item in enumerate(raw or []):
        try:
            kind = item["kind"]
            if kind not in KINDS:
                raise ValueError(f"unknown kind {kind!r}")
            spec = {k: v for k, v in item.items() if k not in ("id", "kind", "description", "rationale")}
            if kind == "arithmetic":
                _check_expression(spec["expression"])
                if not spec.get("operands"):
                    raise ValueError("no operands")
            required = {"echo": ["field"], "sorted": ["control", "option", "items"], "counter": ["action", "counter"],
                        "consistency": ["a", "b"], "blocked": ["field", "forbidden_page"]}.get(kind, [])
            missing = [k for k in required if not spec.get(k)]
            if missing:
                raise ValueError(f"missing {missing}")
            rules.append(Rule(id=str(item.get("id") or f"R{n + 1}"), kind=kind,
                              description=str(item.get("description", ""))[:300], spec=spec,
                              rationale=str(item.get("rationale", ""))[:300]))
        except Exception as exc:  # noqa: BLE001 - one bad rule must not sink the rest
            dropped.append(f"rule {n + 1}: {exc}")
    return rules, dropped
