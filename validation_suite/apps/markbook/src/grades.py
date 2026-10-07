"""Markbook arithmetic: weighted percentages, levels, provisional and class averages.

Everything is computed exactly with fractions and rounded once, for display, to one
decimal place with halves rounded up (the school's reporting rule). Levels are read off
the rounded figure, so a level never disagrees with the percentage printed beside it.
"""

from __future__ import annotations

import decimal
import math
from fractions import Fraction

TENTH = decimal.Decimal("0.1")
#: (lowest rounded percentage, level), best first; anything below the last is level 1.
LEVELS = ((80, 5), (70, 4), (60, 3), (50, 2))


def component(score: int, assessment: dict) -> Fraction:
    """What one mark contributes to the weighted percentage."""
    return Fraction(score * assessment["weight"], assessment["max_marks"])


def weighted(scores: dict, assessments: list[dict]) -> Fraction | None:
    """The exact weighted percentage, or None until every assessment has a mark.

    The weights add up to 100, so the sum of the components is already a percentage:
    hw*20/max_hw + quiz*20/max_quiz + proj*30/max_proj + exam*30/max_exam.
    """
    total = Fraction(0)
    for assessment in assessments:
        score = scores.get(assessment["id"])
        if score is None:
            return None
        total += component(score, assessment)
    return total


def percent(exact: Fraction) -> decimal.Decimal:
    """A percentage for display: one decimal place, halves rounded up.

    The value is first cut to whole hundredths with integer arithmetic, so the rounding
    sees the true digits (no binary floating point, no repeating decimals); the hundredths
    digit alone decides the tenth.
    """
    hundredths = decimal.Decimal(math.floor(exact * 100)) / 100
    return hundredths.quantize(TENTH, rounding=decimal.ROUND_HALF_UP)


def level(shown: decimal.Decimal | None) -> int | None:
    if shown is None:
        return None
    for lowest, value in LEVELS:
        if shown >= lowest:
            return value
    return 1


def provisional(scores: dict, assessments: list[dict]) -> Fraction | None:
    """The student's percentage over the published work they have a mark for."""
    earned, weight = Fraction(0), 0
    for assessment in assessments:
        score = scores.get(assessment["id"])
        if not assessment["published"] or score is None:
            continue
        earned += component(score, assessment)
        weight += assessment["weight"]
    return earned * 100 / weight if weight else None


def column_average(marks: list[int], maximum: int) -> Fraction | None:
    """The class's mean mark in one assessment, as a percentage of its maximum."""
    if not marks:
        return None
    return Fraction(sum(marks) * 100, len(marks) * maximum)


def mean(values: list[Fraction]) -> Fraction | None:
    return sum(values, Fraction(0)) / len(values) if values else None


def shown(exact: Fraction | None) -> str:
    """How a percentage reads on every page: '67.1%', or '-' when it cannot be worked out."""
    return "-" if exact is None else f"{percent(exact)}%"
