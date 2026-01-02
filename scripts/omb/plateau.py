"""
Plateau detection - detect when achieved throughput deviates too far from target rate.
"""

from typing import Dict, List


def check_plateau(
    throughput_history: List[float],
    target_rates: List[float],
    allowed_deviation: float,
    consecutive_fails_allowed: int
) -> bool:
    """
    Check if throughput has plateaued based on deviation from target rate.

    A plateau is detected when the achieved throughput falls below the acceptable
    threshold (target * (1 - allowed_deviation/100)) for consecutive_fails_allowed
    consecutive steps.

    Args:
        throughput_history: List of achieved throughput values (msgs/sec)
        target_rates: List of target rates corresponding to each throughput measurement
        allowed_deviation: Maximum allowed deviation percentage from target rate
        consecutive_fails_allowed: Number of consecutive steps with deviation before triggering plateau

    Returns:
        True if plateau detected, False otherwise
    """
    if len(throughput_history) < consecutive_fails_allowed:
        return False

    if len(throughput_history) != len(target_rates):
        return False

    # Check last N steps for deviation from target
    for i in range(consecutive_fails_allowed):
        idx = len(throughput_history) - consecutive_fails_allowed + i
        achieved = throughput_history[idx]
        target = target_rates[idx]

        if target <= 0:
            # Skip invalid target rates
            return False

        # Calculate minimum acceptable throughput
        min_acceptable = target * (1 - allowed_deviation / 100)

        if achieved >= min_acceptable:
            # This step is within tolerance, no plateau
            return False

    # All consecutive steps exceeded deviation threshold
    return True


def generate_bash_plateau_check(plateau_config: Dict) -> str:
    """
    Generate bash code for plateau detection.

    This generates bash logic to compare achieved throughput against target rate,
    for embedding in batch mode bash scripts.

    Args:
        plateau_config: Dict with 'enabled', 'allowed_deviation', 'consecutive_fails_allowed'

    Returns:
        Bash code snippet for plateau detection, or empty string if disabled
    """
    if not plateau_config.get('enabled', False):
        return ""

    allowed_deviation = plateau_config.get('allowed_deviation', 10.0)
    consecutive_required = plateau_config.get('consecutive_fails_allowed', 2)

    return f'''
    # PLATEAU DETECTION (compare achieved vs target rate)
    if [ $stage_count -ge {consecutive_required} ]; then
      # Check if last N steps all deviated from target by more than {allowed_deviation}%
      all_deviated=true
      for ((i=0; i<{consecutive_required}; i++)); do
        idx=$((stage_count - {consecutive_required} + i))
        achieved=${{throughput_history[$idx]}}
        target=${{target_rates[$idx]}}

        # Calculate minimum acceptable throughput (using awk for floating-point)
        if awk -v t="$target" 'BEGIN {{exit (t <= 0)}}'; then
          min_acceptable=$(awk -v t="$target" -v d="{allowed_deviation}" 'BEGIN {{printf "%.2f", t * (1 - d / 100)}}')

          # Check if achieved >= min_acceptable
          if awk -v a="$achieved" -v m="$min_acceptable" 'BEGIN {{exit (a < m)}}'; then
            # This step is within tolerance
            all_deviated=false
            break
          fi
        fi
      done

      if [ "$all_deviated" = true ]; then
        echo ""
        echo "=============================================="
        echo "PLATEAU DETECTED!"
        echo "Achieved throughput deviated >{allowed_deviation}% from target for {consecutive_required} consecutive steps"
        echo "Max throughput achieved: $(printf '%s\\n' "${{throughput_history[@]}}" | sort -rn | head -1) msgs/sec"
        echo "=============================================="
        break  # Exit loop early
      fi
    fi'''
