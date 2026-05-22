"""Create DOD test data in the ServiceNow dev instance.

Creates:
  - 13 CMDB CI records (cmdb_ci_server, cmdb_ci_service, cmdb_ci_cluster)
  - 1 CMDB cluster-member relationship (cdp-cluster-prod → cdp-rm-02, cdp-nn-01)
  - 10 test incidents covering the full DOD test matrix

Usage:
    SNOW_PASSWORD=<password> python scripts/create_dod_test_data.py

    Optionally override instance or user:
    SNOW_INSTANCE=dev206574.service-now.com SNOW_USER=aria_svc SNOW_PASSWORD=... python ...

After running, the script prints a mapping of DOD label → ServiceNow INC number.
Record these INC numbers for use in pipeline test runs.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import requests

# ── Config ───────────────────────────────────────────────────────────────────

INSTANCE = os.environ.get("SNOW_INSTANCE", "dev206574.service-now.com")
USER = os.environ.get("SNOW_USER", "aria_svc")
PASSWORD = os.environ.get("SNOW_PASSWORD", "")

BASE = f"https://{INSTANCE}/api/now"
AUTH = (USER, PASSWORD)
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


# ── CMDB CI definitions ───────────────────────────────────────────────────────


@dataclass
class CI:
    """A CMDB configuration item to create."""

    name: str
    table: str  # ServiceNow table: cmdb_ci_server | cmdb_ci_service | cmdb_ci_cluster
    ip_address: str = "127.0.0.1"
    short_description: str = ""


CIS: list[CI] = [
    # Simple incidents — clean cmdb_ci
    CI("cdp-dn-01", "cmdb_ci_server", short_description="CDP HDFS DataNode 01"),
    CI("cdp-nn-01", "cmdb_ci_server", short_description="CDP HDFS NameNode 01"),
    CI("cdp-hms-01", "cmdb_ci_service", short_description="CDP Hive Metastore 01"),
    CI("cdp-rm-01", "cmdb_ci_service", short_description="CDP YARN ResourceManager 01"),
    CI("cdp-zk-01", "cmdb_ci_server", short_description="CDP ZooKeeper Node 01"),
    # Edge case — nested root cause #1 (DOD-006)
    CI("cdp-dn-02", "cmdb_ci_server", short_description="CDP HDFS DataNode 02"),
    CI("cdp-nn-02", "cmdb_ci_server", short_description="CDP HDFS NameNode 02"),
    # Edge case — nested root cause #2 (DOD-007)
    CI("cdp-hs2-02", "cmdb_ci_service", short_description="CDP HiveServer2 02"),
    CI("cdp-dn-03", "cmdb_ci_server", short_description="CDP HDFS DataNode 03"),
    # Edge case — cluster-level CI (DOD-008)
    CI(
        "cdp-cluster-prod",
        "cmdb_ci_cluster",
        ip_address="",
        short_description="CDP Production Cluster",
    ),
    CI("cdp-rm-02", "cmdb_ci_server", short_description="CDP YARN ResourceManager 02"),
    # Edge case — empty cmdb_ci incident (DOD-009 — CI created so Agent 1 can resolve it)
    CI("cdp-hms-02", "cmdb_ci_service", short_description="CDP Hive Metastore 02"),
    # False alarm (DOD-010)
    CI("cdp-hs2-03", "cmdb_ci_service", short_description="CDP HiveServer2 03"),
]


# ── Incident definitions ──────────────────────────────────────────────────────


@dataclass
class Incident:
    """A ServiceNow incident to create."""

    label: str
    short_description: str
    description: str
    cmdb_ci_name: str  # resolved to sys_id at runtime; empty string = no CI set
    priority: str = "2"  # 1=Critical, 2=High, 3=Moderate, 4=Low
    opened_at: str = ""
    assignment_group: str = "Data Platform OPS"


INCIDENTS: list[Incident] = [
    # ── Simple incidents ─────────────────────────────────────────────────────
    Incident(
        label="DOD-001",
        short_description="HDFS DataNode cdp-dn-01 — disk space exhausted",
        description=(
            "DataNode cdp-dn-01 is reporting critical disk usage on the CDP cluster. "
            "The node has reached 98% disk utilisation and is no longer accepting block "
            "writes. HDFS replication is degraded and jobs writing to this node are failing "
            "with DiskOutOfSpaceException. Immediate disk cleanup or expansion required."
        ),
        cmdb_ci_name="cdp-dn-01",
        priority="1",
        opened_at="2026-05-22 09:00:00",
    ),
    Incident(
        label="DOD-002",
        short_description="HDFS NameNode cdp-nn-01 — OutOfMemoryError",
        description=(
            "The HDFS NameNode cdp-nn-01 has crashed with a Java OutOfMemoryError. "
            "The heap space was exhausted causing the NameNode main loop to exit. "
            "HDFS is currently in safe mode and no new block writes are being accepted. "
            "All DataNodes have lost their primary metadata server."
        ),
        cmdb_ci_name="cdp-nn-01",
        priority="1",
        opened_at="2026-05-22 09:30:00",
    ),
    Incident(
        label="DOD-003",
        short_description="Hive Metastore cdp-hms-01 — database connection failure",
        description=(
            "The Hive Metastore service on cdp-hms-01 is unable to connect to its backing "
            "MySQL database at mysql-prod-01:3306. All Hive DDL and DML operations are "
            "failing with CommunicationsException. The metastore thrift server is running "
            "but cannot serve requests without the database connection."
        ),
        cmdb_ci_name="cdp-hms-01",
        priority="2",
        opened_at="2026-05-22 10:00:00",
    ),
    Incident(
        label="DOD-004",
        short_description="YARN ResourceManager cdp-rm-01 — NodeManager heartbeat timeouts",
        description=(
            "The YARN ResourceManager cdp-rm-01 is reporting repeated NodeManager heartbeat "
            "timeouts. Three NodeManagers (cdp-nm-04, cdp-nm-07, cdp-nm-12) have been declared "
            "lost in the last 5 minutes due to missed heartbeats. Cluster capacity has dropped "
            "by 12.5%. A network partition or switch failure is suspected."
        ),
        cmdb_ci_name="cdp-rm-01",
        priority="2",
        opened_at="2026-05-22 10:30:00",
    ),
    Incident(
        label="DOD-005",
        short_description="ZooKeeper cdp-zk-01 — quorum lost, ensemble degraded",
        description=(
            "The ZooKeeper node cdp-zk-01 reports that the ensemble has lost quorum. "
            "Peers cdp-zk-02 and cdp-zk-03 are unreachable, leaving only 1 of 3 nodes "
            "responsive. Leader election has failed repeatedly. Services depending on "
            "ZooKeeper coordination (HDFS HA, HBase, Kafka brokers) are affected."
        ),
        cmdb_ci_name="cdp-zk-01",
        priority="2",
        opened_at="2026-05-22 11:00:00",
    ),
    # ── Edge cases ────────────────────────────────────────────────────────────
    Incident(
        label="DOD-006",
        short_description="HDFS DataNode cdp-dn-02 — write pipeline failures",
        description=(
            "DataNode cdp-dn-02 is experiencing repeated write pipeline failures and is "
            "unable to replicate blocks. The DataNode logs show connection refused errors "
            "when attempting to communicate with the NameNode at cdp-nn-02:8020. The "
            "DataNode has entered degraded mode and suspended all write operations pending "
            "NameNode recovery."
        ),
        cmdb_ci_name="cdp-dn-02",
        priority="1",
        opened_at="2026-05-22 11:30:00",
    ),
    Incident(
        label="DOD-007",
        short_description="HiveServer2 cdp-hs2-02 — HDFS write timeouts causing query failures",
        description=(
            "HiveServer2 cdp-hs2-02 is experiencing query timeouts due to HDFS write latency. "
            "Tez tasks are failing because HDFS write pipelines to cdp-dn-03 are timing out "
            "after 30 seconds. The HDFS DataNode cdp-dn-03 serving the /user/hive/warehouse "
            "partition appears to have disk I/O issues that are causing write delays."
        ),
        cmdb_ci_name="cdp-hs2-02",
        priority="2",
        opened_at="2026-05-22 12:00:00",
    ),
    Incident(
        label="DOD-008",
        short_description="YARN jobs failing on production cluster cdp-cluster-prod",
        description=(
            "Multiple Spark and MapReduce jobs are failing to submit on the production cluster. "
            "The YARN ResourceManager on cdp-rm-02 is showing repeated full GC pauses and high "
            "heap usage. The ResourceManager node has not crashed but is unresponsive to new "
            "job submissions due to GC overhead. All cluster queues are blocked."
        ),
        cmdb_ci_name="cdp-cluster-prod",
        priority="1",
        opened_at="2026-05-22 12:30:00",
    ),
    Incident(
        label="DOD-009",
        short_description="Hive queries failing on prod — metastore unreachable",
        description=(
            "Teams across the data platform are unable to execute any Hive queries. All "
            "attempts to connect to the Hive Metastore are failing with connection refused "
            "errors. The Hive Metastore process on cdp-hms-02 appears to be down — port "
            "9083 is not responding. The underlying MySQL database at mysql-prod-02:3306 "
            "may be unavailable, preventing the metastore from starting."
        ),
        cmdb_ci_name="",  # intentionally empty — Agent 1 must extract from description
        priority="1",
        opened_at="2026-05-22 13:00:00",
    ),
    Incident(
        label="DOD-010",
        short_description="Nightly ETL job failing on HiveServer2 cdp-hs2-03",
        description=(
            "The nightly ETL pipeline is failing on HiveServer2 cdp-hs2-03. No infrastructure "
            "or resource alerts are firing — HDFS, YARN, and ZooKeeper are all healthy. The "
            "failing job processes CSV files from the upstream ingestion pipeline into ORC "
            "tables in the data warehouse. Failures started after a schema change in the "
            "source system. The job is aborting with schema mismatch errors on the user_id column."
        ),
        cmdb_ci_name="cdp-hs2-03",
        priority="3",
        opened_at="2026-05-22 13:30:00",
    ),
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _post(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to a ServiceNow table and return the result dict."""
    url = f"{BASE}/table/{table}"
    resp = requests.post(url, auth=AUTH, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})


