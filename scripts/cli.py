"""
CLI argument parsing for Pulsar OMB Orchestrator.
"""

import argparse
from pathlib import Path


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description="Pulsar OMB Load Testing Orchestrator\n\n"
                    "Run OpenMessaging Benchmark tests against existing Pulsar clusters.\n"
                    "NOTE: Pulsar deployment must be managed externally.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Run tests command
    run_parser = subparsers.add_parser("run", help="Run benchmark tests")
    run_parser.add_argument("--test-plan", type=Path, required=True, help="Test plan file")
    run_parser.add_argument("--experiment-id", help="Experiment ID (auto-generated if not provided)")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--experiment-id", default="latest", help="Experiment ID (default: latest)")

    # List command
    list_parser = subparsers.add_parser("list", help="List experiments")

    # Cleanup workers command
    cleanup_workers_parser = subparsers.add_parser("cleanup-workers", help="Delete persistent worker pods")
    cleanup_workers_parser.add_argument("--namespace", default="omb", help="Kubernetes namespace (default: omb)")

    # Cleanup Pulsar namespaces command
    cleanup_pulsar_parser = subparsers.add_parser("cleanup-pulsar", help="Delete Pulsar namespaces matching a pattern")
    cleanup_pulsar_parser.add_argument("--pattern", default="omb-test-*", help="Namespace pattern to match (default: omb-test-*)")
    cleanup_pulsar_parser.add_argument("--dry-run", action="store_true", help="List namespaces without deleting")

    return parser


def parse_args():
    """Parse command line arguments."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return None

    return args
