"""
Batch script generation - render Jinja2 template for batch mode execution.
"""

from pathlib import Path
from typing import Dict

from jinja2 import Environment, FileSystemLoader

from .plateau import generate_bash_plateau_check


TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_batch_script(
    experiment_id: str,
    workers_list: str,
    plateau_config: Dict
) -> str:
    """
    Render the batch runner bash script from Jinja2 template.

    Args:
        experiment_id: Unique experiment identifier
        workers_list: Comma-separated list of worker URLs
        plateau_config: Plateau detection configuration dict

    Returns:
        Rendered bash script string
    """
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("batch_runner.sh.j2")

    plateau_logic = generate_bash_plateau_check(plateau_config)

    return template.render(
        experiment_id=experiment_id,
        workers_list=workers_list,
        plateau_logic=plateau_logic
    )
