"""Turning a control's visible label into a transferable semantic vector.

**The measurement this is built on** (`reports/probe_semantic_vs_hash.md`):

* The current 32-dim character-trigram hash puts cross-vocabulary synonyms **0.24 random
  SDs** above chance — effectively nothing. It cannot see that `Place order` on one
  application and `Submit purchase` on another are the same control, which is precisely
  the mechanism a transferable prior would rely on.
* A frozen `all-MiniLM-L6-v2` embedding puts them **2.86 random SDs** above chance.
* **Neither** separates meaning from spelling *pairwise*: MiniLM scores `Place order`
  against `Cancel order` as highly as it scores genuine synonyms, because sentence
  embeddings encode topic rather than polarity. So a raw pairwise label cosine must never
  become a policy feature. The embedding is a **feature vector for a learned scorer**, and
  polarity is recovered by *projection onto an axis*, not by similarity between labels.

**Why the vector is projected.** 384 dimensions times 100 action slots is 38,400 numbers
per observation, which is not a reasonable observation width. Measured on the probe's own
statistics:

    projection              dim   synonym margin (random SDs)   anchor-axis AUC
    none (full MiniLM)      384              2.86                    1.000
    frozen JL random         64              1.98                    0.979
    PCA on generic vocab     48              3.40                    1.000

PCA is not merely cheaper, it is **better than the full embedding**, and for a known
reason: sentence embeddings carry a large common component shared by all text, which
contributes to every cosine and discriminates nothing. Centring and taking the leading
directions removes it. The cost is that a projection has to be *fitted*, and what it is
fitted on is a generalization decision — see `semantic_vocabulary`, which is frozen,
committed, and contains no label harvested from any benchmark fixture.

**Determinism.** The encoder is pinned by name, run in `eval()` on CPU, never fine-tuned.
The projection is fitted once from a fixed vocabulary in a fixed order by a deterministic
SVD, cached on disk, and verified by checksum. The same label always produces the same
vector, in this process and in any other.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ..utils.logging import get_logger
from .semantic_vocabulary import FIT_VOCABULARY, VOCABULARY_VERSION

logger = get_logger(__name__)

#: The frozen encoder. Pinned; never fine-tuned; no gradient ever flows through it.
SEMANTIC_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

#: Width of the projected label block that reaches the policy. 48 measured at or above
#: the full 384-dim embedding on both statistics that matter (see the module docstring),
#: at a twelfth of the width.
LABEL_DIM = 48

#: Where the fitted projection is cached. Content-addressed by the vocabulary so a change
#: to the word list cannot silently reuse a stale matrix.
_CACHE_DIR = Path(__file__).resolve().parents[3] / "models" / "semantic"


def vocabulary_fingerprint() -> str:
    """Identity of the fitted projection: model, width, and the exact word list."""
    digest = hashlib.sha256()
    digest.update(SEMANTIC_MODEL.encode("utf-8"))
    digest.update(str(LABEL_DIM).encode("utf-8"))
    digest.update(VOCABULARY_VERSION.encode("utf-8"))
    for phrase in FIT_VOCABULARY:
        digest.update(phrase.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


class SemanticLabelEncoder:
    """`label -> LABEL_DIM` semantic vector. Frozen encoder, frozen projection, cached.

    Drop-in for `action_features.LabelEncoder` in shape and contract: `encode(label)`
    returns a unit-norm `float32` vector, and identical labels return identical vectors.
    The difference is what the vector *means* — meaning rather than spelling.

    **Lazy and cached, because the cost profile demands it.** Loading MiniLM takes about a
    minute the first time and the fit takes one forward pass over ~200 phrases; both are
    done once per machine and then read from disk. Per-label encoding is then a dictionary
    lookup for anything already seen, which on a web page is almost everything after the
    first visit.
    """

    def __init__(self, device: str = "cpu", cache_dir: Path | None = None,
                 model_name: str = SEMANTIC_MODEL, dim: int = LABEL_DIM) -> None:
        self.device = device
        self.model_name = model_name
        self.dim = dim
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _CACHE_DIR
        self._encoder = None
        self._mean: np.ndarray | None = None
        self._components: np.ndarray | None = None
        self._vectors: dict[str, np.ndarray] = {}

    # -- the frozen pieces ---------------------------------------------------------

    @property
    def fingerprint(self) -> str:
        return vocabulary_fingerprint()

    def _projection_path(self) -> Path:
        return self._cache_dir / f"label_projection_{self.fingerprint}.npz"

    def _load_encoder(self):  # noqa: ANN202
        if self._encoder is None:
            from ..perception.encoders.models import SentenceTransformerEncoder

            self._encoder = SentenceTransformerEncoder(
                model_name=self.model_name, output_dim=EMBEDDING_DIM, device=self.device)
        return self._encoder

    def _ensure_projection(self) -> None:
        """Load the fitted projection, fitting and caching it on first use."""
        if self._components is not None:
            return

        path = self._projection_path()
        if path.is_file():
            stored = np.load(path)
            self._mean = stored["mean"].astype(np.float32)
            self._components = stored["components"].astype(np.float32)
            logger.info("loaded label projection {} ({} -> {})",
                        path.name, EMBEDDING_DIM, self.dim)
            return

        logger.info("fitting label projection on {} frozen vocabulary phrases",
                    len(FIT_VOCABULARY))
        embedded = self._load_encoder().encode_batch(list(FIT_VOCABULARY))
        mean = embedded.mean(axis=0, keepdims=True)
        centred = embedded - mean
        # `full_matrices=False` gives the economy SVD; the right singular vectors are the
        # principal directions. Deterministic for a fixed input, which is what the
        # fingerprint promises.
        _u, _s, right = np.linalg.svd(centred, full_matrices=False)
        components = right[: self.dim]

        self._mean = mean.astype(np.float32)
        self._components = components.astype(np.float32)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, mean=self._mean, components=self._components,
                 fingerprint=self.fingerprint, model=self.model_name,
                 vocabulary_version=VOCABULARY_VERSION)
        (path.with_suffix(".json")).write_text(json.dumps({
            "fingerprint": self.fingerprint,
            "model": self.model_name,
            "embedding_dim": EMBEDDING_DIM,
            "label_dim": self.dim,
            "vocabulary_version": VOCABULARY_VERSION,
            "vocabulary_size": len(FIT_VOCABULARY),
            "_note": "Fitted once on a frozen generic web-UI vocabulary containing no "
                     "label harvested from any benchmark fixture. Never refit per "
                     "application: a per-application projection would make 'the same "
                     "representation across applications' untrue.",
        }, indent=2), encoding="utf-8")
        logger.info("fitted and cached label projection -> {}", path.name)

    # -- the public contract -------------------------------------------------------

    def encode(self, label: str) -> np.ndarray:
        """One label's unit-norm semantic vector. Cached per distinct string."""
        text = (label or "").strip()
        cached = self._vectors.get(text)
        if cached is not None:
            return cached
        vector = self.encode_batch([text])[0]
        return vector

    def encode_batch(self, labels: list[str]) -> np.ndarray:
        """`(len(labels), dim)`. Batched because the encoder is far faster that way."""
        texts = [(label or "").strip() for label in labels]
        missing = [t for t in dict.fromkeys(texts) if t not in self._vectors]

        if missing:
            self._ensure_projection()
            # An empty label has no meaning to encode, and asking the encoder for one
            # returns the model's bias direction — which would make every unlabelled
            # control look alike *and* look like something. Zero is the honest vector.
            real = [t for t in missing if t]
            if real:
                embedded = self._load_encoder().encode_batch(real)
                projected = (embedded - self._mean) @ self._components.T
                norms = np.linalg.norm(projected, axis=1, keepdims=True)
                projected = projected / np.where(norms == 0, 1.0, norms)
                for text, vector in zip(real, projected.astype(np.float32), strict=True):
                    self._vectors[text] = vector
            for text in missing:
                if not text:
                    self._vectors[text] = np.zeros(self.dim, dtype=np.float32)

        return np.stack([self._vectors[text] for text in texts])

    def warm(self, labels: list[str]) -> None:
        """Pre-encode a known label set, so the first step of a run is not the slow one."""
        self.encode_batch(list(labels))
