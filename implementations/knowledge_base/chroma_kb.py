"""Chroma vector DB implementation of KnowledgeBaseInterface.

Loads runbook .md/.txt files from a directory, embeds them using Chroma's
default embedding function (all-MiniLM-L6-v2 via sentence-transformers), and
persists the collection to disk. Queries use semantic similarity rather than
keyword overlap, giving better recall on incident descriptions that use
different phrasing than the runbook text.

This replaces FileKnowledgeBase in production deployments. FileKnowledgeBase
remains the reference implementation for unit tests and environments without
GPU / internet access.

ARI-60
"""

import logging
import re
from pathlib import Path

from core.exceptions import KnowledgeBaseError
from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.models import LogAccessHint, PlatformTag

logger = logging.getLogger(__name__)

_LOG_PATH_RE = re.compile(r"/[\w./-]*\.log[\w/.-]*|/var/log/[\w/.-]+")
_KEYWORD_RE = re.compile(
    r"\b(ERROR|WARN|FATAL|OOM|OutOfMemory|disk full|timeout|"
    r"connection refused|HDFS|YARN|Hive|Spark|Oozie|safe mode)\b",
    re.IGNORECASE,
)

_TOP_K_HINTS = 5
_TOP_K_LOG = 3


class ChromaKnowledgeBase(KnowledgeBaseInterface):
    """KnowledgeBaseInterface backed by a Chroma vector DB collection.

    Documents are loaded from ``runbook_dir`` at construction time and stored
    in a Chroma collection. Each call to ``get_service_hints`` or
    ``get_log_hints`` performs a semantic nearest-neighbour query.

    Args:
        runbook_dir: Directory containing .md / .txt runbook files.
        persist_directory: Path to persist the Chroma DB to disk. If None,
            an ephemeral (in-memory) collection is used.
        collection_name: Name of the Chroma collection. Override when running
            multiple ARIA instances against the same persist_directory.
        n_results: Number of nearest neighbours to retrieve per query.
    """

    def __init__(
        self,
        runbook_dir: str,
        persist_directory: str | None = None,
        collection_name: str = "aria_runbooks",
        n_results: int = _TOP_K_HINTS,
    ) -> None:
        try:
            import chromadb  # noqa: PLC0415
        except ImportError as exc:
            raise KnowledgeBaseError(
                "chromadb is not installed — run: pip install chromadb"
            ) from exc

        self._n_results = n_results
        runbook_path = Path(runbook_dir)
        if not runbook_path.is_dir():
            raise KnowledgeBaseError(f"Runbook directory not found: {runbook_dir}")

        try:
            if persist_directory:
                client = chromadb.PersistentClient(path=persist_directory)
            else:
                client = chromadb.EphemeralClient()

            self._collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise KnowledgeBaseError(f"Failed to initialise Chroma client: {exc}") from exc

        self._load_runbooks(runbook_path)
        logger.debug(
            "ChromaKnowledgeBase: %d documents in collection '%s'",
            self._collection.count(),
            collection_name,
        )

    # ── KnowledgeBaseInterface ────────────────────────────────────────────────

    def get_service_hints(self, cluster: str, description: str) -> list[str]:
        """Return candidate service names ordered by semantic similarity to the cluster and description.

        Performs a nearest-neighbour query against the Chroma collection using the
        cluster name and incident description as the query text. Documents with a
        cosine distance > 0.9 are filtered out as irrelevant.

        Args:
            cluster: Cluster CI name (e.g. 'cdp-prod-cluster-01').
            description: Raw incident description text.

        Returns:
            List of service/component names from the matched runbook file stems.
            Empty list if the collection is empty or all results are too distant.
        """
        if self._collection.count() == 0:
            return []
        try:
            results = self._collection.query(
                query_texts=[f"{cluster} {description}"],
                n_results=min(self._n_results, self._collection.count()),
                include=["metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning("ChromaKnowledgeBase.get_service_hints failed: %s", exc)
            return []

        names: list[str] = []
        for meta, dist in zip(
            (results.get("metadatas") or [[]])[0],
            (results.get("distances") or [[]])[0],
        ):
            if dist > 0.9:  # cosine distance threshold — skip irrelevant docs
                continue
            name = meta.get("name", "")
            if name:
                names.append(name.replace("_", "-"))  # type: ignore[union-attr]
        return names

    def get_log_hints(self, service: str, platform_tag: PlatformTag) -> LogAccessHint:
        """Return log access guidance extracted from the most semantically relevant runbook docs.

        Queries the Chroma collection using the service name and platform as the query text.
        Only documents with cosine distance <= 0.8 contribute to path/keyword extraction —
        documents beyond that threshold are too dissimilar to be reliable.

        Args:
            service: Resolved service or node name (e.g. 'hive-metastore').
            platform_tag: Platform the service runs on (used to narrow the query text).

        Returns:
            LogAccessHint with extracted log paths and keywords from relevant runbooks.
            confidence is derived from the nearest document's similarity score.
            Returns empty hint if the collection is empty or a query error occurs.
        """
        if self._collection.count() == 0:
            return LogAccessHint(
                platform_tag=platform_tag, log_paths=[], keywords=[], confidence=0.0
            )
        try:
            results = self._collection.query(
                query_texts=[f"{service} {platform_tag.value}"],
                n_results=min(_TOP_K_LOG, self._collection.count()),
                include=["documents", "distances"],
            )
        except Exception as exc:
            logger.warning("ChromaKnowledgeBase.get_log_hints failed: %s", exc)
            return LogAccessHint(
                platform_tag=platform_tag, log_paths=[], keywords=[], confidence=0.0
            )

        docs = (results.get("documents") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        # Weight text extraction by similarity — only use close matches
        relevant = [d for d, dist in zip(docs, distances) if dist <= 0.8]
        combined = " ".join(relevant)

        log_paths = list(dict.fromkeys(_LOG_PATH_RE.findall(combined)))[:10]
        keywords = list(dict.fromkeys(m.group() for m in _KEYWORD_RE.finditer(combined)))[:15]
        confidence = max((1.0 - dist for dist in distances[:1]), default=0.0)

        return LogAccessHint(
            platform_tag=platform_tag,
            log_paths=log_paths,
            keywords=keywords,
            confidence=round(confidence, 3),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_runbooks(self, runbook_path: Path) -> None:
        """Add any runbook files not yet in the Chroma collection.

        Checks the existing collection IDs first to avoid re-embedding documents
        on subsequent startups (important for persistent collections). New documents
        are embedded and added in a single batch call to minimise API overhead.

        Args:
            runbook_path: Directory containing .md / .txt runbook files.
        """
        files = list(runbook_path.glob("**/*.md")) + list(runbook_path.glob("**/*.txt"))
        if not files:
            logger.warning("ChromaKnowledgeBase: no .md/.txt files found in %s", runbook_path)
            return

        existing_ids = set(self._collection.get(include=[])["ids"])

        docs, ids, metadatas = [], [], []
        for f in files:
            doc_id = str(f.relative_to(runbook_path))
            if doc_id in existing_ids:
                continue
            try:
                text = f.read_text(errors="replace").strip()
            except OSError as exc:
                logger.warning("ChromaKnowledgeBase: could not read %s: %s", f, exc)
                continue
            if not text:
                continue
            docs.append(text)
            ids.append(doc_id)
            metadatas.append({"name": f.stem, "path": str(f)})

        if docs:
            try:
                self._collection.add(documents=docs, ids=ids, metadatas=metadatas)  # type: ignore[arg-type]
                logger.debug("ChromaKnowledgeBase: added %d new documents", len(docs))
            except Exception as exc:
                raise KnowledgeBaseError(f"Failed to add documents to Chroma: {exc}") from exc
