"""
OMB metrics extraction - parse throughput and rates from OMB output.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def extract_avg_throughput(result_file: Path) -> Optional[float]:
    """
    Extract average publish rate (throughput) from OMB result file.

    Args:
        result_file: Path to the OMB JSON result file

    Returns:
        Average publish rate in msgs/sec, or None if extraction fails
    """
    try:
        with open(result_file, 'r') as f:
            data = json.load(f)

        # publishRate is an array of per-interval throughput values
        publish_rates = data.get('publishRate', [])
        if publish_rates:
            avg_rate = sum(publish_rates) / len(publish_rates)
            return avg_rate
        return None
    except Exception as e:
        logger.warning(f"Failed to extract throughput from {result_file}: {e}")
        return None


def extract_current_rate_from_logs(logs: str) -> Optional[float]:
    """
    Extract the most recent publish rate from live OMB logs.

    Parses log lines like:
    Pub rate 101926.1 msg/s / 49.8 MB/s | ...

    Returns:
        Most recent publish rate in msgs/sec, or None if not found
    """
    pattern = r'Pub rate\s+([\d.]+)\s+msg/s'
    matches = re.findall(pattern, logs)
    if matches:
        return float(matches[-1])
    return None


def format_rate_status(prefix: str, target_rate: float, current_rate: Optional[float]) -> str:
    """Format a status message with rate info if available."""
    if current_rate is not None:
        if target_rate > 0:
            rate_pct = (current_rate / target_rate) * 100
            return f"{prefix} | Target: {target_rate:,.0f} | Actual: {current_rate:,.0f} msg/s ({rate_pct:.0f}%)"
        else:
            return f"{prefix} | Actual: {current_rate:,.0f} msg/s (max rate)"
    return prefix
