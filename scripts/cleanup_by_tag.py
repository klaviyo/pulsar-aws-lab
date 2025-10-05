#!/usr/bin/env python3
"""
Emergency cleanup script - destroys AWS resources by ExperimentID tag
Use this when Terraform state is lost or corrupted
"""

import argparse
import sys
import boto3
from typing import List

def get_resources_by_experiment_id(experiment_id: str, region: str) -> dict:
    """Find all AWS resources tagged with the experiment ID"""
    ec2 = boto3.client('ec2', region_name=region)

    resources = {
        'instances': [],
        'volumes': [],
        'vpcs': [],
        'security_groups': [],
        'subnets': [],
        'internet_gateways': [],
        'route_tables': []
    }

    # Find instances
    instances = ec2.describe_instances(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            if instance['State']['Name'] != 'terminated':
                resources['instances'].append(instance['InstanceId'])

    # Find volumes
    volumes = ec2.describe_volumes(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for volume in volumes['Volumes']:
        resources['volumes'].append(volume['VolumeId'])

    # Find VPCs
    vpcs = ec2.describe_vpcs(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for vpc in vpcs['Vpcs']:
        resources['vpcs'].append(vpc['VpcId'])

    # Find security groups
    sgs = ec2.describe_security_groups(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for sg in sgs['SecurityGroups']:
        if sg['GroupName'] != 'default':
            resources['security_groups'].append(sg['GroupId'])

    # Find subnets
    subnets = ec2.describe_subnets(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for subnet in subnets['Subnets']:
        resources['subnets'].append(subnet['SubnetId'])

    # Find internet gateways
    igws = ec2.describe_internet_gateways(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for igw in igws['InternetGateways']:
        resources['internet_gateways'].append((igw['InternetGatewayId'], igw.get('Attachments', [])))

    # Find route tables
    rts = ec2.describe_route_tables(
        Filters=[{'Name': 'tag:ExperimentID', 'Values': [experiment_id]}]
    )
    for rt in rts['RouteTables']:
        # Skip main route table
        is_main = any(assoc.get('Main', False) for assoc in rt.get('Associations', []))
        if not is_main:
            resources['route_tables'].append(rt['RouteTableId'])

    return resources

def cleanup_resources(resources: dict, region: str, dry_run: bool = True):
    """Clean up AWS resources in the correct order"""
    ec2 = boto3.client('ec2', region_name=region)

    print("\n" + "=" * 60)
    if dry_run:
        print("DRY RUN - No resources will be deleted")
    else:
        print("DELETING RESOURCES")
    print("=" * 60 + "\n")

    # 1. Terminate instances
    if resources['instances']:
        print(f"Instances to terminate: {resources['instances']}")
        if not dry_run:
            ec2.terminate_instances(InstanceIds=resources['instances'])
            print("Waiting for instances to terminate...")
            waiter = ec2.get_waiter('instance_terminated')
            waiter.wait(InstanceIds=resources['instances'])
            print("Instances terminated.")

    # 2. Delete volumes
    if resources['volumes']:
        print(f"Volumes to delete: {resources['volumes']}")
        if not dry_run:
            for volume_id in resources['volumes']:
                try:
                    ec2.delete_volume(VolumeId=volume_id)
                    print(f"  Deleted volume: {volume_id}")
                except Exception as e:
                    print(f"  Error deleting volume {volume_id}: {e}")

    # 3. Detach and delete internet gateways
    if resources['internet_gateways']:
        for igw_id, attachments in resources['internet_gateways']:
            print(f"Internet Gateway: {igw_id}")
            if not dry_run:
                for attachment in attachments:
                    vpc_id = attachment['VpcId']
                    ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                    print(f"  Detached from VPC: {vpc_id}")
                ec2.delete_internet_gateway(InternetGatewayId=igw_id)
                print(f"  Deleted IGW: {igw_id}")

    # 4. Delete route tables
    if resources['route_tables']:
        print(f"Route tables to delete: {resources['route_tables']}")
        if not dry_run:
            for rt_id in resources['route_tables']:
                try:
                    ec2.delete_route_table(RouteTableId=rt_id)
                    print(f"  Deleted route table: {rt_id}")
                except Exception as e:
                    print(f"  Error deleting route table {rt_id}: {e}")

    # 5. Delete security groups
    if resources['security_groups']:
        print(f"Security groups to delete: {resources['security_groups']}")
        if not dry_run:
            for sg_id in resources['security_groups']:
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    print(f"  Deleted security group: {sg_id}")
                except Exception as e:
                    print(f"  Error deleting security group {sg_id}: {e}")

    # 6. Delete subnets
    if resources['subnets']:
        print(f"Subnets to delete: {resources['subnets']}")
        if not dry_run:
            for subnet_id in resources['subnets']:
                try:
                    ec2.delete_subnet(SubnetId=subnet_id)
                    print(f"  Deleted subnet: {subnet_id}")
                except Exception as e:
                    print(f"  Error deleting subnet {subnet_id}: {e}")

    # 7. Delete VPCs
    if resources['vpcs']:
        print(f"VPCs to delete: {resources['vpcs']}")
        if not dry_run:
            for vpc_id in resources['vpcs']:
                try:
                    ec2.delete_vpc(VpcId=vpc_id)
                    print(f"  Deleted VPC: {vpc_id}")
                except Exception as e:
                    print(f"  Error deleting VPC {vpc_id}: {e}")

    print("\nCleanup complete!" if not dry_run else "\nDry run complete!")

def main():
    parser = argparse.ArgumentParser(
        description="Emergency cleanup for Pulsar AWS Lab resources by ExperimentID tag"
    )
    parser.add_argument(
        "--experiment-id",
        required=True,
        help="Experiment ID to cleanup"
    )
    parser.add_argument(
        "--region",
        default="us-west-2",
        help="AWS region (default: us-west-2)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete resources (default is dry-run)"
    )

    args = parser.parse_args()

    print(f"Searching for resources with ExperimentID: {args.experiment_id}")
    print(f"Region: {args.region}\n")

    resources = get_resources_by_experiment_id(args.experiment_id, args.region)

    # Check if any resources found
    total_resources = sum(
        len(v) if isinstance(v, list) else 0
        for v in resources.values()
    )

    if total_resources == 0:
        print("No resources found with this ExperimentID.")
        return

    cleanup_resources(resources, args.region, dry_run=not args.execute)

    if not args.execute:
        print("\nTo actually delete these resources, run with --execute flag:")
        print(f"  python scripts/cleanup_by_tag.py --experiment-id {args.experiment_id} --execute")

if __name__ == "__main__":
    main()
