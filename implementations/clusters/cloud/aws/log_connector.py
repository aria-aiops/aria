"""AWS EMR log connector — reads cluster logs from an S3 log bucket.

EMR automatically ships logs to S3 when a log bucket is configured at cluster
creation time. Default S3 path: s3://{bucket}/elasticmapreduce/{cluster-id}/

Vault secrets:
  EMR_LOG_BUCKET  — S3 bucket name (required)
  EMR_REGION      — AWS region, e.g. eu-west-1 (optional, falls back to us-east-1)

ARI-51
"""

import gzip
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from core.exceptions import LogStoreUnavailableError, VaultSecretNotFoundError
from core.interfaces.log_store import LogStoreInterface
from core.interfaces.vault import VaultInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag

logger = logging.getLogger(__name__)

_LEVEL_PRIORITY = {"ERROR": 0, "FATAL": 0, "WARN": 1, "WARNING": 1, "INFO": 2, "DEBUG": 3}

# Hadoop / YARN / Spark log line: "2024-01-15 10:23:45,123 WARN Class: message"
_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+"
    r"(?P<level>DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+"
    r"(?:\S+:\s+)?(?P<message>.+)$"
)


class AWSEMRLogConnector(LogStoreInterface):
    """LogStoreInterface backed by EMR logs in an S3 bucket.

    boto3 credentials follow the standard AWS credential chain — IAM role,
    env vars, or ~/.aws/credentials. Auth failures raise
    ``LogStoreUnavailableError``; S3 / parse errors return an empty result.
    """

    def __init__(
        self,
        vault: VaultInterface,
        bucket_secret: str = "EMR_LOG_BUCKET",
        region_secret: str = "EMR_REGION",
        default_region: str = "us-east-1",
        log_prefix: str = "elasticmapreduce",
    ) -> None:
        """Initialise the EMR log connector.

        Args:
            vault: Secret store for retrieving the S3 bucket name and region.
            bucket_secret: Vault key name for the S3 log bucket. Defaults to 'EMR_LOG_BUCKET'.
            region_secret: Vault key name for the AWS region. Defaults to 'EMR_REGION'.
            default_region: Fallback region when EMR_REGION is not set. Defaults to 'us-east-1'.
            log_prefix: S3 key prefix for EMR logs. Defaults to 'elasticmapreduce'.
        """
        self._vault = vault
        self._bucket_secret = bucket_secret
        self._region_secret = region_secret
        self._default_region = default_region
        self._log_prefix = log_prefix

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
        """List S3 objects under the EMR log prefix for the host and parse matching log lines.

        Uses the S3 object LastModified timestamp as a pre-filter (±2h window) to avoid
        reading stale objects. Both .gz and plain text files are supported.

        Args:
            host: EMR cluster ID used as the S3 key segment (e.g. 'j-1234567890').
            platform_tag: Not used for filtering — passed through for traceability.
            start_time: Start of the query window. Lines outside this window are dropped.
            end_time: End of the query window.
            keywords: Optional keyword filter applied after S3 fetch.
            log_paths: Optional sub-paths under the cluster prefix (e.g. ['steps/', 'containers/']).
            max_results: Maximum log lines to return.

        Returns:
            LogQueryResult — empty with LOW confidence on S3 or auth failure.

        Raises:
            LogStoreUnavailableError: If AWS credentials are missing or S3 client cannot be created.
        """
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        bucket = self._vault.get_secret(self._bucket_secret)

        region = self._default_region
        try:
            region = self._vault.get_secret(self._region_secret)
        except VaultSecretNotFoundError:
            pass

        query_desc = f"s3://{bucket}/{self._log_prefix}/{host}/"

        try:
            s3 = boto3.client("s3", region_name=region)
        except NoCredentialsError as exc:
            raise LogStoreUnavailableError(
                "No AWS credentials — configure IAM role or env vars"
            ) from exc
        except Exception as exc:
            raise LogStoreUnavailableError(f"Failed to create S3 client: {exc}") from exc

        prefixes = (
            [f"{self._log_prefix}/{host}/{p.lstrip('/')}" for p in log_paths]
            if log_paths
            else [f"{self._log_prefix}/{host}/"]
        )

        all_lines: list[LogLine] = []
        for prefix in prefixes:
            try:
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        lm = obj["LastModified"].replace(tzinfo=None)
                        if lm < start_time - timedelta(hours=2):
                            continue
                        lines = _fetch_s3_log(s3, bucket, obj["Key"], host, start_time, end_time)
                        all_lines.extend(lines)
                        if len(all_lines) >= max_results * 4:
                            break
            except (ClientError, NoCredentialsError) as exc:
                logger.warning("AWSEMRLogConnector: S3 error for %s: %s", prefix, exc)
            except Exception as exc:
                logger.warning("AWSEMRLogConnector: unexpected error for %s: %s", prefix, exc)

        if keywords:
            all_lines = [
                ll for ll in all_lines if any(k.lower() in ll.message.lower() for k in keywords)
            ]

        all_lines.sort(key=lambda ll: (_LEVEL_PRIORITY.get(ll.level.upper(), 99), ll.timestamp))
        total = len(all_lines)
        confidence = (
            ConfidenceBand.HIGH
            if total >= 10
            else ConfidenceBand.MEDIUM if total > 0 else ConfidenceBand.LOW
        )

        logger.debug(
            "AWSEMRLogConnector: %r → %d/%d lines", host, len(all_lines[:max_results]), total
        )
        return LogQueryResult(
            log_lines=all_lines[:max_results],
            query_executed=query_desc,
            total_scanned=total,
            confidence=confidence,
        )


def _fetch_s3_log(
    s3: Any, bucket: str, key: str, host: str, start_time: datetime, end_time: datetime
) -> list[LogLine]:
    """Download and parse a single S3 log object, returning lines within the time window.

    Transparently handles .gz compressed files. Returns an empty list on any error
    (S3 access denied, corrupt gzip, parse failure) so the caller can continue with
    other objects.

    Args:
        s3: boto3 S3 client.
        bucket: S3 bucket name.
        key: S3 object key.
        host: Hostname used as the source field in returned LogLine objects.
        start_time: Start of the time window.
        end_time: End of the time window.

    Returns:
        List of parsed LogLine objects within the time window. Empty on any error.
    """
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        raw = resp["Body"].read()
        if key.endswith(".gz"):
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", errors="replace")
        lines = []
        for raw_line in text.splitlines():
            ll = _parse_line(raw_line, host)
            if ll and start_time <= ll.timestamp <= end_time:
                lines.append(ll)
        return lines
    except Exception as exc:
        logger.debug("AWSEMRLogConnector: failed to read s3://%s/%s: %s", bucket, key, exc)
        return []


def _parse_line(line: str, host: str) -> LogLine | None:
    """Parse a single EMR/Hadoop log line into a LogLine.

    Expected format: '2024-01-15 10:23:45,123 WARN ClassName: message'

    Args:
        line: Raw log line text.
        host: Hostname used as the source field in the returned LogLine.

    Returns:
        LogLine on successful parse, None if the line doesn't match or timestamp is invalid.
    """
    m = _LOG_RE.match(line.strip())
    if not m:
        return None
    try:
        ts = datetime.fromisoformat(m.group("ts"))
    except ValueError:
        return None
    level = m.group("level").upper().replace("WARNING", "WARN").replace("CRITICAL", "ERROR")
    return LogLine(timestamp=ts, level=level, message=m.group("message"), source=host)
