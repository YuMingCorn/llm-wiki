"""Hybrid wiki search: BM25 + graph walk + optional local vectors, fused by RRF.

Every content page is cut into sections (one chunk per ``##`` heading, plus
the text before the first one). Three streams rank pages:

- bm25: keyword match. CJK runs become character bigrams; Latin terms stay
  whole (``bach-bwv1``) and also add their hyphen parts.
- graph: pages next to the best keyword hits, through typed relations,
  body links in both directions and frontmatter ``sources``.
- vector: cosine similarity of local embeddings (``fastembed``), only when
  the optional ``search`` dependency group is installed. Nothing leaves the
  machine; vectors are cached in ``.llm-wiki/search/``.

Results are fused with weighted reciprocal rank fusion (k = 60; the graph
stream counts 0.1), optionally nudged by
page retention (tools/retention.py) when ``--retention`` is given.

Usage:
  uv run python -I tools/search.py "<問題>" [-n 10] [--no-vector] [--json]
  uv run python -I tools/search.py eval [--file tools/search_eval.tsv]
  uv run python -I tools/search.py index        # build the vector cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _limits  # noqa: E402
import _paths  # noqa: E402
import lint  # noqa: E402
import relations  # noqa: E402

RRF_K = 60
# Graph neighbours only supplement direct hits. On tools/search_eval.tsv
# (15 questions, BM25 on full sections) weight 0.1 matched BM25
# alone on recall@5 (12) and top-1 (7) with MRR 0.58 vs 0.59; 0.2 and above
# lowered top-1 to 3-4. The eval only checks known answer pages, not the
# related pages the graph is meant to surface. Re-run `search.py eval`
# (it reports recall, top-1 and MRR) before changing this.
# With local vectors (Qwen3-Embedding-0.6B-Q, same eval): vector alone found
# 15/15 in the top 5 (top-1 8, MRR 0.72); all three fused 14/15 (top-1 9,
# MRR 0.72); the literal whole-question match ("grep" below) 4/15.
# Rephrased questions are only found by vectors.
STREAM_WEIGHT = {"bm25": 1.0, "vector": 1.0, "graph": 0.1}
BM25_K1, BM25_B = 1.2, 0.75
GRAPH_SEEDS = 5
EDGE_WEIGHT = {"relation": 1.0, "link": 0.5, "source": 0.5}
CACHE_DIR = ".llm-wiki/search"
# Model files live in the project, not the system temp folder that fastembed
# uses by default: a temp cleanup would force a silent 1.1 GB re-download.
MODEL_DIR = ".llm-wiki/models/fastembed"
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"
# Query and passage prefixes the models were trained with.
PREFIXES = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "Qwen/Qwen3-Embedding-0.6B": (
        "Instruct: Given a question, retrieve wiki passages that answer it"
        "\nQuery: ", ""),
    "Qwen/Qwen3-Embedding-0.6B-Q": (
        "Instruct: Given a question, retrieve wiki passages that answer it"
        "\nQuery: ", ""),
}
MAX_CHUNK_CHARS = 2000
# Embedding memory grows with batch x tokens^2: the defaults asked onnxruntime
# for 34 GB. Small batches and truncation keep it well under
# the 8 GB cap of _limits.
EMBED_BATCH = 4
EMBED_MAX_TOKENS = 512
EVAL_FILE = "tools/search_eval.tsv"
MRR_DEPTH = 10

_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_LATIN_RE = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]+")
_H2_RE = re.compile(r"^##\s+(.*?)\s*$")
_STOP = frozenset({"the", "a", "an", "of", "and", "or", "to", "in", "is",
                   "for", "on", "with", "by", "as", "at", "be", "it"})
_CJK_STOP = frozenset({"什麼", "怎麼", "為什", "麼是", "如何", "哪些", "是什",
                       "的是", "有哪", "可以", "一個", "這個"})


def _singular(word: str) -> str:
    """Strips an English plural ending (losses→loss, policies→policy)."""
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    return word[:-1]


def tokenize(text: str) -> list[str]:
    """Splits text into search terms (Latin words and CJK bigrams)."""
    text = _URL_RE.sub(" ", _LINK_TARGET_RE.sub("]", text.lower()))
    tokens: list[str] = []
    for word in _LATIN_RE.findall(text):
        if word in _STOP:
            continue
        word = _singular(word)
        tokens.append(word)
        parts = re.split(r"[-.]", word)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p and p not in _STOP)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(b for i in range(len(run) - 1)
                      if (b := run[i:i + 2]) not in _CJK_STOP)
    return tokens


@dataclass(frozen=True)
class Chunk:
    """One section of a page."""

    page: str
    heading: str
    text: str

    @property
    def embed_text(self) -> str:
        """The start of the chunk, short enough for the embedder.

        Only embeddings are truncated; BM25 indexes the whole section.
        """
        return self.text[:MAX_CHUNK_CHARS]

    @property
    def digest(self) -> str:
        """Content hash of the embedded text, used as the vector cache key."""
        return hashlib.sha256(
            f"{self.page}\n{self.heading}\n{self.embed_text}".encode()
        ).hexdigest()


def chunks_of(key: str, page: lint.Page) -> list[Chunk]:
    """Cuts a page body into ``##`` sections, each prefixed by the title (the first also by the aliases)."""
    title = str((page.frontmatter or {}).get("title") or key)
    # schema §4: Chinese names live only in `aliases`, so index them with the title.
    # Only the first chunk: on every chunk they dilute BM25 (eval MRR 0.55 -> 0.50).
    aliases = " ".join(lint._aliases(page.frontmatter or {}) or [])
    out: list[Chunk] = []
    heading, lines = "", []
    fence = False
    for line in page.body.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        match = None if fence else _H2_RE.match(line)
        if match:
            if any(s.strip() for s in lines):
                out.append(Chunk(key, heading, "\n".join(lines)))
            heading, lines = match.group(1), []
        else:
            lines.append(line)
    if any(s.strip() for s in lines):
        out.append(Chunk(key, heading, "\n".join(lines)))
    return [Chunk(c.page, c.heading,
                  f"{title} {aliases}\n{c.heading}\n{c.text}" if i == 0 and aliases
                  else f"{title}\n{c.heading}\n{c.text}")
            for i, c in enumerate(out)]


