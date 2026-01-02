#!/usr/bin/env python3
"""
Pulsar OMB Load Testing Orchestrator
Workflow controller for running OpenMessaging Benchmark tests against existing Pulsar clusters
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import boto3
import yaml
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

from tui import OrchestratorUI
from operations import cleanup_pulsar_namespaces, cleanup_pulsar_topics
from pulsar_manager import PulsarManager
from results_collector import ResultsCollector
from metrics_collector import MetricsCollector

# Import OMB modules
from omb.workers import WorkerManager
from omb.manifests import ManifestBuilder
from omb.metrics import extract_avg_throughput, extract_current_rate_from_logs, format_rate_status
from omb.plateau import check_plateau
from omb.batch_executor import BatchExecutor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project directories
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = PROJECT_ROOT / "results"

# Pulsar cluster connection details
# Updated to connect directly to brokers (bypassing proxy for better performance)
PULSAR_SERVICE_URL = "pulsar://pulsar-broker.pulsar.svc.cluster.local:6650"
PULSAR_HTTP_URL = "http://pulsar-broker.pulsar.svc.cluster.local:8080"
PULSAR_TEST_NAMESPACE = "public/omb-test"  # Namespace prefix for OMB test topics (OMB appends random suffix)


# OMB Docker image
DEFAULT_OMB_IMAGE = "439508887365.dkr.ecr.us-east-1.amazonaws.com/sre/pulsar-omb:latest"


class OrchestratorError(Exception):
    """Base exception for orchestrator errors"""
    pass


class Orchestrator:
    """Main orchestrator for OMB load testing against existing Pulsar clusters"""

    def __init__(self, experiment_id: Optional[str] = None, namespace: str = "omb", omb_image: Optional[str] = None):
        """
        Initialize orchestrator with experiment tracking.

        Args:
            experiment_id: Unique experiment identifier (auto-generated if not provided)
            namespace: Kubernetes namespace where OMB jobs will run (default: omb)
            omb_image: OMB Docker image to use (default: from DEFAULT_OMB_IMAGE)
        """
        self.experiment_id = experiment_id or f"exp-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.experiment_dir = RESULTS_DIR / self.experiment_id
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.pulsar_service_url = PULSAR_SERVICE_URL
        self.pulsar_http_url = PULSAR_HTTP_URL
        self.omb_image = omb_image or DEFAULT_OMB_IMAGE
        self.pulsar_tenant_namespace = PULSAR_TEST_NAMESPACE  # Will be updated with actual namespace after detection

        # Initialize TUI
        self.ui = OrchestratorUI(
            experiment_id=self.experiment_id,
            namespace=self.namespace,
            pulsar_tenant_namespace=self.pulsar_tenant_namespace
        )


        # Store test results from immediate collection
        self.test_results = ""

        # Initialize managers
        self.pulsar_manager = PulsarManager(
            pulsar_namespace=self.pulsar_tenant_namespace,
            run_command_func=self.run_command,
            add_status_func=self._add_status,
            create_layout_func=self._create_layout
        )

        self.results_collector = ResultsCollector(
            namespace=self.namespace,
            experiment_id=self.experiment_id,
            experiment_dir=self.experiment_dir,
            run_command_func=self.run_command
        )

        # Initialize worker manager for persistent worker pools
        self.worker_manager = WorkerManager(
            namespace=self.namespace,
            omb_image=self.omb_image,
            results_dir=self.experiment_dir
        )

        # Initialize manifest builder for YAML generation
        self.manifest_builder = ManifestBuilder(
            namespace=self.namespace,
            pulsar_service_url=self.pulsar_service_url,
            pulsar_http_url=self.pulsar_http_url,
            pulsar_tenant_namespace=self.pulsar_tenant_namespace,
            omb_image=self.omb_image,
            experiment_id=self.experiment_id,
            worker_manager=self.worker_manager
        )

        # Initialize metrics collector for infrastructure health tracking
        self.metrics_collector = MetricsCollector(
            namespace="pulsar",  # Pulsar components are in "pulsar" namespace
            experiment_dir=self.experiment_dir,
            run_command_func=self.run_command
        )

        # Initialize batch executor for batch mode tests
        self.batch_executor = BatchExecutor(
            experiment_id=self.experiment_id,
            experiment_dir=self.experiment_dir,
            namespace=self.namespace,
            worker_manager=self.worker_manager,
            manifest_builder=self.manifest_builder,
            run_command_func=self.run_command,
            add_status_func=self._add_status,
            create_layout_func=self._create_layout
        )

        # Ensure K8s namespace exists
        self._ensure_namespace_exists()

        # Ensure Pulsar namespace exists
        self.pulsar_manager.ensure_pulsar_namespace_exists()

        # Create/update "latest" symlink
        latest_link = RESULTS_DIR / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(self.experiment_dir)

        # Setup logging to file
        log_file = self.experiment_dir / "orchestrator.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(file_handler)

        logger.info(f"Initialized orchestrator for experiment: {self.experiment_id}")
        self._display_initial_info()

    @property
    def console(self):
        """Delegate console access to UI."""
        return self.ui.console

    @property
    def current_test(self):
        """Delegate current_test access to UI."""
        return self.ui.current_test

    @current_test.setter
    def current_test(self, value):
        """Delegate current_test setter to UI."""
        self.ui.set_current_test(value)

    def _display_initial_info(self) -> None:
        """Display initial experiment information"""
        table = Table(show_header=False, box=box.ROUNDED, border_style="cyan")
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")

        table.add_row("Experiment ID", self.experiment_id)
        table.add_row("OMB Namespace", self.namespace)
        table.add_row("Pulsar Namespace", "pulsar")
        table.add_row("Pulsar URL", self.pulsar_service_url)
        table.add_row("Results Directory", str(self.experiment_dir))

        self.console.print()
        self.console.print(Panel(table, title="[bold cyan]Experiment Configuration[/bold cyan]", border_style="cyan"))
        self.console.print()

    def _ensure_namespace_exists(self) -> None:
        """Ensure the K8s namespace exists, create if not."""
        result = self.run_command(
            ["kubectl", "get", "namespace", self.namespace],
            f"Check if K8s namespace {self.namespace} exists",
            capture_output=True,
            check=False
        )

        if result.returncode != 0:
            logger.info(f"Creating K8s namespace: {self.namespace}")
            self.run_command(
                ["kubectl", "create", "namespace", self.namespace],
                f"Create K8s namespace {self.namespace}"
            )
            logger.info(f"K8s namespace '{self.namespace}' created")
        else:
            logger.debug(f"K8s namespace '{self.namespace}' already exists")

    def _add_status(self, message: str, level: str = 'info') -> None:
        """Add a status message (delegates to UI)."""
        self.ui.add_status(message, level)

    def _create_layout(self):
        """Create the UI layout (delegates to UI)."""
        return self.ui.create_layout()


    def load_config(self, config_file: Path) -> Dict:
        """
        Load YAML configuration file.

        Args:
            config_file: Path to YAML configuration

        Returns:
            Parsed configuration dictionary
        """
        logger.info(f"Loading configuration from {config_file}")
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def run_command(
        self,
        cmd: List[str],
        description: str,
        capture_output: bool = False,
        check: bool = True,
        timeout: Optional[int] = None
    ) -> subprocess.CompletedProcess:
        """
        Run shell command with logging.

        Args:
            cmd: Command and arguments as list
            description: Human-readable description
            capture_output: Whether to capture stdout/stderr
            check: Whether to raise exception on non-zero exit
            timeout: Optional timeout in seconds

        Returns:
            CompletedProcess object

        Raises:
            OrchestratorError: If command fails and check=True
        """
        logger.info(f"Running: {description}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                check=check,
                timeout=timeout
            )
            if capture_output and result.stdout:
                logger.debug(f"Output: {result.stdout[:500]}")
            return result
        except subprocess.TimeoutExpired as e:
            error_msg = f"Command timed out after {timeout}s: {description}"
            logger.error(error_msg)
            if check:
                raise OrchestratorError(error_msg) from e
            # Return a dummy result for non-checked commands
            return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=str(e))
        except subprocess.CalledProcessError as e:
            error_msg = f"Command failed: {description}"
            if capture_output and e.stderr:
                error_msg += f"\nError: {e.stderr}"
            logger.error(error_msg)
            raise OrchestratorError(error_msg) from e



    def run_omb_job(self, test_config: Dict, workload_config: Dict, live: Live) -> str:
        """
        Run OpenMessaging Benchmark job with distributed workers.

        Args:
            test_config: Test run configuration
            workload_config: Workload specification
            live: Rich Live display instance

        Returns:
            Test results as JSON string

        Raises:
            OrchestratorError: If test execution fails
        """
        test_name = test_config['name']
        num_workers = test_config.get('num_workers', 3)  # Default to 3 workers
        target_rate = test_config.get('producer_rate', workload_config.get('producerRate', 0))
        logger.info(f"Running OMB test: {test_name} (with {num_workers} workers, target: {target_rate} msg/s)")

        # Set current test info for UI
        self.current_test = {
            'name': test_name,
            'workers': num_workers,
            'type': test_config.get('type', 'unknown')
        }

        self._add_status(f"Starting test: {test_name}", 'info')
        live.update(self._create_layout())

        # Ensure we have enough workers (persistent across all tests)
        self._add_status(f"Ensuring {num_workers} worker pods are available", 'info')
        live.update(self._create_layout())
        try:
            self.worker_manager.ensure_workers(num_workers)
            self._add_status(f"✓ Workers ready (persistent pool)", 'success')
            live.update(self._create_layout())

            # Give workers time to fully start up JVM and bind HTTP server
            self._add_status(f"Waiting 30s for workers to fully initialize...", 'info')
            live.update(self._create_layout())

            # Progress countdown for 30 second wait
            for i in range(30):
                progress = (i + 1) / 30 * 100
                self._add_status(f"Waiting for worker startup: {i+1}/30s ({progress:.0f}%)", 'info')
                live.update(self._create_layout())
                time.sleep(1)

            self._add_status(f"✓ Worker startup grace period complete", 'success')
            live.update(self._create_layout())
        except Exception as e:
            raise OrchestratorError(f"Failed to ensure workers: {e}")

        # Generate workload ConfigMap
        workload_yaml = self.manifest_builder.build_workload_configmap(test_name, workload_config)
        workload_file = self.experiment_dir / f"workload_{test_name}.yaml"

        with open(workload_file, 'w') as f:
            f.write(workload_yaml)

        # Apply workload ConfigMap
        self._add_status("Creating workload ConfigMap", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(workload_file)],
            f"Apply workload ConfigMap for {test_name}"
        )

        # Create OMB driver Job
        job_yaml = self.manifest_builder.build_driver_job(test_name, num_workers)
        job_file = self.experiment_dir / f"omb_job_{test_name}.yaml"

        with open(job_file, 'w') as f:
            f.write(job_yaml)

        # Collect baseline infrastructure metrics before test
        self._add_status("Collecting baseline infrastructure metrics...", 'info')
        live.update(self._create_layout())
        try:
            self.metrics_collector.collect_baseline_metrics()
            self._add_status("✓ Baseline metrics collected", 'success')
        except Exception as e:
            logger.warning(f"Failed to collect baseline metrics: {e}")
            self._add_status("⚠ Failed to collect baseline metrics", 'warning')
        live.update(self._create_layout())

        # Apply Job
        self._add_status("Starting driver Job", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(job_file)],
            f"Create OMB driver Job for {test_name}"
        )

        # Start background metrics collection
        self._add_status("Starting background metrics collection...", 'info')
        live.update(self._create_layout())
        try:
            self.metrics_collector.start_background_collection(interval_seconds=30)
            self._add_status("✓ Background metrics collection started", 'success')
        except Exception as e:
            logger.warning(f"Failed to start background metrics collection: {e}")
            self._add_status("⚠ Background metrics collection disabled", 'warning')
        live.update(self._create_layout())

        # Wait for Job pod to start and read logs to detect namespace
        self._add_status("Waiting for Job pod to start...", 'info')
        live.update(self._create_layout())

        # Wait for Job pod to be running and producing logs
        max_wait = 60  # 60 seconds
        wait_start = time.time()
        pod_running = False

        while time.time() - wait_start < max_wait:
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", "omb",
                 "-l", f"job-name=omb-{test_name}",
                 "-o", "jsonpath={.items[0].status.phase}"],
                "Check Job pod status",
                capture_output=True,
                check=False
            )

            if result.returncode == 0 and result.stdout.strip() == "Running":
                pod_running = True
                break

            time.sleep(2)

        if not pod_running:
            logger.warning("Job pod did not reach Running state within timeout")
            self._add_status("⚠ Job pod not running yet, may not detect namespace", 'warning')
            live.update(self._create_layout())
        else:
            # Wait additional time for OMB workers to initialize and create namespace
            self._add_status("Job running, waiting for worker initialization and namespace creation...", 'info')
            live.update(self._create_layout())
            # OMB workers need time to initialize PulsarBenchmarkDriver and create namespace
            # The driver logs "Created Pulsar namespace" during initialization on worker pods
            time.sleep(30)  # Increased from 15s to allow workers to fully initialize

        # Try to get namespace from worker pod logs (OMB logs namespace during driver initialization)
        self._add_status("Detecting Pulsar namespace from worker pod logs...", 'info')
        live.update(self._create_layout())

        detected_ns = self.pulsar_manager.detect_pulsar_namespace_from_logs(test_name, self.namespace)
        if detected_ns:
            self.pulsar_tenant_namespace = detected_ns
            self.pulsar_manager.pulsar_namespace = detected_ns
            self.ui.set_pulsar_namespace(detected_ns)  # Update TUI display
            self._add_status(f"✓ Pulsar namespace: {detected_ns}", 'success')
            logger.info(f"Using Pulsar namespace: {detected_ns}")
        else:
            # Fallback to topic-based detection with retry (wait for topics to be created)
            logger.warning("Could not detect namespace from logs, falling back to topic search")
            self._add_status("Waiting for topics to be created for namespace detection...", 'info')
            live.update(self._create_layout())

            # Retry topic detection for up to 60 seconds (topics should appear within warmup)
            max_retries = 12  # 12 * 5s = 60s
            for attempt in range(max_retries):
                detected_ns = self.pulsar_manager.detect_pulsar_namespace()
                if detected_ns:
                    self.pulsar_tenant_namespace = detected_ns
                    self.pulsar_manager.pulsar_namespace = detected_ns
                    self.ui.set_pulsar_namespace(detected_ns)  # Update TUI display
                    self._add_status(f"✓ Pulsar namespace: {detected_ns} (detected from topics)", 'success')
                    logger.info(f"Detected namespace with topics after {attempt + 1} attempts: {detected_ns}")
                    break

                if attempt < max_retries - 1:
                    logger.debug(f"No namespace with topics yet, retrying in 5s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(5)
            else:
                # After all retries, still couldn't detect
                self._add_status("⚠ Could not detect Pulsar namespace with topics", 'warning')
                logger.warning(f"Failed to detect namespace with topics after {max_retries} attempts")
        live.update(self._create_layout())

        # Wait for Job completion or failure
        self._add_status(f"Running benchmark test (this may take several minutes)...", 'info')
        live.update(self._create_layout())
        # Calculate expected test duration from workload config
        warmup_minutes = workload_config.get('warmupDurationMinutes', 1)
        test_minutes = workload_config.get('testDurationMinutes', 5)
        expected_duration_seconds = (warmup_minutes + test_minutes) * 60
        # When to start checking for the sleep message (test should be done)
        # Start checking 2 minutes before expected completion to avoid missing the 30s collection window
        check_sleep_after = max(60, expected_duration_seconds - 120)  # At least 60s into test, or 2min before end

        logger.info(f"Expected test duration: ~{warmup_minutes + test_minutes} minutes (warmup: {warmup_minutes}m, test: {test_minutes}m)")
        logger.info(f"Will start polling for sleep message after {check_sleep_after}s")

        # Poll Job status until complete or failed
        timeout_seconds = expected_duration_seconds + (10 * 60)  # Expected duration + 10min buffer
        start_time = time.time()
        poll_interval = 10  # Check Job status every 10 seconds
        log_poll_interval = 5  # Check logs more frequently when near completion

        job_succeeded = False
        job_failed = False
        results_collected = False

        while time.time() - start_time < timeout_seconds:
            result = self.run_command(
                ["kubectl", "get", "job", f"omb-{test_name}", "-n", self.namespace, "-o", "json"],
                f"Get Job {test_name} status",
                capture_output=True,
                check=False
            )

            if result.returncode == 0:
                job_status = json.loads(result.stdout)
                status = job_status.get('status', {})

                # Check for completion via succeeded/failed counts (more reliable than conditions)
                succeeded_count = status.get('succeeded', 0)
                failed_count = status.get('failed', 0)
                active_count = status.get('active', 0)

                if succeeded_count > 0:
                    job_succeeded = True
                    self._add_status(f"✓ Benchmark completed successfully", 'success')
                    live.update(self._create_layout())
                    logger.info(f"✓ Job {test_name} completed successfully (succeeded: {succeeded_count})")

                    # Results already collected during sleep window
                    if results_collected:
                        logger.info(f"Results already collected during sleep window")
                    else:
                        # Fallback: collect now if we somehow missed the sleep window
                        self._add_status("Collecting test results...", 'info')
                        live.update(self._create_layout())
                        logger.info(f"Collecting results for {test_name}...")
                        results = self.results_collector.collect_job_logs(test_name, success=True)

                        if results:
                            self._add_status(f"✓ Results collected ({len(results)} bytes)", 'success')
                            self.test_results = results
                        else:
                            self._add_status("⚠ No results data collected", 'warning')
                            self.test_results = ""
                        live.update(self._create_layout())

                    break
                elif failed_count > 0:
                    job_failed = True
                    self._add_status(f"✗ Benchmark failed", 'error')
                    live.update(self._create_layout())
                    logger.error(f"✗ Job {test_name} failed (failed: {failed_count})")
                    # Give pod a moment to fully terminate before collecting logs
                    time.sleep(2)
                    break

                # Still running - check if we should start polling for sleep message
                elapsed = int(time.time() - start_time)
                current_rate = None  # Will be populated from logs if available

                # Poll logs for current rate and (near completion) sleep message
                if active_count > 0:
                    # Check pod logs for the sleep message
                    pod_name_result = self.run_command(
                        ["kubectl", "get", "pods", "-n", self.namespace,
                         "-l", f"job-name=omb-{test_name}",
                         "-o", "jsonpath={.items[0].metadata.name}"],
                        f"Get pod name for {test_name}",
                        capture_output=True,
                        check=False
                    )

                    if pod_name_result.returncode == 0 and pod_name_result.stdout.strip():
                        pod_name = pod_name_result.stdout.strip()
                        # Get last 50 lines of logs to check for sleep message and current rate
                        log_result = self.run_command(
                            ["kubectl", "logs", pod_name, "-n", self.namespace, "--tail=50"],
                            f"Check logs for status",
                            capture_output=True,
                            check=False
                        )

                        # Extract current publish rate from logs for status display
                        if log_result.returncode == 0:
                            current_rate = extract_current_rate_from_logs(log_result.stdout)

                        # Only collect results when near expected completion
                        if elapsed >= check_sleep_after and not results_collected:
                            if log_result.returncode == 0 and "seconds to allow results collection" in log_result.stdout:
                                # Sleep message detected! Pod is in the collection window
                                logger.info(f"✓ Detected sleep message in logs - collecting results during 60s window")
                                self._add_status("Collecting test results (during sleep window)...", 'info')
                                live.update(self._create_layout())

                                results = self.results_collector.collect_job_logs(test_name, success=True)

                                if results:
                                    self._add_status(f"✓ Results collected ({len(results)} bytes)", 'success')
                                    self.test_results = results
                                    results_collected = True
                                    logger.info(f"✓ Results collected successfully during sleep window")
                                else:
                                    logger.warning(f"Failed to collect results during sleep window")

                                live.update(self._create_layout())

                # Log progress with rate info if available
                minutes = elapsed // 60
                seconds = elapsed % 60
                status = format_rate_status(f"[{minutes}m {seconds}s]", target_rate, current_rate)
                self._add_status(status, 'info')
                live.update(self._create_layout())
                logger.info(f"Job {test_name} still running... ({elapsed}s elapsed, active: {active_count}, succeeded: {succeeded_count}, failed: {failed_count})")

            # Use shorter poll interval when checking for sleep message
            if elapsed >= check_sleep_after and not results_collected:
                time.sleep(log_poll_interval)
            else:
                time.sleep(poll_interval)

        if not (job_succeeded or job_failed):
            logger.error(f"Timeout waiting for Job {test_name} after {timeout_seconds}s")
            self.results_collector.collect_job_logs(test_name, success=False)
            raise OrchestratorError(f"OMB test {test_name} timed out")

        if job_failed:
            self.results_collector.collect_job_logs(test_name, success=False)
            raise OrchestratorError(f"OMB test {test_name} failed")

        # Results were already collected immediately after Job succeeded
        # Use the stored results
        results = self.test_results

        # Stop background metrics collection and save timeseries
        self._add_status("Stopping metrics collection...", 'info')
        live.update(self._create_layout())
        try:
            self.metrics_collector.stop_background_collection()
            self.metrics_collector.collect_final_metrics()
            self.metrics_collector.export_metrics_for_plotting()
            self._add_status("✓ Infrastructure metrics saved", 'success')
        except Exception as e:
            logger.warning(f"Failed to finalize metrics collection: {e}")
            self._add_status("⚠ Metrics collection incomplete", 'warning')
        live.update(self._create_layout())

        # Cleanup Pulsar topics created during test
        self.pulsar_manager.cleanup_test_topics(live)

        # Cleanup ephemeral test resources (workers are persistent and reused)
        logger.info(f"Cleaning up test resources for {test_name}...")
        self.run_command(
            ["kubectl", "delete", "job", f"omb-{test_name}", "-n", self.namespace],
            f"Delete OMB driver Job {test_name}",
            check=False
        )
        self.run_command(
            ["kubectl", "delete", "configmap", f"omb-workload-{test_name}", "-n", self.namespace],
            f"Delete workload ConfigMap {test_name}",
            check=False
        )
        # Note: Workers are persistent and reused across tests - not deleted here

        return results

    def run_tests(self, test_plan_file: Path) -> None:
        """
        Execute test plan with OMB.

        Automatically detects if test plan is batch-compatible and uses
        batch mode for improved efficiency (single Job for all stages).

        Args:
            test_plan_file: Path to test plan YAML
        """
        logger.info("="*60)
        logger.info("RUNNING BENCHMARK TESTS")
        logger.info("="*60)

        test_plan = self.load_config(test_plan_file)

        # Create results directory
        results_dir = self.experiment_dir / "benchmark_results"
        results_dir.mkdir(exist_ok=True)

        # Check if batch mode is applicable
        if self.batch_executor.is_batch_compatible(test_plan):
            batch_config = test_plan.get('batch_mode', {})
            if batch_config.get('enabled', True):  # Default to enabled for compatible plans
                logger.info("="*60)
                logger.info("BATCH MODE ENABLED")
                logger.info(f"Test plan is batch-compatible ({len(test_plan['test_runs'])} stages)")
                logger.info("Running all stages in single Job for improved efficiency")
                logger.info("="*60)

                with Live(self._create_layout(), refresh_per_second=2, console=self.console) as live:
                    self.batch_executor.run_batch_tests(test_plan, live, self._generate_workload)
                return

        # Fall back to standard single-job-per-stage mode
        logger.info("Using standard single-job-per-stage mode")

        # Plateau detection configuration
        plateau_config = test_plan.get('plateau_detection', {})
        plateau_enabled = plateau_config.get('enabled', False)
        min_improvement_percent = plateau_config.get('min_improvement_percent', 2.0)
        consecutive_steps_required = plateau_config.get('consecutive_steps_required', 2)

        if plateau_enabled:
            logger.info(f"Plateau detection ENABLED:")
            logger.info(f"  - Min improvement threshold: {min_improvement_percent}%")
            logger.info(f"  - Consecutive steps required: {consecutive_steps_required}")

        # Track throughput history for plateau detection
        throughput_history: List[float] = []
        plateau_detected = False
        max_throughput = 0.0
        max_throughput_step = ""

        # Run tests with Rich Live display
        with Live(self._create_layout(), refresh_per_second=2, console=self.console) as live:
            # Run each test
            for idx, test_run in enumerate(test_plan['test_runs']):
                test_name = test_run['name']
                logger.info(f"\n{'='*60}")
                logger.info(f"Test {idx + 1}/{len(test_plan['test_runs'])}: {test_name}")
                logger.info(f"{'='*60}\n")

                # Generate workload
                workload = self._generate_workload(test_plan['base_workload'], test_run)

                # Run OMB job
                try:
                    # Run test (results are saved by results_collector.collect_job_logs())
                    self.run_omb_job(test_run, workload, live)

                    # Results are already saved by results_collector.collect_job_logs()
                    # to benchmark_results/{test_name}.json
                    result_file = results_dir / f"{test_name}.json"

                    self._add_status(f"✓ Test '{test_name}' completed", 'success')
                    live.update(self._create_layout())
                    logger.info(f"✓ Test '{test_name}' completed")

                    if result_file.exists():
                        logger.info(f"Results: {result_file}")

                        # Extract throughput for plateau detection
                        if plateau_enabled:
                            throughput = extract_avg_throughput(result_file)
                            if throughput is not None:
                                throughput_history.append(throughput)
                                logger.info(f"  Achieved throughput: {throughput:,.0f} msgs/sec")

                                # Track maximum throughput
                                if throughput > max_throughput:
                                    max_throughput = throughput
                                    max_throughput_step = test_name

                                # Check for plateau
                                if check_plateau(throughput_history, min_improvement_percent, consecutive_steps_required):
                                    plateau_detected = True
                                    logger.info("="*60)
                                    logger.info("PLATEAU DETECTED!")
                                    logger.info(f"Throughput has not improved by >{min_improvement_percent}% for {consecutive_steps_required} consecutive steps")
                                    logger.info(f"Maximum throughput achieved: {max_throughput:,.0f} msgs/sec (at step '{max_throughput_step}')")
                                    logger.info("Stopping test run early and generating report...")
                                    logger.info("="*60)
                                    self._add_status(f"🎯 Plateau detected at {max_throughput:,.0f} msgs/sec", 'success')
                                    live.update(self._create_layout())
                                    break
                    else:
                        logger.warning(f"Results file not found: {result_file}")

                except OrchestratorError as e:
                    self._add_status(f"✗ Test '{test_name}' failed: {e}", 'error')
                    live.update(self._create_layout())
                    logger.error(f"Test '{test_name}' failed: {e}")
                    continue

        if plateau_detected:
            logger.info(f"\n{'='*60}")
            logger.info(f"TEST RUN STOPPED - PLATEAU DETECTED")
            logger.info(f"Maximum sustained throughput: {max_throughput:,.0f} msgs/sec")
            logger.info(f"Achieved at step: {max_throughput_step}")
            logger.info(f"Results: {results_dir}")
            logger.info(f"{'='*60}\n")
        else:
            logger.info(f"\n{'='*60}")
            logger.info(f"ALL TESTS COMPLETED")
            if throughput_history:
                logger.info(f"Maximum throughput: {max(throughput_history):,.0f} msgs/sec")
            logger.info(f"Results: {results_dir}")
            logger.info(f"{'='*60}\n")

        # Generate HTML report using existing report generator
        self.console.print("\n[bold cyan]Generating test report...[/bold cyan]")

        from report_generator import ReportGenerator
        report_gen = ReportGenerator(self.experiment_dir, self.experiment_id)

        # Get all result files (filter out workload config files)
        result_files = [f for f in results_dir.glob("*.json") if not f.name.endswith("_workload.json")]

        if result_files:
            # Generate full report package with updated namespace info
            report_config = {
                'test_plan': test_plan,
                'namespace': self.namespace,
                'pulsar_namespace': self.pulsar_tenant_namespace,  # Use detected namespace
                'pulsar_service_url': self.pulsar_service_url,
                'experiment_id': self.experiment_id
            }

            report_dir = report_gen.create_report_package(
                results_files=result_files,
                cost_data=None,  # No cost data for test runs (only for full experiments)
                config=report_config,
                include_raw_data=False,  # Don't duplicate - files already in benchmark_results/
            )
            self.console.print(f"[bold green]✓ Report generated:[/bold green] {report_dir}\n")
            self.console.print(f"[dim]Raw results: {results_dir}[/dim]\n")
        else:
            logger.warning("No result files found to generate report")
            self.console.print("[yellow]⚠ No results to generate report[/yellow]\n")

        # Note: Workers are persistent across test runs - namespace is NOT cleaned up
        # Use 'python scripts/orchestrator.py cleanup-workers' to manually clean up workers
        # self.k8s_manager.cleanup_namespace()

    def _generate_workload(self, base: Dict, overrides: Dict) -> Dict:
        """Generate OMB workload from test plan"""
        workload = {
            'name': overrides.get('name', base['name']),
            'topics': overrides.get('workload_overrides', {}).get('topics', base['topics']),
            'partitionsPerTopic': overrides.get('workload_overrides', {}).get('partitions_per_topic', base['partitions_per_topic']),
            'messageSize': overrides.get('workload_overrides', {}).get('message_size', base['message_size']),
            'useRandomizedPayloads': True,
            'randomBytesRatio': 0,
            'randomizedPayloadPoolSize': 1,
            'subscriptionsPerTopic': base.get('subscriptions_per_topic', 1),
            'consumerPerSubscription': overrides.get('workload_overrides', {}).get('consumers_per_topic', base.get('consumers_per_topic', 1)),
            'producersPerTopic': overrides.get('workload_overrides', {}).get('producers_per_topic', base.get('producers_per_topic', 1)),
            'consumerBacklogSizeGB': base.get('consumer_backlog_size_gb', 0),
            'testDurationMinutes': overrides.get('workload_overrides', {}).get('test_duration_minutes', base.get('test_duration_minutes', 5)),
            'warmupDurationMinutes': overrides.get('workload_overrides', {}).get('warmup_duration_minutes', base.get('warmup_duration_minutes', 1)),
        }

        # Set producer rate based on test type
        if overrides['type'] == 'fixed_rate' and 'producer_rate' in overrides:
            workload['producerRate'] = overrides['producer_rate']
        elif overrides['type'] == 'max_rate':
            # producerRate: 0 means "produce at maximum possible rate" (saturation test)
            workload['producerRate'] = 0

        return workload


    def generate_report(self) -> None:
        """Generate comprehensive experiment report with metrics and costs"""
        logger.info("="*60)
        logger.info("GENERATING REPORT")
        logger.info("="*60)

        # Find all result files
        results_dir = self.experiment_dir / "benchmark_results"
        if not results_dir.exists():
            logger.error(f"Results directory not found: {results_dir}")
            logger.error("Run tests first using: orchestrator.py run --test-plan <file>")
            return

        # Filter out workload config files from result files
        result_files = [f for f in results_dir.glob("*.json") if not f.name.endswith("_workload.json")]
        if not result_files:
            logger.error(f"No result files found in {results_dir}")
            logger.error("Expected JSON files from OMB tests")
            return

        logger.info(f"Found {len(result_files)} result files")

        # Parse OMB results
        metrics = self.results_collector.parse_omb_results(result_files)

        # Load experiment configuration
        config_file = self.experiment_dir / "infrastructure.yaml"
        config = {}
        region = "us-east-1"  # Default region
        if config_file.exists():
            config = self.load_config(config_file)
            region = config.get('region', 'us-east-1')

        # Get cost data
        logger.info("Fetching AWS cost data...")
        from cost_tracker import CostTracker
        cost_tracker = CostTracker(region=region)
        cost_data = cost_tracker.get_experiment_costs(self.experiment_id)

        # Generate report package
        logger.info("Generating HTML report...")
        from report_generator import ReportGenerator
        generator = ReportGenerator(self.experiment_dir)

        report_dir = generator.create_report_package(
            results_files=result_files,
            cost_data=cost_data,
            config=config,
            include_raw_data=True
        )

        logger.info("="*60)
        logger.info("REPORT GENERATED")
        logger.info(f"  HTML: {report_dir}/index.html")
        logger.info(f"  CSV:  {report_dir}/metrics.csv")
        logger.info(f"  JSON: {report_dir}/metrics.json")
        logger.info("="*60)

    @staticmethod
    def resolve_experiment_id(experiment_id: str) -> str:
        """Resolve experiment ID, handling 'latest' shortcut"""
        if experiment_id == "latest":
            latest_link = RESULTS_DIR / "latest"
            if not latest_link.exists():
                raise OrchestratorError("No experiments found")
            return latest_link.resolve().name
        return experiment_id

    @staticmethod
    def list_experiments() -> None:
        """List all experiments with timestamps"""
        if not RESULTS_DIR.exists():
            print("No experiments found.")
            return

        experiments = sorted(
            [d for d in RESULTS_DIR.iterdir() if d.is_dir() and d.name.startswith("exp-")],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        if not experiments:
            print("No experiments found.")
            return

        print("\nAvailable Experiments:")
        print("=" * 60)
        for exp_dir in experiments:
            exp_id = exp_dir.name
            timestamp = datetime.fromtimestamp(exp_dir.stat().st_mtime)

            is_latest = ""
            latest_link = RESULTS_DIR / "latest"
            if latest_link.exists() and latest_link.resolve() == exp_dir:
                is_latest = " (latest)"

            print(f"{exp_id:30} {timestamp.strftime('%Y-%m-%d %H:%M:%S')}{is_latest}")
        print("=" * 60)


def main():
    """Main entry point"""
    from cli import parse_args

    args = parse_args()
    if args is None:
        sys.exit(1)

    try:
        # Handle list command
        if args.command == "list":
            Orchestrator.list_experiments()
            return

        # Handle cleanup-workers command (doesn't need experiment ID)
        if args.command == "cleanup-workers":
            from omb.workers import WorkerManager
            namespace = args.namespace
            worker_manager = WorkerManager(namespace=namespace, omb_image="", results_dir=Path("/tmp"))
            worker_manager.cleanup_workers()
            print(f"✓ Workers cleaned up in namespace '{namespace}'")
            return

        # Handle cleanup-pulsar command (doesn't need experiment ID)
        if args.command == "cleanup-pulsar":
            cleanup_pulsar_namespaces(pattern=args.pattern, dry_run=args.dry_run, max_workers=args.workers)
            return

        # Resolve experiment ID
        experiment_id = getattr(args, "experiment_id", None)
        if experiment_id:
            experiment_id = Orchestrator.resolve_experiment_id(experiment_id)

        orchestrator = Orchestrator(experiment_id)

        # Execute command
        if args.command == "run":
            orchestrator.run_tests(args.test_plan)
        elif args.command == "report":
            orchestrator.generate_report()

    except OrchestratorError as e:
        logger.error(f"Orchestrator error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
