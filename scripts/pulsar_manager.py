"""
Pulsar namespace and topic management for OMB Orchestrator.
Handles Pulsar tenant/namespace operations, topic cleanup, and namespace detection.
"""

import logging
import re
from typing import Optional, Callable

from rich.live import Live

logger = logging.getLogger(__name__)

# Default Pulsar test namespace
PULSAR_TEST_NAMESPACE = "public/omb-test"


class PulsarManager:
    """Manages Pulsar-specific operations."""

    def __init__(
        self,
        pulsar_namespace: str,
        run_command_func: Callable,
        add_status_func: Optional[Callable] = None,
        create_layout_func: Optional[Callable] = None
    ):
        """
        Initialize Pulsar manager.

        Args:
            pulsar_namespace: Pulsar tenant/namespace (e.g., 'public/omb-test')
            run_command_func: Function to run kubectl commands
            add_status_func: Optional function to add UI status messages
            create_layout_func: Optional function to create UI layout
        """
        self.pulsar_tenant_namespace = pulsar_namespace
        self.run_command = run_command_func
        self._add_status = add_status_func
        self._create_layout = create_layout_func

    def ensure_pulsar_namespace_exists(self) -> None:
        """Ensure the Pulsar tenant/namespace for tests exists."""
        # Check if namespace exists
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "list", "public"],
            f"List Pulsar namespaces in public tenant",
            capture_output=True,
            check=False
        )

        if result.returncode == 0:
            namespaces = [line.strip().strip('"') for line in result.stdout.strip().split('\n')
                         if line.strip() and line.strip().startswith('public/')]

            if self.pulsar_tenant_namespace in namespaces:
                logger.debug(f"Pulsar namespace '{self.pulsar_tenant_namespace}' already exists")
                return

        # Create the namespace
        logger.info(f"Creating Pulsar namespace: {self.pulsar_tenant_namespace}")
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "create", self.pulsar_tenant_namespace],
            f"Create Pulsar namespace {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        if result.returncode == 0:
            logger.info(f"✓ Pulsar namespace '{self.pulsar_tenant_namespace}' created")
        else:
            if "already exists" in result.stderr.lower():
                logger.debug(f"Pulsar namespace '{self.pulsar_tenant_namespace}' already exists")
            else:
                logger.warning(f"Failed to create Pulsar namespace: {result.stderr}")

    def detect_pulsar_namespace_from_logs(self, test_name: str, namespace: str = "omb") -> Optional[str]:
        """
        Detect Pulsar namespace by reading OMB driver Job logs.

        Args:
            test_name: Name of the test
            namespace: Kubernetes namespace

        Returns:
            Namespace string like 'public/omb-test-7Wv9Uqc' or None
        """
        try:
            # Get the pod name
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", namespace,
                 "-l", f"job-name=omb-{test_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                f"Get OMB driver pod for {test_name}",
                capture_output=True,
                check=False
            )

            if result.returncode != 0 or not result.stdout.strip():
                logger.warning("Could not find OMB driver pod")
                return None

            pod_name = result.stdout.strip()

            # Get logs
            result = self.run_command(
                ["kubectl", "logs", pod_name, "-n", namespace, "--tail=200"],
                f"Get logs from {pod_name}",
                capture_output=True,
                check=False
            )

            if result.returncode != 0:
                logger.warning(f"Could not get logs from pod {pod_name}")
                return None

            # Parse namespace from logs
            pattern = r'Creating.*topic.*persistent://(public/[^/]+)/'
            match = re.search(pattern, result.stdout)

            if match:
                detected_ns = match.group(1)
                logger.info(f"✓ Detected Pulsar namespace from logs: {detected_ns}")
                return detected_ns

            return None

        except Exception as e:
            logger.error(f"Error detecting namespace from logs: {e}")
            return None

    def detect_pulsar_namespace(self) -> Optional[str]:
        """
        Detect active Pulsar namespace matching omb-test pattern.

        Returns:
            Namespace string or None
        """
        logger.info("Detecting Pulsar namespace with omb-test pattern...")

        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "list", "public"],
            "List Pulsar namespaces",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.warning("Failed to list Pulsar namespaces")
            return None

        lines = result.stdout.strip().split('\n')
        omb_namespaces = []

        for line in lines:
            line = line.strip().strip('"')
            if line.startswith('public/omb-test') and 'Defaulted container' not in line:
                omb_namespaces.append(line)

        if omb_namespaces:
            detected_ns = omb_namespaces[0]
            logger.info(f"✓ Found Pulsar namespace: {detected_ns}")
            return detected_ns

        return None

    def cleanup_test_topics(self, live: Optional[Live] = None) -> None:
        """Delete all topics in the Pulsar test namespace."""
        if live and self._add_status and self._create_layout:
            self._add_status(f"Cleaning up topics in {self.pulsar_tenant_namespace}...", 'info')
            live.update(self._create_layout())

        logger.info(f"Cleaning up Pulsar topics in namespace '{self.pulsar_tenant_namespace}'...")

        # List topics
        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "list", self.pulsar_tenant_namespace],
            f"List topics in {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        if result.returncode != 0:
            logger.warning(f"Failed to list topics: {result.stderr}")
            if live and self._add_status and self._create_layout:
                self._add_status("⚠ Failed to list topics for cleanup", 'warning')
                live.update(self._create_layout())
            return

        # Parse topics
        topics = [line.strip() for line in result.stdout.strip().split('\n')
                 if line.strip() and line.strip().startswith('persistent://')
                 and 'Defaulted container' not in line]

        if not topics:
            logger.info(f"No topics to delete in '{self.pulsar_tenant_namespace}'")
            if live and self._add_status and self._create_layout:
                self._add_status("✓ No topics to clean up", 'success')
                live.update(self._create_layout())
            return

        logger.info(f"Found {len(topics)} topic(s) to delete")

        # Delete topics
        topics_deleted = 0
        for topic_url in topics:
            result = self.run_command(
                ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                 "bin/pulsar-admin", "topics", "delete", topic_url, "-f"],
                f"Delete topic {topic_url.split('/')[-1]}",
                check=False,
                capture_output=True
            )

            if result.returncode == 0:
                topics_deleted += 1
                logger.debug(f"  ✓ Deleted: {topic_url.split('/')[-1]}")
            else:
                logger.warning(f"  ✗ Failed to delete {topic_url.split('/')[-1]}: {result.stderr}")

        logger.info(f"✓ Deleted {topics_deleted}/{len(topics)} regular topic(s)")

        # List and delete partitioned topics (they don't show up in regular topics list)
        partitioned_result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "topics", "list-partitioned-topics", self.pulsar_tenant_namespace],
            f"List partitioned topics in {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        partitioned_deleted = 0
        if partitioned_result.returncode == 0:
            partitioned_topics = [line.strip() for line in partitioned_result.stdout.strip().split('\n')
                                 if line.strip() and line.strip().startswith('persistent://')
                                 and 'Defaulted container' not in line]

            if partitioned_topics:
                logger.info(f"Found {len(partitioned_topics)} partitioned topic(s) to delete")
                for topic_url in partitioned_topics:
                    result = self.run_command(
                        ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
                         "bin/pulsar-admin", "topics", "delete-partitioned-topic", topic_url, "-f"],
                        f"Delete partitioned topic {topic_url.split('/')[-1]}",
                        check=False,
                        capture_output=True
                    )

                    if result.returncode == 0:
                        partitioned_deleted += 1
                        logger.debug(f"  ✓ Deleted partitioned: {topic_url.split('/')[-1]}")
                    else:
                        logger.warning(f"  ✗ Failed to delete partitioned {topic_url.split('/')[-1]}: {result.stderr}")

                logger.info(f"✓ Deleted {partitioned_deleted}/{len(partitioned_topics)} partitioned topic(s)")

        total_deleted = topics_deleted + partitioned_deleted
        if live and self._add_status and self._create_layout:
            self._add_status(f"✓ Cleaned up {total_deleted} topic(s) ({topics_deleted} regular, {partitioned_deleted} partitioned)", 'success')
            live.update(self._create_layout())

        # Cleanup namespace
        self.cleanup_pulsar_namespace(live)

    def cleanup_pulsar_namespace(self, live: Optional[Live] = None) -> None:
        """Delete the Pulsar tenant/namespace."""
        if not self.pulsar_tenant_namespace or self.pulsar_tenant_namespace == PULSAR_TEST_NAMESPACE:
            logger.debug("No specific Pulsar namespace to clean up")
            return

        if live and self._add_status and self._create_layout:
            self._add_status(f"Deleting Pulsar namespace {self.pulsar_tenant_namespace}...", 'info')
            live.update(self._create_layout())

        logger.info(f"Deleting Pulsar namespace '{self.pulsar_tenant_namespace}'...")

        result = self.run_command(
            ["kubectl", "exec", "-n", "pulsar", "pulsar-broker-0", "--",
             "bin/pulsar-admin", "namespaces", "delete", self.pulsar_tenant_namespace],
            f"Delete Pulsar namespace {self.pulsar_tenant_namespace}",
            check=False,
            capture_output=True
        )

        if result.returncode == 0:
            logger.info(f"✓ Pulsar namespace '{self.pulsar_tenant_namespace}' deleted")
            if live and self._add_status and self._create_layout:
                self._add_status(f"✓ Pulsar namespace deleted", 'success')
                live.update(self._create_layout())
        else:
            logger.warning(f"Failed to delete Pulsar namespace: {result.stderr}")
            if live and self._add_status and self._create_layout:
                self._add_status("⚠ Failed to delete Pulsar namespace", 'warning')
                live.update(self._create_layout())