class BM25:
    """Okapi BM25 over chunks."""

    def __init__(self, docs: list[list[str]]) -> None:
        """Indexes tokenized documents."""
        self.freqs = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg = sum(self.lengths) / max(len(docs), 1)
        df: Counter[str] = Counter()
        for f in self.freqs:
            df.update(f.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5))
                    for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        """Returns one score per document."""
        out = []
        terms = Counter(query)
        for freq, length in zip(self.freqs, self.lengths, strict=True):
            score = 0.0
            norm = BM25_K1 * (1 - BM25_B + BM25_B * length / self.avg)
            for term, qf in terms.items():
                tf = freq.get(term, 0)
                if tf:
                    score += qf * self.idf[term] * tf * (BM25_K1 + 1) / (
                        tf + norm)
            out.append(score)
        return out


def _page_ranking(chunks: list[Chunk], scores: list[float]
                  ) -> list[tuple[str, float, str]]:
    """Collapses chunk scores to (page, best score, best heading)."""
    best: dict[str, tuple[float, str]] = {}
    for chunk, score in zip(chunks, scores, strict=True):
        if score > 0 and score > best.get(chunk.page, (0.0, ""))[0]:
            best[chunk.page] = (score, chunk.heading)
    return sorted(((p, s, h) for p, (s, h) in best.items()),
                  key=lambda x: (-x[1], x[0]))


def graph_edges(wiki: lint.Wiki) -> dict[str, dict[str, float]]:
    """Builds undirected weighted adjacency between content pages."""
    adjacent: dict[str, dict[str, float]] = defaultdict(dict)

    def add(a: str, b: str, kind: str) -> None:
        if a == b or a not in wiki.pages or b not in wiki.pages:
            return
        weight = EDGE_WEIGHT[kind]
        for x, y in ((a, b), (b, a)):
            adjacent[x][y] = max(adjacent[x].get(y, 0.0), weight)

    for edge in relations.all_edges(wiki):
        add(edge.page, edge.target, "relation")
    for key, page in wiki.pages.items():
        for link in page.links:
            if not link.is_image:
                add(key, link.resolved, "link")
        for source in page.sources:
            add(key, source, "source")
    return adjacent


def graph_ranking(adjacent: dict[str, dict[str, float]],
                  seeds: Sequence[str]) -> list[tuple[str, float]]:
    """Ranks pages linked to the seed pages, nearer seeds counting more.

    Seeds linked to other seeds get credit too: leaving them out let weak
    keyword hits with graph credit overtake the strong ones.
    """
    scores: dict[str, float] = defaultdict(float)
    for rank, seed in enumerate(seeds, 1):
        for neighbour, weight in adjacent.get(seed, {}).items():
            scores[neighbour] += weight / rank
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


