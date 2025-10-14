"""
Results collection for OMB Orchestrator.
Handles collection of benchmark results and logs from OMB driver pods.
"""

import json
import logging
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class ResultsCollector:
    """Collects and processes OMB benchmark results."""

    def __init__(
        self,
        namespace: str,
        experiment_id: str,
        experiment_dir: Path,
        run_command_func: Callable
    ):
        """
        Initialize results collector.

        Args:
            namespace: Kubernetes namespace
            experiment_id: Experiment ID
            experiment_dir: Directory for experiment artifacts
            run_command_func: Function to run kubectl commands
        """
        self.namespace = namespace
        self.experiment_id = experiment_id
        self.experiment_dir = experiment_dir
        self.run_command = run_command_func

    def collect_job_logs(self, test_name: str, success: bool) -> str:
        """
        Collect logs and results from OMB Job pod.

        Args:
            test_name: Name of the test
            success: Whether the test succeeded

        Returns:
            JSON results as string
        """
        # Get Job pod name - retry a few times
        pod_name = ""
        for attempt in range(5):
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace,
                 "-l", f"job-name=omb-{test_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                f"Get Job pod for {test_name} (attempt {attempt + 1})",
                capture_output=True,
                check=False
            )
            pod_name = result.stdout.strip()
            if pod_name:
                break
            time.sleep(1)

        if not pod_name:
            logger.warning(f"Could not find pod for Job {test_name}")
            return ""

        logger.info(f"Found pod: {pod_name}")

        # Get and save pod logs
        log_result = self.run_command(
            ["kubectl", "logs", pod_name, "-n", self.namespace],
            f"Get logs for {test_name}",
            capture_output=True,
            check=False
        )
        logs = log_result.stdout

        log_file = self.experiment_dir / f"omb_{test_name}_{'success' if success else 'failed'}.log"
        with open(log_file, 'w') as f:
            f.write(logs)
        logger.info(f"Logs saved to: {log_file}")

        # Copy JSON results if test succeeded
        json_data = ""
        if success:
            results_dir = self.experiment_dir / "benchmark_results"
            results_dir.mkdir(exist_ok=True)

            result_file = results_dir / f"{test_name}.json"
            source_path = f"/results/{self.experiment_id}/{test_name}.json"

            # Try kubectl cp first (during 30s sleep window)
            logger.info(f"Attempting to copy results file:")
            logger.info(f"  Pod: {pod_name}")
            logger.info(f"  Source path: {source_path}")
            logger.info(f"  Destination: {result_file}")

            cp_result = self.run_command(
                ["kubectl", "cp", f"{self.namespace}/{pod_name}:{source_path}", str(result_file)],
                f"Copy results for {test_name}",
                check=False,
                capture_output=True
            )

            logger.info(f"kubectl cp result:")
            logger.info(f"  Return code: {cp_result.returncode}")
            if cp_result.stdout:
                logger.info(f"  Stdout: {cp_result.stdout}")
            if cp_result.stderr:
                logger.info(f"  Stderr: {cp_result.stderr}")
            logger.info(f"  File exists after copy: {result_file.exists()}")
            if result_file.exists():
                logger.info(f"  File size: {result_file.stat().st_size} bytes")

            if cp_result.returncode == 0 and result_file.exists() and result_file.stat().st_size > 0:
                logger.info(f"✓ Results copied successfully via kubectl cp")
                with open(result_file, 'r') as f:
                    json_data = f.read()
            else:
                # Fallback: extract from logs
                logger.warning(f"kubectl cp failed, falling back to log extraction...")
                json_data = self._extract_json_from_logs(logs, result_file)

        return json_data

    def _extract_json_from_logs(self, logs: str, result_file: Path) -> str:
        """
        Extract JSON results from pod logs.

        Args:
            logs: Pod log content
            result_file: Path to save extracted JSON

        Returns:
            JSON string or empty string
        """
        try:
            # Find JSON in logs - look for "Results saved to" marker
            json_start = logs.rfind('Results saved to ')
            if json_start != -1:
                remaining = logs[json_start:]
                brace_start = remaining.find('{')
                if brace_start != -1:
                    json_portion = remaining[brace_start:]
                    brace_end = json_portion.rfind('}')
                    if brace_end != -1:
                        json_data = json_portion[:brace_end + 1]

                        # Validate JSON
                        json.loads(json_data)

                        # Save to file
                        with open(result_file, 'w') as f:
                            f.write(json_data)
                        logger.info(f"✓ Extracted {len(json_data)} bytes of JSON from logs to: {result_file}")
                        return json_data
                    else:
                        logger.warning("Could not find closing brace in JSON output")
                else:
                    logger.warning("Could not find JSON start in logs")
            else:
                logger.warning("Could not find 'Results saved' marker in logs")
        except Exception as e:
            logger.warning(f"Error extracting JSON from logs: {e}")

        return ""

    def collect_pod_logs(self) -> None:
        """Collect logs from all pods for debugging."""
        logger.info("Collecting pod logs for troubleshooting...")

        result = self.run_command(
            ["kubectl", "get", "pods", "-n", self.namespace, "-o", "json"],
            "Get all pods",
            capture_output=True
        )

        pods = json.loads(result.stdout)

        logs_dir = self.experiment_dir / "pod_logs"
        logs_dir.mkdir(exist_ok=True)

        for pod in pods.get('items', []):
            pod_name = pod['metadata']['name']

            logger.info(f"Collecting logs from {pod_name}...")

            result = self.run_command(
                ["kubectl", "logs", pod_name, "-n", self.namespace, "--tail=1000"],
                f"Get logs from {pod_name}",
                capture_output=True,
                check=False
            )

            if result.returncode == 0:
                log_file = logs_dir / f"{pod_name}.log"
                with open(log_file, 'w') as f:
                    f.write(result.stdout)
                logger.debug(f"Saved logs to {log_file}")
            else:
                logger.warning(f"Failed to get logs from {pod_name}")

        logger.info(f"✓ Pod logs collected in {logs_dir}")

    def parse_omb_results(self, result_files: list) -> list:
        """
        Parse OMB JSON result files.

        Args:
            result_files: List of result file paths

        Returns:
            List of parsed result dictionaries
        """
        results = []

        for result_file in result_files:
            try:
                with open(result_file, 'r') as f:
                    data = json.load(f)
                    results.append(data)
            except Exception as e:
                logger.error(f"Error parsing {result_file}: {e}")

        return results
