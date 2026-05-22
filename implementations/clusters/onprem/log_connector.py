"""On-premise SSH log connector — provider-agnostic log retrieval over SSH.

Works for any cluster running on bare-metal, VMs, or private cloud where logs
are accessible via SSH grep: CDP, HDP, MapR, Oracle RAC, generic Hadoop, etc.

The caller supplies the SSH credentials and log directories at construction time
so the same class covers every on-premise platform without subclassing.

ARI-47 / ARI-67
"""

import io
import logging
import re
import shlex
from datetime import datetime

import paramiko

from core.exceptions import LogStoreUnavailableError
from core.interfaces.log_store import LogStoreInterface
from core.interfaces.vault import VaultInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag

logger = logging.getLogger(__name__)

_DEFAULT_KEYWORDS = ["ERROR", "WARN", "FATAL", "OOM", "Exception", "OutOfMemory"]

_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,\.]\d+)\s+" r"(?P<level>\w+)\s+" r"(?P<rest>.+)$"
)
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S,%f")
_LEVEL_PRIORITY = {"ERROR": 0, "FATAL": 0, "WARN": 1, "WARNING": 1, "INFO": 2, "DEBUG": 3}

_MAX_RAW_LINES = 2000


class SSHLogConnector(LogStoreInterface):
    """LogStoreInterface backed by SSH access to any on-premise cluster node.

    Retrieves logs by SSHing to the target node and grepping the configured
    log directories. The SSH private key is pulled from vault at query time.
    Non-fatal: SSH failures return an empty result and log a WARNING.

    Usage — CDP cluster:
        SSHLogConnector(
            vault,
            ssh_key_secret="CDP_SSH_KEY",
            ssh_user="hadoop",
            log_dirs=["/var/log/hadoop-hdfs", "/var/log/hadoop-yarn", ...],
        )

    Usage — any other on-prem cluster:
        SSHLogConnector(
            vault,
            ssh_key_secret="MY_CLUSTER_SSH_KEY",
            ssh_user="admin",
            log_dirs=["/var/log/myplatform"],
        )
    """

    def __init__(
        self,
        vault: VaultInterface,
        ssh_key_secret: str,
        ssh_user: str,
        log_dirs: list[str] | None = None,
        default_keywords: list[str] | None = None,
        ssh_port: int = 22,
        timeout: int = 30,
        host_key_secret: str | None = None,
    ) -> None:
        """Initialise the SSH log connector.

        Args:
            vault: Secret store used to retrieve the SSH private key (and optional host key).
            ssh_key_secret: Vault key name for the PEM-encoded SSH private key.
            ssh_user: SSH username on the target nodes (e.g. 'hadoop', 'admin').
            log_dirs: List of directories to search for log files on the target host.
                      When None, query_logs returns an empty result (no directories = no grep).
            default_keywords: Keywords used for grep when no KB hints are available.
                              Defaults to ERROR, WARN, FATAL, OOM, Exception, OutOfMemory.
            ssh_port: SSH port. Defaults to 22.
            timeout: SSH connection and command timeout in seconds. Defaults to 30.
            host_key_secret: Vault key name for the base64-encoded SSH host public key.
                             Required for strict host key checking. When None, connection
                             is refused (RejectPolicy) — always provide this in production.
        """
        self._vault = vault
        self._ssh_key_secret = ssh_key_secret
        self._ssh_user = ssh_user
        self._log_dirs = log_dirs
        self._default_keywords = default_keywords or _DEFAULT_KEYWORDS
        self._ssh_port = ssh_port
        self._timeout = timeout
        self._host_key_secret = host_key_secret

    # ── LogStoreInterface ─────────────────────────────────────────────────────

    def query_logs(
        self,
        host: str,
        platform_tag: PlatformTag,
        start_time: datetime,
        end_time: datetime,
        keywords: list[str] | None = None,
        log_paths: list[str] | None = None,
        max_results: int = 50,
    ) -> LogQueryResult:
        """SSH into the target host, grep log directories for keywords, and return parsed lines.

        Never raises — SSH or grep failures return an empty result with LOW confidence
        and log a WARNING so the pipeline continues.

        Args:
            host: Hostname or IP to SSH into.
            platform_tag: Passed through to query_executed for traceability only.
            start_time: Start of the time window. Lines outside this window are dropped.
            end_time: End of the time window.
            keywords: Keywords to grep for. Defaults to default_keywords if None.
            log_paths: Directories to search. Overrides the connector's log_dirs if provided.
            max_results: Maximum number of parsed log lines to return.

        Returns:
            LogQueryResult — empty with LOW confidence on SSH failure or no matching lines.
        """
        dirs = log_paths or self._log_dirs or []
        kws = keywords or self._default_keywords
        query_desc = (
            f"ssh://{self._ssh_user}@{host} "
            f"dirs={len(dirs)} keywords={len(kws)} "
            f"[{start_time.isoformat()} \u2192 {end_time.isoformat()}]"
        )

        if not dirs:
            logger.warning("SSHLogConnector: no log_dirs configured for %r", host)
            return LogQueryResult(
                log_lines=[],
                query_executed=query_desc,
                total_scanned=0,
                confidence=ConfidenceBand.LOW,
            )

        try:
            raw_lines = self._ssh_grep(host, dirs, kws)
        except Exception as exc:
            logger.warning("SSHLogConnector SSH failed for %r: %s", host, exc)
            return LogQueryResult(
                log_lines=[],
                query_executed=query_desc,
                total_scanned=0,
                confidence=ConfidenceBand.LOW,
            )

        log_lines = []
        for raw in raw_lines:
            ll = _parse_line(raw, host)
            if ll is None:
                continue
            if not (start_time <= ll.timestamp <= end_time):
                continue
            log_lines.append(ll)

        log_lines.sort(
            key=lambda line: (_LEVEL_PRIORITY.get(line.level.upper(), 99), line.timestamp)
        )
        total = len(log_lines)
        log_lines = log_lines[:max_results]

        confidence = (
            ConfidenceBand.HIGH
            if total >= 10
            else ConfidenceBand.MEDIUM if total > 0 else ConfidenceBand.LOW
        )

        logger.debug("SSHLogConnector: %r → %d/%d lines", host, len(log_lines), total)
        return LogQueryResult(
            log_lines=log_lines,
            query_executed=query_desc,
            total_scanned=total,
            confidence=confidence,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _ssh_grep(self, host: str, dirs: list[str], keywords: list[str]) -> list[str]:
        """Connect via SSH and run a grep across the configured log directories.

        Constructs a single grep command that searches all directories at once
        and tails the last _MAX_RAW_LINES lines to avoid transferring huge files.

        Args:
            host: Target hostname or IP.
            dirs: List of log directories to grep.
            keywords: List of keywords to match (OR-combined into a single regex).

        Returns:
            List of raw log line strings from stdout.

        Raises:
            ValueError: If host_key_secret is not set (security requirement).
            Any SSH exception propagates to query_logs which handles it.
        """
        key_pem = self._vault.get_secret(self._ssh_key_secret)
        pkey = _load_private_key(key_pem)

        client = paramiko.SSHClient()
        if self._host_key_secret:
            host_key_pem = self._vault.get_secret(self._host_key_secret)
            _load_known_host_key(client, host, host_key_pem)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            raise ValueError(
                f"SSHLogConnector: host_key_secret is required for {host!r}. "
                "Set CDP_HOST_KEY (or the relevant host_key_secret) to enable the connector."
            )
        try:
            client.connect(
                hostname=host,
                username=self._ssh_user,
                pkey=pkey,
                port=self._ssh_port,
                timeout=self._timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            pattern = "|".join(re.escape(k) for k in keywords)
            quoted_dirs = " ".join(shlex.quote(d) for d in dirs)
            cmd = (
                f"grep -rh -E {shlex.quote(pattern)} {quoted_dirs} 2>/dev/null"
                f" | tail -n {_MAX_RAW_LINES}"
            )
            _, stdout, _ = client.exec_command(cmd, timeout=self._timeout)
            return stdout.read().decode("utf-8", errors="replace").splitlines()
        finally:
            client.close()


def _load_known_host_key(client: paramiko.SSHClient, hostname: str, pubkey_b64: str) -> None:
    """Register a base64-encoded SSH public key as the only trusted key for hostname."""
    import base64

    parts = pubkey_b64.strip().split()
    if len(parts) < 2:
        raise LogStoreUnavailableError("Invalid SSH host key format — expected '<type> <base64>'")
    key_type, key_data = parts[0], parts[1]
    raw = base64.b64decode(key_data)
    key_classes = {
        "ssh-rsa": paramiko.RSAKey,
        "ssh-ed25519": paramiko.Ed25519Key,
        "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
        "ecdsa-sha2-nistp384": paramiko.ECDSAKey,
        "ecdsa-sha2-nistp521": paramiko.ECDSAKey,
    }
    cls = key_classes.get(key_type)
    if cls is None:
        raise LogStoreUnavailableError(f"Unsupported SSH host key type: {key_type}")
    key_obj = cls(data=raw)
    client.get_host_keys().add(hostname, key_type, key_obj)


def _load_private_key(pem: str) -> paramiko.PKey:
    """Try each known paramiko key class against the PEM string and return the first that succeeds.

    DSSKey was removed in paramiko 3.x — guarded with hasattr to stay compatible
    with both paramiko 2.x and 3.x without conditional imports.

    Raises:
        LogStoreUnavailableError: If no key class accepts the PEM data.
    """
    # DSSKey was removed in paramiko 3.x — guard with hasattr
    _key_classes = [
        c for c in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey) if c is not None
    ] + ([paramiko.DSSKey] if hasattr(paramiko, "DSSKey") else [])
    for cls in _key_classes:
        try:
            return cls.from_private_key(io.StringIO(pem))
        except paramiko.SSHException:
            continue
    raise LogStoreUnavailableError("Cannot load SSH private key — unsupported key format")


def _parse_line(raw: str, host: str) -> LogLine | None:
    """Parse a single raw log line into a LogLine using the standard Hadoop/YARN log format.

    Expected format: '2024-01-15 10:23:45,123 ERROR ClassName: message text'

    Args:
        raw: A single line of text from the SSH grep output.
        host: Hostname used as the source field in the returned LogLine.

    Returns:
        LogLine on successful parse, None if the line doesn't match the pattern
        or if the timestamp cannot be parsed.
    """
    m = _LOG_LINE_RE.match(raw.strip())
    if not m:
        return None
    ts_str = m.group("ts").replace(",", ".")
    for fmt in _TS_FORMATS:
        try:
            ts = datetime.strptime(ts_str, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    return LogLine(
        timestamp=ts,
        level=m.group("level"),
        message=m.group("rest"),
        source=host,
    )