class VectorIndex:
    """Local embeddings of chunks, cached by content hash."""

    def __init__(self, root: Path, model: str = DEFAULT_MODEL) -> None:
        """Loads fastembed lazily.

        Raises:
            ImportError: The optional ``search`` group is not installed.
        """
        import numpy as np  # noqa: PLC0415  (optional dependency)
        from fastembed import TextEmbedding  # noqa: PLC0415

        self.np = np
        self.model_name = model
        self.model = TextEmbedding(model, threads=_limits.THREADS,
                                   cache_dir=str(root / MODEL_DIR))
        tokenizer = getattr(self.model.model, "tokenizer", None)
        if tokenizer is not None:
            tokenizer.enable_truncation(max_length=EMBED_MAX_TOKENS)
        self.cache = root / CACHE_DIR / (re.sub(r"\W+", "_", model) + ".npz")
        self.query_prefix, self.passage_prefix = PREFIXES.get(model, ("", ""))

    def _load(self) -> dict[str, object]:
        if not self.cache.is_file():
            return {}
        data = self.np.load(self.cache)
        return dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    def vectors(self, chunks: list[Chunk]) -> object:
        """Returns a normalised matrix, one row per chunk, filling the cache."""
        np = self.np
        cached = self._load()
        missing = [c for c in chunks if c.digest not in cached]
        if missing:
            texts = [self.passage_prefix + c.embed_text for c in missing]
            vectors = self.model.embed(texts, batch_size=EMBED_BATCH)
            for chunk, vec in zip(missing, vectors, strict=True):
                cached[chunk.digest] = np.asarray(vec, dtype=np.float32)
            keep = {c.digest for c in chunks}
            keys = sorted(k for k in cached if k in keep)
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache, keys=np.array(keys),
                     vectors=np.stack([cached[k] for k in keys]))
        matrix = np.stack([cached[c.digest] for c in chunks])
        return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def scores(self, matrix: object, query: str) -> list[float]:
        """Cosine similarity of every chunk to the query."""
        vec = self.np.asarray(next(iter(self.model.embed(
            [self.query_prefix + query], batch_size=1))),
            dtype=self.np.float32)
        vec /= self.np.linalg.norm(vec)
        return [float(s) for s in matrix @ vec]


@dataclass
class Hit:
    """One fused search result."""

    page: str
    title: str
    score: float
    heading: str
    ranks: dict[str, int]


class Searcher:
    """Holds the wiki, chunks and indexes for repeated queries."""

    def __init__(self, root: Path, use_vector: bool = True,
                 model: str = DEFAULT_MODEL,
                 prior: Callable[[str], float] | None = None) -> None:
        """Loads pages and builds the keyword and graph indexes."""
        self.wiki = lint.load(root)
        self.chunks = [c for k, p in sorted(self.wiki.pages.items())
                       for c in chunks_of(k, p)]
        self.bm25 = BM25([tokenize(c.text) for c in self.chunks])
        # schema §4: bodies use the English name, so a Chinese alias in the query
        # also searches for its page's title.
        # One alias may live on pages in two domains (lint allows it), so keep all.
        self.alias_titles: dict[str, list[str]] = {}
        for key, page in self.wiki.pages.items():
            fm = page.frontmatter or {}
            for alias in lint._aliases(fm) or []:
                self.alias_titles.setdefault(alias, []).append(
                    str(fm.get("title") or key))
        self.adjacent = graph_edges(self.wiki)
        self.prior = prior
        self.vector: VectorIndex | None = None
        self.matrix = None
        self.vector_error = ""
        if use_vector:
            try:
                self.vector = VectorIndex(root, model)
                self.matrix = self.vector.vectors(self.chunks)
            except ImportError:
                self.vector_error = ("沒有安裝向量搜尋（uv sync --group "
                                     "search），只用 BM25 與圖")
                self.vector = None

    def expand(self, query: str) -> str:
        """Appends the English title of every page whose alias is in the query."""
        rest, extra = query, []
        for alias in sorted(self.alias_titles, key=len, reverse=True):
            if alias in rest:
                rest = rest.replace(alias, " ")
                extra.extend(self.alias_titles[alias])
        return " ".join([query, *extra])

    def streams(self, query: str) -> dict[str, list[tuple[str, str]]]:
        """Returns each stream's ranked (page, best heading) list."""
        out: dict[str, list[tuple[str, str]]] = {}
        terms = tokenize(self.expand(query))
        bm25 = _page_ranking(self.chunks, self.bm25.scores(terms))
        out["bm25"] = [(p, h) for p, _, h in bm25]
        seeds = [p for p, _ in out["bm25"][:GRAPH_SEEDS]]
        if self.vector is not None:
            vec = _page_ranking(self.chunks,
                                self.vector.scores(self.matrix, query))
            out["vector"] = [(p, h) for p, _, h in vec]
            seeds = list(dict.fromkeys(
                seeds + [p for p, _ in out["vector"][:GRAPH_SEEDS]]))
        out["graph"] = [(p, "") for p, _ in
                        graph_ranking(self.adjacent, seeds)]
        return out

    def search(self, query: str, limit: int = 10,
               streams: Sequence[str] | None = None) -> list[Hit]:
        """Fuses the streams with reciprocal rank fusion."""
        ranked = self.streams(query)
        if streams:
            ranked = {k: v for k, v in ranked.items() if k in streams}
        fused: dict[str, float] = defaultdict(float)
        ranks: dict[str, dict[str, int]] = defaultdict(dict)
        headings: dict[str, str] = {}
        for name, items in ranked.items():
            for rank, (page, heading) in enumerate(items, 1):
                fused[page] += STREAM_WEIGHT[name] / (RRF_K + rank)
                ranks[page][name] = rank
                if heading and page not in headings:
                    headings[page] = heading
        if self.prior is not None:
            for page in fused:
                fused[page] *= 0.9 + 0.1 * self.prior(page)
        order = sorted(fused, key=lambda p: (-fused[p], p))[:limit]
        return [Hit(p, str((self.wiki.pages[p].frontmatter or {}).get(
                    "title", "")), round(fused[p], 5), headings.get(p, ""),
                    ranks[p]) for p in order]