def _get_sys_id(table: str, name: str) -> str | None:
    """Look up the sys_id of a CI by name. Returns None if not found."""
    url = f"{BASE}/table/{table}"
    resp = requests.get(
        url,
        auth=AUTH,
        headers=HEADERS,
        params={"sysparm_query": f"name={name}", "sysparm_fields": "sys_id", "sysparm_limit": "1"},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0]["sys_id"] if results else None


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """Create all CMDB CIs, cluster relationships, and test incidents."""
    if not PASSWORD:
        print("ERROR: SNOW_PASSWORD environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to https://{INSTANCE} as {USER}")
    print()

    # ── Step 1: Create CMDB CIs ──────────────────────────────────────────────
    print("=== Step 1: Creating CMDB CI records ===")
    ci_sys_ids: dict[str, str] = {}

    for ci in CIS:
        payload: dict[str, Any] = {
            "name": ci.name,
            "short_description": ci.short_description,
        }
        if ci.ip_address:
            payload["ip_address"] = ci.ip_address

        result = _post(ci.table, payload)
        sys_id = result.get("sys_id", "")
        ci_sys_ids[ci.name] = sys_id
        print(f"  Created CI: {ci.name} ({ci.table}) → sys_id={sys_id[:20]}...")

    print()

    # ── Step 2: Create cluster member relationships ──────────────────────────
    print("=== Step 2: Creating cluster member relationships ===")
    cluster_sys_id = ci_sys_ids.get("cdp-cluster-prod", "")
    member_names = ["cdp-rm-02", "cdp-nn-01"]

    for member_name in member_names:
        member_sys_id = ci_sys_ids.get(member_name, "")
        if not cluster_sys_id or not member_sys_id:
            print(f"  SKIP: missing sys_id for cdp-cluster-prod or {member_name}")
            continue

        rel_payload = {
            "parent": cluster_sys_id,
            "child": member_sys_id,
            "type": {"display_value": "Members::Member of"},
        }
        result = _post("cmdb_rel_ci", rel_payload)
        print(
            f"  Created relationship: cdp-cluster-prod → {member_name} (sys_id={result.get('sys_id','')[:20]}...)"
        )

    print()

    # ── Step 3: Create incidents ─────────────────────────────────────────────
    print("=== Step 3: Creating incidents ===")
    inc_mapping: dict[str, str] = {}

    for inc in INCIDENTS:
        payload = {
            "short_description": inc.short_description,
            "description": inc.description,
            "priority": inc.priority,
            "state": "1",  # New
            "assignment_group": {"display_value": inc.assignment_group},
        }

        if inc.opened_at:
            payload["opened_at"] = inc.opened_at

        if inc.cmdb_ci_name:
            ci_sys_id = ci_sys_ids.get(inc.cmdb_ci_name, "")
            if ci_sys_id:
                payload["cmdb_ci"] = ci_sys_id
            else:
                print(
                    f"  WARN: no sys_id for cmdb_ci '{inc.cmdb_ci_name}' — incident will have no CI"
                )

        result = _post("incident", payload)
        inc_number = result.get("number", "?")
        inc_mapping[inc.label] = inc_number
        ci_note = f"cmdb_ci={inc.cmdb_ci_name}" if inc.cmdb_ci_name else "cmdb_ci=EMPTY"
        print(f"  Created {inc.label} → {inc_number}  ({ci_note})")

    print()
    print("=== DOD label → INC number mapping ===")
    print(json.dumps(inc_mapping, indent=2))

    # Save mapping to file for reference
    mapping_path = "data/dod_incident_mapping.json"
    with open(mapping_path, "w") as f:
        json.dump(inc_mapping, f, indent=2)
    print(f"\nMapping saved to {mapping_path}")
    print("\nDone. Use these INC numbers with POST /api/v1/pipeline/run to run DOD tests.")


if __name__ == "__main__":
    main()
