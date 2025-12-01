"""
Operational utilities for Pulsar OMB Orchestrator.
Handles cleanup operations for workers, topics, and namespaces.
"""

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Tuple

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

logger = logging.getLogger(__name__)
console = Console()


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


@dataclass
class NamespaceDeleteResult:
    """Result of deleting a single namespace."""
    namespace: str
    success: bool
    topics_deleted: int
    topics_failed: int
    error: str = ""


def _delete_single_namespace(ns: str, progress: Progress = None) -> NamespaceDeleteResult:
    """
    Delete a single Pulsar namespace and all its topics.

    Args:
        ns: Full namespace path (e.g., public/omb-test-abc)
        progress: Optional Rich Progress object for sub-task tracking

    Returns:
        NamespaceDeleteResult with deletion outcome
    """
    topics_deleted = 0
    topics_failed = 0
    ns_short = ns.split('/')[-1]  # Get just the namespace name for display
    topic_task = None

    # First, list all topics (regular + partitioned) to get total count
    all_topics = []

    topic_result = subprocess.run(
        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
         "bin/pulsar-admin", "topics", "list", ns],
        capture_output=True,
        text=True,
        check=False
    )

    if topic_result.returncode == 0:
        regular_topics = [t.strip() for t in topic_result.stdout.strip().split('\n')
                        if t.strip() and t.strip().startswith('persistent://')]
        all_topics.extend([('regular', t) for t in regular_topics])

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
        all_topics.extend([('partitioned', t) for t in partitioned_topics])

    # Create sub-task for topic deletion if we have topics and a progress bar
    if progress and all_topics:
        topic_task = progress.add_task(
            f"  [dim]{ns_short}[/dim]",
            total=len(all_topics)
        )

    # Delete all topics
    for topic_type, topic in all_topics:
        if topic_type == 'regular':
            delete_result = subprocess.run(
                ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "topics", "delete", topic, "-f"],
                capture_output=True,
                text=True,
                check=False
            )
        else:  # partitioned
            delete_result = subprocess.run(
                ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "topics", "delete-partitioned-topic", topic, "-f"],
                capture_output=True,
                text=True,
                check=False
            )

        if delete_result.returncode == 0:
            topics_deleted += 1
        else:
            topics_failed += 1

        if topic_task is not None:
            progress.advance(topic_task)

    # Remove sub-task when done
    if topic_task is not None:
        progress.remove_task(topic_task)

    # Now delete the namespace
    result = subprocess.run(
        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
         "bin/pulsar-admin", "namespaces", "delete", ns],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode == 0:
        return NamespaceDeleteResult(
            namespace=ns,
            success=True,
            topics_deleted=topics_deleted,
            topics_failed=topics_failed
        )
    else:
        error_msg = result.stderr.strip()
        # Filter out "Defaulted container" warnings
        error_lines = [line for line in error_msg.split('\n')
                      if 'Defaulted container' not in line]
        clean_error = '\n'.join(error_lines).strip()
        return NamespaceDeleteResult(
            namespace=ns,
            success=False,
            topics_deleted=topics_deleted,
            topics_failed=topics_failed,
            error=clean_error
        )


def cleanup_pulsar_namespaces(pattern: str = "omb-test-*", dry_run: bool = False, max_workers: int = 5) -> None:
    """
    Clean up Pulsar namespaces matching a pattern (parallel deletion).

    Args:
        pattern: Glob pattern for namespace names to delete (default: omb-test-*)
        dry_run: If True, only list namespaces without deleting
        max_workers: Number of parallel deletion workers (default: 5)
    """
    console.print(f"\n[cyan]Looking for Pulsar namespaces matching:[/cyan] public/{pattern}")
    console.print("=" * 60)

    # List all namespaces in public tenant
    result = subprocess.run(
        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
         "bin/pulsar-admin", "namespaces", "list", "public"],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        console.print(f"[red]Error listing namespaces:[/red] {result.stderr}")
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
        console.print(f"[yellow]No namespaces found matching pattern:[/yellow] {pattern}")
        return

    console.print(f"\n[green]Found {len(namespaces)} namespace(s)[/green] to {'delete' if not dry_run else 'list'}:\n")
    for ns in namespaces:
        console.print(f"  • {ns}")

    if dry_run:
        console.print("\n[yellow][DRY RUN][/yellow] No changes made. Run without --dry-run to delete.")
        return

    console.print(f"\n{'='*60}")
    confirm = console.input(f"[bold]Delete {len(namespaces)} namespace(s)?[/bold] (yes/no): ")
    if confirm.lower() != 'yes':
        console.print("[yellow]Cancelled.[/yellow]")
        return

    console.print(f"\n[cyan]Deleting namespaces with {max_workers} parallel workers...[/cyan]\n")

    # Track results
    results: List[NamespaceDeleteResult] = []
    errors: List[NamespaceDeleteResult] = []

    # Use Rich progress bar with parallel execution
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
        transient=False
    ) as progress:
        main_task = progress.add_task("[cyan]Deleting namespaces...", total=len(namespaces))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all deletion tasks, passing progress for sub-task tracking
            future_to_ns = {
                executor.submit(_delete_single_namespace, ns, progress): ns
                for ns in namespaces
            }

            # Process results as they complete
            for future in as_completed(future_to_ns):
                ns = future_to_ns[future]
                try:
                    result = future.result()
                    results.append(result)

                    if result.success:
                        progress.console.print(
                            f"  [green]✓[/green] {result.namespace} "
                            f"[dim](topics: {result.topics_deleted} deleted, {result.topics_failed} failed)[/dim]"
                        )
                    else:
                        errors.append(result)
                        progress.console.print(
                            f"  [red]✗[/red] {result.namespace}: {result.error}"
                        )

                except Exception as e:
                    errors.append(NamespaceDeleteResult(
                        namespace=ns,
                        success=False,
                        topics_deleted=0,
                        topics_failed=0,
                        error=str(e)
                    ))
                    progress.console.print(f"  [red]✗[/red] {ns}: {e}")

                progress.advance(main_task)

    # Summary
    deleted = sum(1 for r in results if r.success)
    failed = len(errors)
    total_topics_deleted = sum(r.topics_deleted for r in results)
    total_topics_failed = sum(r.topics_failed for r in results)

    console.print(f"\n{'='*60}")
    console.print(f"[bold]Summary:[/bold]")
    console.print(f"  Namespaces: [green]{deleted} deleted[/green], [red]{failed} failed[/red]")
    console.print(f"  Topics:     [green]{total_topics_deleted} deleted[/green], [red]{total_topics_failed} failed[/red]")

    if errors:
        console.print(f"\n[red]Failed namespaces:[/red]")
        for err in errors:
            console.print(f"  • {err.namespace}: {err.error}")