def grep_baseline(wiki: lint.Wiki, query: str, limit: int) -> list[str]:
    """Ranks pages by literal hits of the question's space-separated parts.

    A crude floor for comparison, not the /wiki-query workflow (index, topic
    pages, then regexes the agent picks for ``doc.py grep``).
    """
    terms = [t for t in re.split(r"[\s，、？?。]+", query.lower()) if t]
    counts = {}
    for key, page in wiki.pages.items():
        text = page.body.lower()
        hits = sum(text.count(t) for t in terms)
        if hits:
            counts[key] = hits
    return sorted(counts, key=lambda k: (-counts[k], k))[:limit]


def evaluate(root: Path, path: Path, use_vector: bool,
             limit: int = 5) -> dict:
    """Recall@limit of each stream mix against question → expected pages.

    The file is tab-separated: question, then expected pages separated by
    spaces. A question counts as found when any expected page is returned.
    """
    searcher = Searcher(root, use_vector)
    rows = [line.split("\t") for line in
            path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
    mixes = {"grep": None, "bm25": ["bm25"], "bm25+graph": ["bm25", "graph"]}
    if searcher.vector is not None:
        mixes |= {"vector": ["vector"], "all": None}
    found: dict[str, int] = dict.fromkeys(mixes, 0)
    top1: dict[str, int] = dict.fromkeys(mixes, 0)
    reciprocal: dict[str, float] = dict.fromkeys(mixes, 0.0)
    misses: dict[str, list[str]] = defaultdict(list)
    for question, expected in ((r[0], set(r[1].split())) for r in rows):
        for name, streams in mixes.items():
            if name == "grep":
                pages = grep_baseline(searcher.wiki, question, MRR_DEPTH)
            else:
                pages = [h.page for h in searcher.search(
                    question, MRR_DEPTH, streams)]
            first = next((i for i, p in enumerate(pages, 1)
                          if p in expected), None)
            if first is not None:
                reciprocal[name] += 1 / first
                top1[name] += first == 1
            if first is not None and first <= limit:
                found[name] += 1
            else:
                misses[name].append(question)
    n = max(len(rows), 1)
    return {"questions": len(rows), "recall_at": limit, "found": found,
            "top1": top1,
            "mrr": {k: round(v / n, 3) for k, v in reciprocal.items()},
            "misses": dict(misses), "note": searcher.vector_error}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="+",
                    help='問題，或 "eval"、"index" 子指令')
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--no-vector", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--retention", action="store_true",
                    help="nudge results by page retention")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default=EVAL_FILE)
    args = ap.parse_args(argv)
    if args.n < 1:
        ap.error("-n 要大於 0")
    root = _paths.ROOT
    command = args.query[0]
    if command == "eval" and len(args.query) == 1:
        print(json.dumps(evaluate(root, root / args.file, not args.no_vector),
                         ensure_ascii=False, indent=1))
        return 0
    if command == "index" and len(args.query) == 1:
        if args.no_vector:
            ap.error("index 是建向量快取，不能和 --no-vector 一起用")
        searcher = Searcher(root, True, args.model)
        if searcher.vector is None:
            print(searcher.vector_error, file=sys.stderr)
            return 1
        print(f"已建立 {len(searcher.chunks)} 段的向量快取")
        return 0
    prior = None
    if args.retention:
        import retention  # noqa: PLC0415

        prior = retention.Retention(root).score
    searcher = Searcher(root, not args.no_vector, args.model, prior)
    hits = searcher.search(" ".join(args.query), args.n)
    if searcher.vector_error:
        print(searcher.vector_error, file=sys.stderr)
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], ensure_ascii=False,
                         indent=1))
        return 0
    for i, h in enumerate(hits, 1):
        streams = ",".join(f"{k}:{v}" for k, v in sorted(h.ranks.items()))
        section = f"#{h.heading}" if h.heading else ""
        print(f"{i}\t{h.score:.4f}\t{h.page}{section}\t{h.title}\t{streams}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
