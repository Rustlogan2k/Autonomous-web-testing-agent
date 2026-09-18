"""A1 and A2: the two frozen, non-learning label priors, defined exactly once.

**Why this module exists.** `docs/methodology/2026-09-18-a0-a1-a2-frozen.md` froze A1's
word list and A2's anchor phrases in prose, before either had an implementation.
`scripts/probe_anchor_audit.py` then needed A1 to run a control, transcribed the list into
itself, and left a comment saying that when A1 was finally implemented both copies must
collapse into one definition. This is that definition. The audit script, the probes and
the policy arms all import from here, so there is no second place a token can drift.

**What each arm is.** Both sit on top of `HandPriorityPolicy`'s fixed linear score and
neither learns anything:

    score_A1(a) = w . phi(a) + W_LABEL_PRIOR * lexical(label(a))
    score_A2(a) = w . phi(a) + W_LABEL_PRIOR * anchor(label(a))

`w . phi(a)` is bit-identical to A0's, `W_LABEL_PRIOR` is the same 1.2 for both, and the
only thing that varies between the two rows is *how the label is read*. A0 reads it not at
all -- its hashed-label weights are exactly zero -- so A0 -> A1 -> A2 varies label access
with learning held at zero throughout.

**The vocabulary below is copied verbatim from the frozen methodology document and must
not be edited.** Not to add a word that would help on a fixture, and specifically not to
add any of the out-of-list probe's head words: `scripts/probe_out_of_list_vocabulary.py`
exists to measure what happens when the important vocabulary is *absent*, and
`tests/unit/test_label_priors.py` fails if those words ever appear here.

**A provenance defect, recorded where the code is rather than only in a report.** The word
list was frozen on 2026-09-18, 39 days after the deep-flow fixture was committed
(2026-08-10, `3844914`) and after A0's stall had already been traced to that fixture's
`order-4` page -- the freezing document quotes the stall rate itself. `place`, `order` and
`continue` are positives; `cancel`, `back` and `return` are negatives; those are exactly
the labels on `order-4.html` and on the four gates before it. The list is therefore frozen
against *tuning* -- it cannot have been adjusted after a result was seen -- but it is
**not independent of the deep-flow fixture**, so A1/A2 numbers on that fixture are an
upper bound, not clean evidence. See `docs/methodology/2026-09-18-a1-provenance-audit.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)

#: The weight both priors are added at. Fixed in the frozen document to sit between A0's
#: `navigational` (+0.6) and `is_form_control` (+1.5), so a commit-ish label outranks an
#: ordinary link but does not outrank filling a form that has not been filled. Identical
#: for A1 and A2 so the two rows differ in how the label is read, not in how loudly.
W_LABEL_PRIOR = 1.2

# -- A1: the frozen lexical vocabulary ---------------------------------------------------

#: Generic web-UI commit/submit verbs. 21 tokens. Frozen 2026-09-18 in commit 0d104f3.
A1_POSITIVE_TOKENS: frozenset[str] = frozenset({
    "submit", "confirm", "place", "checkout", "purchase", "buy", "pay", "order",
    "continue", "proceed", "next", "finish", "complete", "done",
    "save", "apply", "send", "create", "register", "signup", "subscribe",
})

#: Tokens marking an action as abandoning or reversing a flow. 14 tokens. Present because
#: "Cancel order" contains `order` and would otherwise be promoted by the positive list.
A1_NEGATIVE_TOKENS: frozenset[str] = frozenset({
    "cancel", "abort", "discard", "delete", "remove", "reset", "clear",
    "back", "previous", "return", "logout", "signout", "exit", "close",
})

A1_VOCABULARY_VERSION = "2026-09-18.frozen"

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def tokenize(label: str) -> set[str]:
    """Lowercase, split on non-alphanumerics, drop empties. A1's only text processing.

    No stemming and no lemmatisation, deliberately: A1 is a *word list*, and a word list
    that silently stems is a different and more capable thing than the one that was
    frozen. The consequence is real, and measuring it is the point of the coverage probe --
    `payment` does not match `pay`, and `finalise` does not match `finish`.
    """
    return set(_WORD_SPLIT.split((label or "").lower())) - {""}


def a1_lexical_score(label: str) -> float:
    """A1 as frozen: **an indicator**, +1 if any positive token is present, -1 if any
    negative one is, and the sum when both are.

    The frozen document is explicit that this is a match, not a tally: "`1.0` when the
    action's visible label contains *any* token from the frozen vocabulary ... `0.0`
    otherwise", with the negative list "scored by the same rule", and it works the example
    `Cancel order -> +1.2 - 1.2 = 0`. So `Place order` scores **+1**, not +2, even though
    both `place` and `order` are in the positive list.

    This function is the frozen definition and is what the A1 policy arm scores.
    """
    words = tokenize(label)
    positive = 1.0 if words & A1_POSITIVE_TOKENS else 0.0
    negative = 1.0 if words & A1_NEGATIVE_TOKENS else 0.0
    return positive - negative


def a1_lexical_count_score(label: str) -> float:
    """The **count** variant: +1 per distinct positive token, -1 per distinct negative one.

    Not the frozen definition, and not what any policy arm scores. It exists because
    `scripts/probe_anchor_audit.py` shipped this form -- it transcribed the word list from
    the frozen document but implemented `len(words & POS) - len(words & NEG)` rather than
    the indicator -- and `reports/probe_anchor_audit.json`'s published A1 AUC was computed
    with it. Keeping it here means that number stays reproducible instead of silently
    changing under a corrected import, and it lets the probes report both so the
    discrepancy is visible rather than resolved by fiat.

    The two forms differ only where a label carries more than one token from the same
    list: `Place order` is +1 under the frozen indicator and +2 here.
    """
    words = tokenize(label)
    return float(len(words & A1_POSITIVE_TOKENS) - len(words & A1_NEGATIVE_TOKENS))


# -- A2: the frozen anchor phrases -------------------------------------------------------

#: Generic web-UI phrases describing committing a workflow. Frozen 2026-09-18.
A2_POSITIVE_ANCHORS: tuple[str, ...] = (
    "submit the form", "confirm and place the order", "proceed to the next step",
    "complete the purchase", "save these changes", "create the account",
)

#: Generic phrases describing abandoning a flow or navigating away from it.
A2_NEGATIVE_ANCHORS: tuple[str, ...] = (
    "cancel and discard changes", "go back to the previous page",
    "return to the home page", "log out of the account",
    "read the terms and conditions", "contact customer support",
)

A2_ANCHORS_VERSION = "2026-09-18.frozen"


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def anchor_axis_scores(
    label_vectors: np.ndarray, positive: np.ndarray, negative: np.ndarray
) -> np.ndarray:
    """A2's score: max cosine to any positive anchor minus max cosine to any negative one.

    One definition, used by the policy arm and by every probe, so a probe cannot
    accidentally measure a scorer no arm implements. Rows are L2-normalised here rather
    than assumed normalised upstream.
    """
    labels = _unit(np.atleast_2d(np.asarray(label_vectors, dtype=np.float64)))
    pos = _unit(np.asarray(positive, dtype=np.float64))
    neg = _unit(np.asarray(negative, dtype=np.float64))
    return (labels @ pos.T).max(axis=1) - (labels @ neg.T).max(axis=1)


@dataclass
class LexicalCommitPrior:
    """A1 as a callable: label -> score. Stateless, deterministic, no model loaded."""

    name: str = "A1_lexical"

    def __call__(self, label: str) -> float:
        return a1_lexical_score(label)

    def to_dict(self) -> dict:
        return {
            "prior": self.name,
            "kind": "frozen lexical word list",
            "vocabulary_version": A1_VOCABULARY_VERSION,
            "positive_tokens": sorted(A1_POSITIVE_TOKENS),
            "negative_tokens": sorted(A1_NEGATIVE_TOKENS),
            "learns": False,
        }


class AnchorProjectionPrior:
    """A2 as a callable: label -> anchor-axis score on the frozen 48-D projection.

    The encoder and the projection are the ones the policy representation itself uses
    (`SemanticLabelEncoder`), not a second copy: scoring on a representation no arm
    receives would measure something the project does not implement. Anchor vectors are
    encoded once on construction and label scores are memoised, so the per-step cost on a
    fixture with a few dozen distinct labels is a dict lookup after the first visit.
    """

    def __init__(self, encoder=None) -> None:
        from .semantic_labels import SemanticLabelEncoder

        self.name = "A2_anchor_projection"
        self.encoder = SemanticLabelEncoder() if encoder is None else encoder
        anchors = self.encoder.encode_batch(
            list(A2_POSITIVE_ANCHORS) + list(A2_NEGATIVE_ANCHORS))
        self._positive = anchors[: len(A2_POSITIVE_ANCHORS)]
        self._negative = anchors[len(A2_POSITIVE_ANCHORS):]
        self._cache: dict[str, float] = {}

    def __call__(self, label: str) -> float:
        text = (label or "").strip()
        cached = self._cache.get(text)
        if cached is None:
            vector = self.encoder.encode_batch([text])
            cached = float(anchor_axis_scores(vector, self._positive, self._negative)[0])
            self._cache[text] = cached
        return cached

    def to_dict(self) -> dict:
        # `SemanticLabelEncoder.fingerprint` is a property; a test double may expose a
        # method. Accept either rather than depending on which.
        fingerprint = getattr(self.encoder, "fingerprint", None)
        return {
            "prior": self.name,
            "kind": "frozen sentence-embedding anchor axis on the 48-D projection",
            "anchors_version": A2_ANCHORS_VERSION,
            "positive_anchors": list(A2_POSITIVE_ANCHORS),
            "negative_anchors": list(A2_NEGATIVE_ANCHORS),
            "encoder": getattr(self.encoder, "model_name", None),
            "projection_fingerprint": fingerprint() if callable(fingerprint) else fingerprint,
            "labels_cached": len(self._cache),
            "learns": False,
        }
