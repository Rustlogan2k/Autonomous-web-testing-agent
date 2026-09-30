"""A persistent cache in front of the frozen label encoder.

**Why this exists.** The semantic label vector for `"Reserve this slot"` is a pure function
of the string: a frozen sentence encoder followed by a frozen projection. Computing it
needs a transformer model resident in memory, which costs a few hundred megabytes and a
couple of seconds to load. One process can afford that. A transfer experiment runs a dozen
or more worker processes side by side, each of which would load its own copy in order to
encode the same few hundred strings -- on a 16 GB machine that is the difference between
the run fitting in memory and not.

So the vectors are stored on disk, keyed by the projection's fingerprint and the exact
string. A worker reads the store at start-up and loads the model only if it meets a label
nobody has encoded before. After one warm-up pass over the suite that essentially never
happens, and the workers run with no language model in memory at all.

**Correctness.** A cached vector is the encoder's own output for that string, written once.
The store is namespaced by `vocabulary_fingerprint()`, so a change to the model, the
projection width or the fit vocabulary makes every old entry invisible rather than stale.

**Concurrency.** Each process appends its own new entries to its own shard file and never
rewrites another's, so there is nothing to lock. Readers merge every shard they find.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from ..agents.semantic_labels import LABEL_DIM, SemanticLabelEncoder, vocabulary_fingerprint
from ..utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "models" / "semantic" / "label_store"
#: Overrides the store location, e.g. for a container with a mounted cache volume.
STORE_ENV_VAR = "WTA_LABEL_STORE"


def store_dir() -> Path:
    override = os.environ.get(STORE_ENV_VAR)
    return Path(override) if override else _DEFAULT_DIR


class StoredLabelEncoder:
    """`SemanticLabelEncoder`'s contract, backed by an on-disk store.

    Exposes `encode`, `encode_batch`, `warm`, `fingerprint`, `model_name` and `dim`, which
    is everything the feature extractor and the anchor prior use, so it is a drop-in.
    """

    def __init__(self, directory: Path | None = None, device: str = "cpu") -> None:
        self.dim = LABEL_DIM
        self.fingerprint = vocabulary_fingerprint()
        self._directory = Path(directory) if directory is not None else store_dir()
        self._device = device
        self._inner: SemanticLabelEncoder | None = None
        self._vectors: dict[str, np.ndarray] = {}
        self._unsaved: dict[str, np.ndarray] = {}
        self.model_loads = 0
        self._load_shards()

    @property
    def model_name(self) -> str:
        from ..agents.semantic_labels import SEMANTIC_MODEL

        return SEMANTIC_MODEL

    # -- the store ---------------------------------------------------------------------

    def _shards(self) -> list[Path]:
        if not self._directory.is_dir():
            return []
        return sorted(self._directory.glob(f"labels_{self.fingerprint}.*.jsonl"))

    def _load_shards(self) -> None:
        for shard in self._shards():
            try:
                with shard.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        vector = np.asarray(row["v"], dtype=np.float32)
                        if vector.shape == (self.dim,):
                            self._vectors[row["t"]] = vector
            except (OSError, ValueError) as exc:
                # A shard being appended to by another process can end in a partial line.
                # Everything before it is good, and a label missing from the cache is
                # simply encoded again.
                logger.debug("label store shard {} read stopped early: {}", shard.name, exc)
        if self._vectors:
            logger.info("label store: {} cached label vectors ({})",
                        len(self._vectors), self.fingerprint)

    def flush(self) -> None:
        """Append this process's new entries to its own shard."""
        if not self._unsaved:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        shard = self._directory / f"labels_{self.fingerprint}.{os.getpid()}.jsonl"
        with shard.open("a", encoding="utf-8") as handle:
            for text, vector in self._unsaved.items():
                handle.write(json.dumps(
                    {"t": text, "v": [round(float(x), 6) for x in vector]},
                    ensure_ascii=False) + "\n")
        self._unsaved = {}

    # -- the encoder contract ----------------------------------------------------------

    def _encoder(self) -> SemanticLabelEncoder:
        if self._inner is None:
            logger.info("label store miss: loading the sentence encoder in this process")
            self._inner = SemanticLabelEncoder(device=self._device)
            self.model_loads += 1
        return self._inner

    def encode_batch(self, labels: list[str]) -> np.ndarray:
        texts = [(label or "").strip() for label in labels]
        missing = [t for t in dict.fromkeys(texts) if t not in self._vectors]
        if missing:
            empties = [t for t in missing if not t]
            real = [t for t in missing if t]
            for text in empties:
                self._vectors[text] = np.zeros(self.dim, dtype=np.float32)
            if real:
                encoded = self._encoder().encode_batch(real)
                for text, vector in zip(real, encoded, strict=True):
                    vector = np.asarray(vector, dtype=np.float32)
                    self._vectors[text] = vector
                    self._unsaved[text] = vector
                self.flush()
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack([self._vectors[text] for text in texts])

    def encode(self, label: str) -> np.ndarray:
        return self.encode_batch([label])[0]

    def warm(self, labels: list[str]) -> None:
        self.encode_batch(list(labels))

    def __len__(self) -> int:
        return len(self._vectors)
