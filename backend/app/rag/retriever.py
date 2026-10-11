"""Retrieval over the store's policy, FAQ and product documents.

Documents are Markdown files split into chunks at "##" headings. Ranking uses BM25, which needs no
external service, gives reproducible results in tests and CI, and works well for short policy text.
The `Retriever` interface keeps the door open for an embedding store (pgvector or Chroma).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STOPWORDS = set(
    "a an and are as at be but by can do does for from how i if in is it its me my of on or our so that the "
    "their them there this to was we what when where which who will with you your about any have has get".split()
)

# Light normalisation so "refunds" matches "refund" and "returned" matches "return".
SYNONYMS = {
    "money": "refund", "reimburse": "refund", "cancelation": "cancel", "cancellation": "cancel",
    "deliver": "ship", "delivery": "ship", "shipping": "ship", "shipped": "ship", "parcel": "ship",
    "guarantee": "warranty", "broken": "defect", "defective": "defect", "faulty": "defect",
    "pay": "payment", "card": "payment", "abroad": "international", "overseas": "international",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.append(SYNONYMS.get(w, w))
    return out


@dataclass(frozen=True)
class Chunk:
    doc: str      # file name, e.g. returns-and-refunds.md
    title: str    # document title
    section: str  # "##" heading
    text: str

    @property
    def source(self) -> str:
        return f"{self.doc}#{self.section}"


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def load_chunks(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        title, section, lines = path.stem, "Overview", []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if lines:
                    chunks.append(Chunk(path.name, title, section, " ".join(lines).strip()))
                section, lines = line[3:].strip(), []
            elif line.strip():
                lines.append(line.strip().lstrip("- "))
        if lines:
            chunks.append(Chunk(path.name, title, section, " ".join(lines).strip()))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Section titles are repeated so headings weigh more than body text.
        self.docs = [tokenize(f"{c.title} {c.section} {c.section} {c.text}") for c in chunks]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df: Counter = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    @classmethod
    def from_dir(cls, docs_dir: Path) -> "Retriever":
        return cls(load_chunks(docs_dir))

    def search(self, query: str, k: int = 3, min_score: float = 1.0) -> list[Hit]:
        q = tokenize(query)
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s >= min_score:
                scored.append(Hit(self.chunks[i], round(s, 3)))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]
