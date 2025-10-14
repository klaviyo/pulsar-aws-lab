"""
Operational utilities for Pulsar OMB Orchestrator.
Handles cleanup operations for workers, topics, and namespaces.
"""

import logging
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def cleanup_pulsar_topics(namespace: str, pulsar_namespace: str) -> None:
    """
    Clean up all topics in a Pulsar namespace.

    Args:
        namespace: Kubernetes namespace
        pulsar_namespace: Pulsar tenant/namespace (e.g., public/omb-test-abc)
    """
    logger.info(f"Cleaning up Pulsar topics in namespace '{pulsar_namespace}'...")

    # List all topics in the namespace
    result = subprocess.run(
        ["kubectl", "exec", "-n", namespace, "pulsar-broker-0", "--",
         "bin/pulsar-admin", "topics", "list", pulsar_namespace],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        logger.warning(f"Failed to list topics: {result.stderr}")
        return

    # Parse topic list
    topics = [
        line.strip()
        for line in result.stdout.strip().split('\n')
        if line.strip() and line.strip().startswith('persistent://')
           and 'Defaulted container' not in line
    ]

    if not topics:
        logger.info("No topics found to delete")
        return

    logger.info(f"Found {len(topics)} topic(s) to delete")

    # Delete each topic
    deleted = 0
    failed = 0

    for topic in topics:
        delete_result = subprocess.run(
            ["kubectl", "exec", "-n", namespace, "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "delete", topic, "-f"],
            capture_output=True,
            text=True,
            check=False
        )

        if delete_result.returncode == 0:
            deleted += 1
        else:
            failed += 1
            logger.warning(f"Failed to delete topic {topic}: {delete_result.stderr}")

    logger.info(f"✓ Cleaned up {deleted}/{len(topics)} topics")
    if failed > 0:
        logger.warning(f"⚠ {failed} topics failed to delete")


def cleanup_pulsar_namespaces(pattern: str = "omb-test-*", dry_run: bool = False) -> None:
    """
    Clean up Pulsar namespaces matching a pattern.

    Args:
        pattern: Glob pattern for namespace names to delete (default: omb-test-*)
        dry_run: If True, only list namespaces without deleting
    """
    print(f"\nLooking for Pulsar namespaces matching: public/{pattern}")
    print("=" * 60)

    # List all namespaces in public tenant
    result = subprocess.run(
        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
         "bin/pulsar-admin", "namespaces", "list", "public"],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        print(f"Error listing namespaces: {result.stderr}")
        return

    # Parse namespace list and filter by pattern
    lines = result.stdout.strip().split('\n')
    namespaces = []
    for line in lines:
        line = line.strip()
        if line and line.startswith('public/') and 'Defaulted container' not in line:
            namespace_name = line.split('/')[-1]
            # Match pattern (simple glob matching)
            if fnmatch(namespace_name, pattern):
                namespaces.append(line)

    if not namespaces:
        print(f"No namespaces found matching pattern: {pattern}")
        return

    print(f"Found {len(namespaces)} namespace(s) to {'delete' if not dry_run else 'list'}:\n")
    for ns in namespaces:
        print(f"  - {ns}")

    if dry_run:
        print("\n[DRY RUN] No changes made. Run without --dry-run to delete.")
        return

    print(f"\n{'='*60}")
    confirm = input(f"Delete {len(namespaces)} namespace(s)? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return

    print("\nDeleting namespaces...")
    deleted = 0
    failed = 0

    for ns in namespaces:
        print(f"\nProcessing {ns}...")

        total_deleted = 0
        total_failed = 0

        # First, delete regular (non-partitioned) topics
        topic_result = subprocess.run(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "list", ns],
            capture_output=True,
            text=True,
            check=False
        )

        if topic_result.returncode == 0:
            topics = [t.strip() for t in topic_result.stdout.strip().split('\n')
                     if t.strip() and t.strip().startswith('persistent://')]

            if topics:
                print(f"  Found {len(topics)} regular topic(s), deleting...")
                for topic in topics:
                    delete_result = subprocess.run(
                        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                         "bin/pulsar-admin", "topics", "delete", topic, "-f"],
                        capture_output=True,
                        text=True,
                        check=False
                    )

                    if delete_result.returncode == 0:
                        total_deleted += 1
                    else:
                        total_failed += 1
                        print(f"    ✗ Failed to delete topic {topic}: {delete_result.stderr.strip()}")

        # Second, delete partitioned topics
        partitioned_result = subprocess.run(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "list-partitioned-topics", ns],
            capture_output=True,
            text=True,
            check=False
        )

        if partitioned_result.returncode == 0:
            partitioned_topics = [t.strip() for t in partitioned_result.stdout.strip().split('\n')
                                 if t.strip() and t.strip().startswith('persistent://')]

            if partitioned_topics:
                print(f"  Found {len(partitioned_topics)} partitioned topic(s), deleting...")
                for topic in partitioned_topics:
                    delete_result = subprocess.run(
                        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                         "bin/pulsar-admin", "topics", "delete-partitioned-topic", topic, "-f"],
                        capture_output=True,
                        text=True,
                        check=False
                    )

                    if delete_result.returncode == 0:
                        total_deleted += 1
                    else:
                        total_failed += 1
                        print(f"    ✗ Failed to delete partitioned topic {topic}: {delete_result.stderr.strip()}")

        if total_deleted > 0 or total_failed > 0:
            print(f"  Total topics: {total_deleted} deleted, {total_failed} failed")
        else:
            print(f"  No topics found in {ns}")

        # Now delete the namespace
        result = subprocess.run(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "delete", ns],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            print(f"  ✓ Namespace deleted: {ns}")
            deleted += 1
        else:
            error_msg = result.stderr.strip()
            # Filter out "Defaulted container" warnings
            error_lines = [line for line in error_msg.split('\n')
                          if 'Defaulted container' not in line]
            clean_error = '\n'.join(error_lines).strip()
            print(f"  ✗ Failed to delete namespace {ns}: {clean_error}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Summary: {deleted} deleted, {failed} failed")
