"""
Terminal UI components for Pulsar OMB Orchestrator.
"""

from datetime import datetime
from typing import Dict, List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class OrchestratorUI:
    """Manages terminal UI for orchestrator."""

    def __init__(self, experiment_id: str, namespace: str, pulsar_tenant_namespace: str):
        self.console = Console()
        self.experiment_id = experiment_id
        self.namespace = namespace
        self.pulsar_tenant_namespace = pulsar_tenant_namespace
        self.status_messages: List[Dict[str, str]] = []
        self.current_test: Optional[Dict] = None
        self.grafana_url: Optional[str] = None

    def add_status(self, message: str, level: str = 'info') -> None:
        """Add a status message to the log."""
        self.status_messages.append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message,
            'level': level
        })

    def set_current_test(self, test: Optional[Dict]) -> None:
        """Set the currently running test."""
        self.current_test = test

    def set_grafana_url(self, url: str) -> None:
        """Set the Grafana dashboard URL."""
        self.grafana_url = url

    def set_pulsar_namespace(self, namespace: str) -> None:
        """Update the Pulsar tenant/namespace (after detection)."""
        self.pulsar_tenant_namespace = namespace

    def create_layout(self) -> Layout:
        """Create the split-pane layout (horizontal split: metadata on top, status on bottom)."""
        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=1),
            Layout(name="bottom", ratio=2)
        )

        layout["top"].update(self._create_metadata_panel())
        layout["bottom"].update(self._create_status_panel())

        return layout

    def _create_metadata_panel(self) -> Panel:
        """Create static metadata panel."""
        # Experiment info
        exp_table = Table(show_header=False, box=None, padding=(0, 1))
        exp_table.add_column("Key", style="bold cyan", width=18)
        exp_table.add_column("Value", style="white")

        exp_table.add_row("Experiment ID", self.experiment_id)
        exp_table.add_row("K8s Namespace", self.namespace)
        exp_table.add_row("Pulsar K8s NS", "pulsar")
        exp_table.add_row("Pulsar Tenant/NS", self.pulsar_tenant_namespace)

        # Test info
        if self.current_test:
            test_table = Table(
                show_header=False, box=None, padding=(0, 1),
                title="[bold yellow]Current Test[/bold yellow]",
                title_justify="left"
            )
            test_table.add_column("Key", style="bold yellow", width=18)
            test_table.add_column("Value", style="white")

            test_table.add_row("Test Name", self.current_test.get('name', 'N/A'))
            test_table.add_row("Workers", str(self.current_test.get('workers', 'N/A')))
            test_table.add_row("Type", self.current_test.get('type', 'N/A'))

            content = Table.grid()
            content.add_row(exp_table)
            content.add_row("")
            content.add_row(test_table)
        else:
            content = exp_table

        # Monitoring info with Grafana link
        if self.grafana_url:
            monitor_text = Text()
            monitor_text.append("\n\nMonitoring:\n", style="bold green")
            monitor_text.append(f"{self.grafana_url}\n", style="blue underline")

            final_content = Table.grid()
            final_content.add_row(content)
            final_content.add_row(monitor_text)
        else:
            final_content = content

        return Panel(
            final_content,
            title="[bold cyan]Experiment Info[/bold cyan]",
            border_style="cyan",
            padding=(1, 2)
        )

    def _create_status_panel(self) -> Panel:
        """Create live status panel."""
        if not self.status_messages:
            content = Text("Waiting for test to start...", style="dim italic")
        else:
            # Show last 20 status messages
            content = Text()
            for msg in self.status_messages[-20:]:
                timestamp = msg.get('time', '')
                message = msg.get('message', '')
                level = msg.get('level', 'info')

                style_map = {
                    'info': 'white',
                    'success': 'green',
                    'warning': 'yellow',
                    'error': 'red'
                }
                style = style_map.get(level, 'white')

                content.append(f"[dim]{timestamp}[/dim] ", style="dim")
                content.append(f"{message}\n", style=style)

        return Panel(
            content,
            title="[bold green]Status Log[/bold green]",
            border_style="green",
            padding=(1, 2)
        )
