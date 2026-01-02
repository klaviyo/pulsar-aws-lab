"""
Plateau detection - detect when throughput has stopped improving.
"""

from typing import Dict, List


def check_plateau(
    throughput_history: List[float],
    min_improvement_percent: float,
    consecutive_steps_required: int
) -> bool:
    """
    Check if throughput has plateaued based on recent history.

    Args:
        throughput_history: List of achieved throughput values (msgs/sec)
        min_improvement_percent: Minimum improvement percentage to consider as "improvement"
        consecutive_steps_required: Number of consecutive steps without improvement to trigger plateau

    Returns:
        True if plateau detected, False otherwise
    """
    if len(throughput_history) < consecutive_steps_required + 1:
        return False

    # Get the baseline (best throughput before the last N steps)
    baseline_idx = len(throughput_history) - consecutive_steps_required - 1
    baseline = throughput_history[baseline_idx]

    # Check if all recent steps failed to improve beyond the threshold
    for i in range(consecutive_steps_required):
        recent_idx = baseline_idx + 1 + i
        recent = throughput_history[recent_idx]
        improvement = ((recent - baseline) / baseline) * 100 if baseline > 0 else 0

        if improvement > min_improvement_percent:
            # Found improvement, no plateau
            return False

    # No improvement in consecutive_steps_required steps
    return True


def generate_bash_plateau_check(plateau_config: Dict) -> str:
    """
    Generate bash code for plateau detection.

    This generates equivalent bash logic to the Python check_plateau function,
    for embedding in batch mode bash scripts.

    Args:
        plateau_config: Dict with 'enabled', 'min_improvement_percent', 'consecutive_steps_required'

    Returns:
        Bash code snippet for plateau detection, or empty string if disabled
    """
    if not plateau_config.get('enabled', False):
        return ""

    min_improvement = plateau_config.get('min_improvement_percent', 10.0)
    consecutive_required = plateau_config.get('consecutive_steps_required', 2)

    return f'''
    # PLATEAU DETECTION (matches Python check_plateau logic)
    if [ $stage_count -ge $(({consecutive_required} + 1)) ]; then
      # Get baseline (best throughput before last N steps)
      baseline_idx=$((stage_count - {consecutive_required} - 1))
      baseline=${{throughput_history[$baseline_idx]}}

      # Check if recent steps improved over baseline
      improved=false
      for ((i=0; i<{consecutive_required}; i++)); do
        recent_idx=$((baseline_idx + 1 + i))
        recent=${{throughput_history[$recent_idx]}}

        # Calculate improvement percentage (using awk instead of bc)
        if awk -v b="$baseline" 'BEGIN {{exit (b <= 0)}}'; then
          improvement=$(awk -v r="$recent" -v b="$baseline" 'BEGIN {{printf "%.2f", ((r - b) / b) * 100}}')
          if awk -v imp="$improvement" -v min="{min_improvement}" 'BEGIN {{exit (imp <= min)}}'; then
            improved=true
            break
          fi
        fi
      done

      if [ "$improved" = false ]; then
        echo ""
        echo "=============================================="
        echo "PLATEAU DETECTED!"
        echo "No improvement > {min_improvement}% for {consecutive_required} consecutive steps"
        echo "Max throughput achieved: $(printf '%s\\n' "${{throughput_history[@]}}" | sort -rn | head -1) msgs/sec"
        echo "=============================================="
        break  # Exit loop early
      fi
    fi'''
