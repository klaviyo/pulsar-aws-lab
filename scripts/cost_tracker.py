#!/usr/bin/env python3
"""
Cost Tracking Module
Tracks and reports AWS costs for Pulsar experiments using Cost Explorer API
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class CostTracker:
    """AWS Cost tracking and reporting"""

    def __init__(self, region: str = "us-west-2"):
        """Initialize cost tracker"""
        self.region = region
        self.ce_client = boto3.client('ce', region_name=region)
        self.ec2_client = boto3.client('ec2', region_name=region)

    def get_experiment_costs(
        self,
        experiment_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        Get costs for a specific experiment using tags

        Args:
            experiment_id: Experiment ID
            start_date: Start date for cost query (defaults to 7 days ago)
            end_date: End date for cost query (defaults to tomorrow)

        Returns:
            Dictionary with cost breakdown
        """
        logger.info(f"Fetching costs for experiment: {experiment_id}")

        # Default date range
        if not start_date:
            start_date = datetime.now() - timedelta(days=7)
        if not end_date:
            end_date = datetime.now() + timedelta(days=1)

        # Format dates for Cost Explorer API
        start = start_date.strftime('%Y-%m-%d')
        end = end_date.strftime('%Y-%m-%d')

        try:
            # Query costs by experiment tag
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    'Start': start,
                    'End': end
                },
                Granularity='DAILY',
                Filter={
                    'Tags': {
                        'Key': 'ExperimentID',
                        'Values': [experiment_id]
                    }
                },
                Metrics=['UnblendedCost', 'UsageQuantity'],
                GroupBy=[
                    {
                        'Type': 'DIMENSION',
                        'Key': 'SERVICE'
                    },
                    {
                        'Type': 'TAG',
                        'Key': 'Component'
                    }
                ]
            )

            return self._process_cost_response(response)

        except ClientError as e:
            logger.error(f"Error fetching costs: {e}")
            return {
                'total_cost': 0.0,
                'by_service': {},
                'by_component': {},
                'daily_costs': [],
                'error': str(e)
            }

    def _process_cost_response(self, response: Dict) -> Dict:
        """Process Cost Explorer API response"""
        total_cost = 0.0
        by_service = {}
        by_component = {}
        daily_costs = []

        for result in response.get('ResultsByTime', []):
            date = result['TimePeriod']['Start']
            daily_total = 0.0

            for group in result.get('Groups', []):
                cost = float(group['Metrics']['UnblendedCost']['Amount'])
                usage = float(group['Metrics']['UsageQuantity']['Amount'])

                # Extract service and component from keys
                keys = group.get('Keys', [])
                service = keys[0] if len(keys) > 0 else 'Unknown'
                component = keys[1] if len(keys) > 1 else 'Unknown'

                # Aggregate by service
                if service not in by_service:
                    by_service[service] = {'cost': 0.0, 'usage': 0.0}
                by_service[service]['cost'] += cost
                by_service[service]['usage'] += usage

                # Aggregate by component
                if component not in by_component:
                    by_component[component] = {'cost': 0.0, 'usage': 0.0}
                by_component[component]['cost'] += cost
                by_component[component]['usage'] += usage

                daily_total += cost
                total_cost += cost

            daily_costs.append({
                'date': date,
                'cost': daily_total
            })

        return {
            'total_cost': total_cost,
            'by_service': by_service,
            'by_component': by_component,
            'daily_costs': daily_costs
        }

    def get_instance_costs(self, instance_ids: List[str]) -> Dict:
        """
        Get costs for specific EC2 instances

        Args:
            instance_ids: List of EC2 instance IDs

        Returns:
            Dictionary with instance cost breakdown
        """
        logger.info(f"Fetching costs for {len(instance_ids)} instances")

        costs = {}
        for instance_id in instance_ids:
            try:
                # Get instance details
                response = self.ec2_client.describe_instances(
                    InstanceIds=[instance_id]
                )

                if response['Reservations']:
                    instance = response['Reservations'][0]['Instances'][0]
                    instance_type = instance['InstanceType']
                    state = instance['State']['Name']

                    # Get pricing information (simplified - actual pricing is complex)
                    # In production, use AWS Price List API
                    estimated_hourly_cost = self._estimate_instance_cost(instance_type)

                    costs[instance_id] = {
                        'instance_type': instance_type,
                        'state': state,
                        'estimated_hourly_cost': estimated_hourly_cost
                    }

            except ClientError as e:
                logger.error(f"Error fetching instance {instance_id}: {e}")
                costs[instance_id] = {'error': str(e)}

        return costs

    def _estimate_instance_cost(self, instance_type: str) -> float:
        """
        Estimate hourly cost for instance type (simplified)

        In production, use AWS Price List API for accurate pricing
        """
        # Simplified pricing estimates for common instance types (us-west-2)
        pricing = {
            't3.micro': 0.0104,
            't3.small': 0.0208,
            't3.medium': 0.0416,
            't3.large': 0.0832,
            't3.xlarge': 0.1664,
            'm5.large': 0.096,
            'm5.xlarge': 0.192,
            'c5.large': 0.085,
            'c5.xlarge': 0.17,
        }

        return pricing.get(instance_type, 0.1)  # Default estimate

    def estimate_experiment_cost(
        self,
        instance_counts: Dict[str, int],
        instance_types: Dict[str, str],
        duration_hours: float,
        storage_gb: int = 0,
        storage_type: str = "gp3"
    ) -> Dict:
        """
        Estimate total experiment cost before deployment

        Args:
            instance_counts: Dict of component -> count
            instance_types: Dict of component -> instance type
            duration_hours: Expected experiment duration in hours
            storage_gb: Total EBS storage in GB
            storage_type: EBS volume type

        Returns:
            Dictionary with cost estimates
        """
        logger.info("Estimating experiment costs")

        compute_cost = 0.0
        breakdown = {}

        # Calculate compute costs
        for component, count in instance_counts.items():
            instance_type = instance_types.get(component, 't3.small')
            hourly_cost = self._estimate_instance_cost(instance_type)
            component_cost = hourly_cost * count * duration_hours

            compute_cost += component_cost
            breakdown[component] = {
                'instance_type': instance_type,
                'count': count,
                'hourly_cost': hourly_cost,
                'total_cost': component_cost
            }

        # Calculate storage costs (simplified)
        storage_cost = self._estimate_storage_cost(storage_gb, storage_type, duration_hours)

        # Calculate data transfer (simplified estimate - 10% of compute)
        data_transfer_cost = compute_cost * 0.1

        total_cost = compute_cost + storage_cost + data_transfer_cost

        return {
            'total_estimated_cost': total_cost,
            'compute_cost': compute_cost,
            'storage_cost': storage_cost,
            'data_transfer_cost': data_transfer_cost,
            'breakdown': breakdown,
            'duration_hours': duration_hours
        }

    def _estimate_storage_cost(
        self,
        storage_gb: int,
        storage_type: str,
        duration_hours: float
    ) -> float:
        """Estimate EBS storage cost"""
        # Simplified pricing (us-west-2, per GB-month)
        monthly_pricing = {
            'gp2': 0.10,
            'gp3': 0.08,
            'io1': 0.125,
            'io2': 0.125,
        }

        monthly_cost_per_gb = monthly_pricing.get(storage_type, 0.10)
        hours_per_month = 730  # Average hours in a month

        return (storage_gb * monthly_cost_per_gb) * (duration_hours / hours_per_month)

    def generate_cost_report(
        self,
        experiment_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> str:
        """
        Generate formatted cost report

        Args:
            experiment_id: Experiment ID
            start_date: Start date
            end_date: End date

        Returns:
            Formatted cost report string
        """
        costs = self.get_experiment_costs(experiment_id, start_date, end_date)

        report = []
        report.append(f"\n{'='*60}")
        report.append(f"Cost Report for Experiment: {experiment_id}")
        report.append(f"{'='*60}\n")

        report.append(f"Total Cost: ${costs['total_cost']:.2f}\n")

        if costs.get('by_service'):
            report.append("Cost by Service:")
            for service, data in sorted(costs['by_service'].items(), key=lambda x: x[1]['cost'], reverse=True):
                report.append(f"  {service}: ${data['cost']:.2f}")
            report.append("")

        if costs.get('by_component'):
            report.append("Cost by Component:")
            for component, data in sorted(costs['by_component'].items(), key=lambda x: x[1]['cost'], reverse=True):
                report.append(f"  {component}: ${data['cost']:.2f}")
            report.append("")

        if costs.get('daily_costs'):
            report.append("Daily Costs:")
            for daily in costs['daily_costs']:
                report.append(f"  {daily['date']}: ${daily['cost']:.2f}")

        report.append(f"\n{'='*60}\n")

        return '\n'.join(report)


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: cost_tracker.py <experiment_id>")
        sys.exit(1)

    experiment_id = sys.argv[1]

    tracker = CostTracker()
    print(tracker.generate_cost_report(experiment_id))
