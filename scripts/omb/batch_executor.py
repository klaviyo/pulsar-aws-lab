"""
Batch mode execution for OMB tests.

Handles running multiple test stages in a single Kubernetes Job for efficiency.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from rich.live import Live

from .batch_script import render_batch_script
from .metrics import extract_current_rate_from_logs, format_rate_status

logger = logging.getLogger(__name__)


class BatchExecutor:
    """
    Executes OMB test plans in batch mode.

    Batch mode runs all test stages in a single Kubernetes Job,
    avoiding the overhead of creating/destroying Jobs per stage.
    """

    def __init__(
        self,
        experiment_id: str,
        experiment_dir: Path,
        namespace: str,
        worker_manager,
        manifest_builder,
        run_command_func: Callable,
        add_status_func: Callable,
        create_layout_func: Callable
    ):
        self.experiment_id = experiment_id
        self.experiment_dir = experiment_dir
        self.namespace = namespace
        self.worker_manager = worker_manager
        self.manifest_builder = manifest_builder
        self.run_command = run_command_func
        self._add_status = add_status_func
        self._create_layout = create_layout_func

    def is_batch_compatible(self, test_plan: Dict) -> bool:
        """
        Check if test plan is eligible for batch mode execution.

        Criteria:
        - All test_runs must have same num_workers
        - All test_runs must be fixed_rate type
        - Must have more than 1 test_run (otherwise no benefit)
        - batch_mode.enabled is not explicitly False
        """
        test_runs = test_plan.get('test_runs', [])

        if len(test_runs) <= 1:
            return False

        batch_config = test_plan.get('batch_mode', {})
        if batch_config.get('enabled') is False:
            return False

        # Check all runs have same worker count and are fixed_rate
        first_workers = test_runs[0].get('num_workers', 3)
        for run in test_runs:
            if run.get('type') != 'fixed_rate':
                return False
            if run.get('num_workers', 3) != first_workers:
                return False

        return True

    def generate_batch_workloads(
        self,
        test_plan: Dict,
        generate_workload_func: Callable
    ) -> List[Tuple[str, Dict, int]]:
        """
        Generate all workload configurations for batch mode.

        Args:
            test_plan: Parsed test plan dictionary
            generate_workload_func: Function to generate workload from base + overrides

        Returns:
            List of (stage_id, workload_dict, target_rate) tuples
        """
        workloads = []
        base_workload = test_plan['base_workload']

        for idx, test_run in enumerate(test_plan['test_runs']):
            stage_id = f"{idx+1:03d}-{test_run['name']}"
            workload = generate_workload_func(base_workload, test_run)
            target_rate = test_run.get('producer_rate', 0)
            workloads.append((stage_id, workload, target_rate))

        return workloads

    def collect_batch_results(
        self,
        batch_name: str,
        workloads: List[Tuple[str, Dict, int]]
    ) -> Dict[str, Dict]:
        """
        Collect results from batch Job pod.

        Uses kubectl cp to retrieve result files from the pod.
        """
        results = {}
        results_dir = self.experiment_dir / "benchmark_results"
        results_dir.mkdir(exist_ok=True)

        # Get pod name for the batch job
        try:
            result = self.run_command(
                ["kubectl", "get", "pods", "-n", self.namespace,
                 "-l", f"job-name=omb-batch-{batch_name}",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                "Get batch pod name",
                capture_output=True,
                check=True
            )
            pod_name = result.stdout.strip()
        except Exception as e:
            logger.error(f"Could not find pod for batch job {batch_name}: {e}")
            return results

        if not pod_name:
            logger.error(f"No pod found for batch job {batch_name}")
            return results

        # Copy results from pod for each completed stage
        for stage_id, workload, target_rate in workloads:
            try:
                source_path = f"/results/{self.experiment_id}/{stage_id}.json"
                dest_path = results_dir / f"{stage_id}.json"

                self.run_command(
                    ["kubectl", "cp",
                     f"{self.namespace}/{pod_name}:{source_path}",
                     str(dest_path)],
                    f"Copy results for stage {stage_id}",
                    check=False
                )

                if dest_path.exists():
                    with open(dest_path, 'r') as f:
                        data = json.load(f)
                    results[stage_id] = {
                        'data': data,
                        'target_rate': target_rate
                    }
                    logger.info(f"Collected results for stage {stage_id}")

                    # Save workload config for report generator
                    workload_config_path = results_dir / f"{stage_id}_workload.json"
                    workload_config = {
                        'workload': workload
                    }
                    with open(workload_config_path, 'w') as wf:
                        json.dump(workload_config, wf, indent=2)
                    logger.debug(f"Saved workload config for {stage_id}")
            except Exception as e:
                logger.warning(f"Failed to collect results for stage {stage_id}: {e}")

        return results

    def run_batch_tests(
        self,
        test_plan: Dict,
        live: Live,
        generate_workload_func: Callable
    ) -> None:
        """
        Execute a test plan in batch mode.

        Steps:
        1. Generate all workloads upfront
        2. Create single batch ConfigMap
        3. Ensure workers are ready (once)
        4. Create and run single batch Job
        5. Monitor Job completion
        6. Collect all results
        7. Cleanup resources
        """
        batch_name = test_plan['name'].replace(' ', '-').lower()
        num_workers = test_plan['test_runs'][0].get('num_workers', 3)
        plateau_config = test_plan.get('plateau_detection', {})

        logger.info(f"Running batch mode for: {batch_name}")
        logger.info(f"Stages: {len(test_plan['test_runs'])}")

        self._add_status(f"Starting batch mode: {len(test_plan['test_runs'])} stages", 'info')
        live.update(self._create_layout())

        # Step 1: Generate all workloads
        workloads = self.generate_batch_workloads(test_plan, generate_workload_func)
        self._add_status(f"Generated {len(workloads)} workload configurations", 'success')
        live.update(self._create_layout())

        # Step 2: Create batch ConfigMap
        configmap_yaml = self.manifest_builder.build_batch_configmap(batch_name, workloads)
        configmap_file = self.experiment_dir / f"batch_configmap_{batch_name}.yaml"
        with open(configmap_file, 'w') as f:
            f.write(configmap_yaml)

        self._add_status("Creating batch ConfigMap...", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(configmap_file)],
            f"Apply batch ConfigMap for {batch_name}"
        )
        self._add_status("Batch ConfigMap created", 'success')
        live.update(self._create_layout())

        # Step 3: Ensure workers (ONCE for entire batch)
        self._add_status(f"Ensuring {num_workers} workers are ready...", 'info')
        live.update(self._create_layout())
        try:
            self.worker_manager.ensure_workers(num_workers)
            self._add_status("Workers ready", 'success')
            live.update(self._create_layout())

            # Single grace period for worker warmup
            self._add_status("Waiting 30s for workers to fully initialize...", 'info')
            live.update(self._create_layout())
            for i in range(30):
                progress = (i + 1) / 30 * 100
                self._add_status(f"Worker startup: {i+1}/30s ({progress:.0f}%)", 'info')
                live.update(self._create_layout())
                time.sleep(1)
            self._add_status("Worker startup complete", 'success')
            live.update(self._create_layout())
        except Exception as e:
            raise RuntimeError(f"Failed to ensure workers: {e}")

        # Step 4: Create and run batch Job
        worker_addresses = self.worker_manager.get_worker_addresses(num_workers)
        workers_list = ",".join(worker_addresses)
        bash_script = render_batch_script(self.experiment_id, workers_list, plateau_config)
        job_yaml = self.manifest_builder.build_batch_job(batch_name, num_workers, bash_script)
        job_file = self.experiment_dir / f"batch_job_{batch_name}.yaml"
        with open(job_file, 'w') as f:
            f.write(job_yaml)

        self._add_status("Starting batch Job...", 'info')
        live.update(self._create_layout())
        self.run_command(
            ["kubectl", "apply", "-f", str(job_file)],
            f"Create batch Job for {batch_name}"
        )
        self._add_status("Batch Job started", 'success')
        live.update(self._create_layout())

        # Step 5: Monitor Job completion
        warmup_min = test_plan['base_workload'].get('warmup_duration_minutes', 1)
        test_min = test_plan['base_workload'].get('test_duration_minutes', 3)
        stage_duration_sec = (warmup_min + test_min) * 60
        total_expected_sec = stage_duration_sec * len(workloads)
        timeout_seconds = total_expected_sec + (15 * 60)  # Add 15min buffer

        self._add_status(f"Monitoring batch Job (timeout: {timeout_seconds//60}min)...", 'info')
        live.update(self._create_layout())

        start_time = time.time()
        stages_completed = 0
        current_stage = None

        while time.time() - start_time < timeout_seconds:
            result = self.run_command(
                ["kubectl", "get", "job", f"omb-batch-{batch_name}",
                 "-n", self.namespace,
                 "-o", "jsonpath={.status.succeeded},{.status.failed}"],
                "Check batch job status",
                capture_output=True,
                check=False
            )

            status = result.stdout.strip()
            succeeded, failed = status.split(',') if ',' in status else ('', '')

            if succeeded == '1':
                self._add_status("Batch Job completed successfully", 'success')
                live.update(self._create_layout())
                break
            elif failed == '1':
                self._add_status("Batch Job failed", 'error')
                live.update(self._create_layout())
                break

            # Try to get current stage from logs
            current_rate = None
            try:
                log_result = self.run_command(
                    ["kubectl", "logs", "-n", self.namespace,
                     "-l", f"job-name=omb-batch-{batch_name}",
                     "--tail=2000"],
                    "Get batch job logs",
                    capture_output=True,
                    check=False
                )
                logs = log_result.stdout

                # Count COMPLETED stages
                completed_matches = re.findall(r'Stage (\S+) completed successfully', logs)
                if completed_matches:
                    stages_completed = len(completed_matches)

                # Check for currently running stage
                current_stage_match = re.findall(r'STAGE: (\S+)', logs)
                current_stage = current_stage_match[-1] if current_stage_match else None

                # Extract current rate from logs
                current_rate = extract_current_rate_from_logs(logs, current_stage)

                # Check for plateau detection
                if 'PLATEAU DETECTED' in logs:
                    self._add_status(f"Plateau detected at stage {stages_completed}", 'success')
                    live.update(self._create_layout())

                # Check if batch execution is complete
                if 'BATCH EXECUTION COMPLETE' in logs:
                    self._add_status("Batch execution complete, collecting results...", 'success')
                    live.update(self._create_layout())
                    break
            except Exception as e:
                logger.debug(f"Error getting batch logs: {e}")

            # Get target rate for current stage
            target_rate = next((rate for stage_id, _, rate in workloads if stage_id == current_stage), 0)

            if current_stage:
                status_msg = format_rate_status(f"Running: {current_stage}", target_rate, current_rate)
                self._add_status(status_msg, 'info')
            else:
                self._add_status(
                    f"Running batch... {stages_completed}/{len(workloads)} completed",
                    'info'
                )
            live.update(self._create_layout())
            time.sleep(10)

        # Step 6: Collect results
        self._add_status("Collecting batch results...", 'info')
        live.update(self._create_layout())

        results = self.collect_batch_results(batch_name, workloads)
        self._add_status(f"Collected {len(results)} stage results", 'success')
        live.update(self._create_layout())

        # Step 7: Generate report
        self._add_status("Generating report...", 'info')
        live.update(self._create_layout())

        try:
            from report_generator import ReportGenerator
            report_gen = ReportGenerator(self.experiment_dir, self.experiment_id)

            results_dir = self.experiment_dir / "benchmark_results"
            # Exclude _workload.json files (config files, not results)
            result_files = [
                f for f in results_dir.glob("*.json")
                if not f.name.endswith('_workload.json')
            ]

            if result_files:
                report_config = {
                    'test_plan': test_plan,
                    'namespace': self.namespace,
                    'experiment_id': self.experiment_id
                }
                report_gen.create_report_package(
                    results_files=result_files,
                    cost_data=None,
                    config=report_config,
                    include_raw_data=False,
                )
                self._add_status("Report generated", 'success')
            else:
                self._add_status("No result files found for report", 'warning')
        except Exception as e:
            logger.warning(f"Failed to generate report: {e}")
            self._add_status(f"Report generation failed: {e}", 'warning')
        live.update(self._create_layout())

        # Step 8: Cleanup
        self._add_status("Cleaning up batch resources...", 'info')
        live.update(self._create_layout())

        self.run_command(
            ["kubectl", "delete", "job", f"omb-batch-{batch_name}",
             "-n", self.namespace, "--wait=false"],
            f"Delete batch Job {batch_name}",
            check=False
        )
        self.run_command(
            ["kubectl", "delete", "configmap", f"omb-batch-{batch_name}",
             "-n", self.namespace],
            f"Delete batch ConfigMap {batch_name}",
            check=False
        )

        self._add_status("Batch cleanup complete", 'success')
        live.update(self._create_layout())

        # Log summary
        if results:
            throughputs = []
            for stage_id, result_data in results.items():
                data = result_data.get('data', {})
                publish_rates = data.get('publishRate', [])
                if publish_rates:
                    avg = sum(publish_rates) / len(publish_rates)
                    throughputs.append(avg)

            if throughputs:
                logger.info(f"Batch complete: {len(results)} stages, max throughput: {max(throughputs):,.0f} msgs/sec")
