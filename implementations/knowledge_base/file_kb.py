"""File-based KnowledgeBaseInterface — keyword scoring against local runbook files.

Loads all .md/.txt files from a directory at init time. Queries score each
file by token overlap with the incident text and extract log paths/keywords
via regex. No network, no vector DB — safe for unit tests and local dev.

ARI-59
"""

import logging
import re
from pathlib import Path

from core.exceptions import KnowledgeBaseError
from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.models import LogAccessHint, PlatformTag

logger = logging.getLogger(__name__)

_HIGH_SCORE = 0.15
_MEDIUM_SCORE = 0.05

_LOG_PATH_RE = re.compile(r"/[\w./-]*\.log[\w/.-]*|/var/log/[\w/.-]+")
_KEYWORD_RE = re.compile(
    r"\b(ERROR|WARN|FATAL|OOM|OutOfMemory|disk full|timeout|"
    r"connection refused|HDFS|YARN|Hive|Spark|Oozie|safe mode)\b",
    re.IGNORECASE,
)


class FileKnowledgeBase(KnowledgeBaseInterface):
    """KnowledgeBaseInterface backed by local .md/.txt runbook files.

    Each file is scored by keyword overlap with the query text.
    The file stem is treated as the service/component name.
    """

    def __init__(self, runbook_dir: str) -> None:
        """Load all .md/.txt runbook files from the given directory at construction time.

        Args:
            runbook_dir: Path to the directory containing runbook files.

        Raises:
            KnowledgeBaseError: If the directory does not exist.
        """
        path = Path(runbook_dir)
        if not path.is_dir():
            raise KnowledgeBaseError(f"Runbook directory not found: {runbook_dir}")
        self._files = self._load(path)
        logger.debug("FileKnowledgeBase loaded %d files from %s", len(self._files), runbook_dir)

    # ── KnowledgeBaseInterface ───────────────────────────────────────────────

    def get_service_hints(self, cluster: str, description: str) -> list[str]:
        """Return candidate service names ordered by keyword relevance."""
        if not self._files:
            return []
        tokens = _tokenize(f"{cluster} {description}")
        scored = _score(self._files, tokens)
        return [name.replace("_", "-") for name, _ in scored]

    def get_log_hints(self, service: str, platform_tag: PlatformTag) -> LogAccessHint:
        """Return log paths and keywords from the best-matching runbook file."""
        tokens = _tokenize(f"{service} {platform_tag.value}")
        scored = _score(self._files, tokens)

        if not scored:
            return LogAccessHint(
                platform_tag=platform_tag, log_paths=[], keywords=[], confidence=0.0
            )

        best_name, best_score = scored[0]
        content = self._files[best_name]

        log_paths = list(dict.fromkeys(_LOG_PATH_RE.findall(content)))[:10]
        keywords = list(dict.fromkeys(m.group() for m in _KEYWORD_RE.finditer(content)))[:15]

        if best_score >= _HIGH_SCORE:
            confidence = 0.85
        elif best_score >= _MEDIUM_SCORE:
            confidence = 0.55
        else:
            confidence = 0.2

        return LogAccessHint(
            platform_tag=platform_tag,
            log_paths=log_paths,
            keywords=keywords,
            confidence=confidence,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _load(directory: Path) -> dict[str, str]:
        """Recursively read all .md and .txt files into a dict keyed by lowercased file stem.

        Args:
            directory: Root directory to search.

        Returns:
            Dict mapping file stem (e.g. 'hive-metastore') to file content string.
        """
        files: dict[str, str] = {}
        for pattern in ("**/*.md", "**/*.txt"):
            for p in directory.glob(pattern):
                files[p.stem.lower()] = p.read_text(encoding="utf-8")
        return files


def _tokenize(text: str) -> set[str]:
    """Split text into a set of lowercased tokens, ignoring words shorter than 3 characters.

    Short words (stop words, prepositions) are dropped because they add noise to
    token-overlap scoring without improving relevance.
    """
    return {w.lower() for w in re.findall(r"\w+", text) if len(w) > 2}


def _score(files: dict[str, str], tokens: set[str]) -> list[tuple[str, float]]:
    """Score each file by token overlap. Returns [(name, score)] sorted desc."""
    results = []
    for name, content in files.items():
        file_tokens = _tokenize(content)
        overlap = len(tokens & file_tokens)
        if overlap:
            score = overlap / (len(tokens) + 1)
            results.append((name, score))
    return sorted(results, key=lambda x: x[1], reverse=True)
