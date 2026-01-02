"""
OpenMessaging Benchmark (OMB) management module.
"""

from .workers import WorkerManager
from .manifests import ManifestBuilder, indent_yaml
from .metrics import extract_avg_throughput, extract_current_rate_from_logs, format_rate_status
from .plateau import check_plateau, generate_bash_plateau_check
from .batch_script import render_batch_script
from .batch_executor import BatchExecutor

__all__ = [
    'WorkerManager',
    'ManifestBuilder',
    'indent_yaml',
    'extract_avg_throughput',
    'extract_current_rate_from_logs',
    'format_rate_status',
    'check_plateau',
    'generate_bash_plateau_check',
    'render_batch_script',
    'BatchExecutor',
]
