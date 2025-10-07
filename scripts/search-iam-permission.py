#!/usr/bin/env python3
"""
Search IAM roles for specific permissions.

Usage:
    # Single permission
    python search-iam-permission.py --permission ec2:RunInstances

    # Multiple permissions with AND logic (role must have ALL permissions)
    python search-iam-permission.py --permission ec2:RunInstances --permission ec2:CreateKeyPair --mode AND

    # Multiple permissions with OR logic (role must have ANY permission)
    python search-iam-permission.py --permission ec2:RunInstances --permission ec2:CreateKeyPair --mode OR

    # With wildcards
    python search-iam-permission.py --permission ec2:* --verbose
"""

import argparse
import boto3
import json
import sys
from typing import List, Dict, Set
from botocore.exceptions import ClientError


class IAMPermissionSearcher:
    def __init__(self, region: str = None, verbose: bool = False):
        self.iam = boto3.client('iam', region_name=region)
        self.verbose = verbose
        self.checked_policies = {}  # Cache for managed policies

    def log(self, message: str):
        """Print verbose logging messages."""
        if self.verbose:
            print(f"[DEBUG] {message}")

    def matches_permission(self, action: str, search_permission: str) -> bool:
        """
        Check if an action matches the search permission.
        Supports wildcards: ec2:*, *, ec2:RunInstances*
        """
        search_lower = search_permission.lower()
        action_lower = action.lower()

        # Exact match
        if action_lower == search_lower:
            return True

        # Wildcard matching
        if '*' in search_lower:
            # Convert to regex-like pattern
            import re
            pattern = search_lower.replace('*', '.*')
            pattern = f"^{pattern}$"
            return re.match(pattern, action_lower) is not None

        return False

    def search_policy_document(self, policy_doc: dict, search_permissions: List[str]) -> Dict[str, List[str]]:
        """Search a policy document for matching permissions.

        Returns: Dict mapping each search_permission to list of matched actions
        """
        matches = {perm: [] for perm in search_permissions}

        statements = policy_doc.get('Statement', [])
        if not isinstance(statements, list):
            statements = [statements]

        for statement in statements:
            # Only check Allow statements
            if statement.get('Effect') != 'Allow':
                continue

            actions = statement.get('Action', [])
            if not isinstance(actions, list):
                actions = [actions]

            for action in actions:
                for search_perm in search_permissions:
                    if self.matches_permission(action, search_perm):
                        matches[search_perm].append(action)

        return matches

    def check_inline_policy(self, role_name: str, policy_name: str, search_permissions: List[str]) -> Dict[str, List[str]]:
        """Check an inline policy for the permissions."""
        try:
            self.log(f"Checking inline policy: {policy_name}")
            response = self.iam.get_role_policy(
                RoleName=role_name,
                PolicyName=policy_name
            )
            policy_doc = response['PolicyDocument']
            return self.search_policy_document(policy_doc, search_permissions)
        except ClientError as e:
            print(f"Error checking inline policy {policy_name}: {e}", file=sys.stderr)
            return {perm: [] for perm in search_permissions}

    def check_managed_policy(self, policy_arn: str, search_permissions: List[str]) -> Dict[str, List[str]]:
        """Check a managed policy for the permissions (with caching)."""
        # Create cache key from policy_arn and permissions tuple
        cache_key = (policy_arn, tuple(search_permissions))

        if cache_key in self.checked_policies:
            return self.checked_policies[cache_key]

        try:
            self.log(f"Checking managed policy: {policy_arn}")

            # Get the default version
            policy = self.iam.get_policy(PolicyArn=policy_arn)
            version_id = policy['Policy']['DefaultVersionId']

            # Get the policy document
            policy_version = self.iam.get_policy_version(
                PolicyArn=policy_arn,
                VersionId=version_id
            )
            policy_doc = policy_version['PolicyVersion']['Document']

            matches = self.search_policy_document(policy_doc, search_permissions)
            self.checked_policies[cache_key] = matches
            return matches

        except ClientError as e:
            print(f"Error checking managed policy {policy_arn}: {e}", file=sys.stderr)
            empty_result = {perm: [] for perm in search_permissions}
            self.checked_policies[cache_key] = empty_result
            return empty_result

    def search_role(self, role_name: str, search_permissions: List[str]) -> Dict:
        """Search a single role for the permissions."""
        self.log(f"Searching role: {role_name}")
        results = {
            'role_name': role_name,
            'permissions_found': {perm: [] for perm in search_permissions},  # Track which permissions found
            'inline_policies': {},
            'managed_policies': {}
        }

        try:
            # Check inline policies
            inline_policies = self.iam.list_role_policies(RoleName=role_name)
            for policy_name in inline_policies.get('PolicyNames', []):
                matches = self.check_inline_policy(role_name, policy_name, search_permissions)
                # Filter out permissions with no matches
                filtered_matches = {perm: actions for perm, actions in matches.items() if actions}
                if filtered_matches:
                    results['inline_policies'][policy_name] = filtered_matches
                    # Track which permissions were found
                    for perm in filtered_matches:
                        results['permissions_found'][perm].extend(filtered_matches[perm])

            # Check managed policies
            attached_policies = self.iam.list_attached_role_policies(RoleName=role_name)
            for policy in attached_policies.get('AttachedPolicies', []):
                policy_arn = policy['PolicyArn']
                policy_name = policy['PolicyName']
                matches = self.check_managed_policy(policy_arn, search_permissions)
                # Filter out permissions with no matches
                filtered_matches = {perm: actions for perm, actions in matches.items() if actions}
                if filtered_matches:
                    results['managed_policies'][policy_name] = {
                        'arn': policy_arn,
                        'actions': filtered_matches
                    }
                    # Track which permissions were found
                    for perm in filtered_matches:
                        results['permissions_found'][perm].extend(filtered_matches[perm])

        except ClientError as e:
            print(f"Error searching role {role_name}: {e}", file=sys.stderr)

        return results

    def search_all_roles(self, search_permissions: List[str], mode: str = 'OR') -> List[Dict]:
        """Search all IAM roles for the permissions.

        Args:
            search_permissions: List of permissions to search for
            mode: 'AND' (role must have ALL permissions) or 'OR' (role must have ANY permission)
        """
        if len(search_permissions) == 1:
            print(f"Searching for permission: {search_permissions[0]}")
        else:
            print(f"Searching for permissions ({mode} mode): {', '.join(search_permissions)}")
        print("-" * 80)

        roles_with_permission = []

        try:
            # Paginate through all roles
            paginator = self.iam.get_paginator('list_roles')
            for page in paginator.paginate():
                for role in page['Roles']:
                    role_name = role['RoleName']
                    result = self.search_role(role_name, search_permissions)

                    # Apply AND/OR logic
                    if mode == 'AND':
                        # Role must have ALL permissions
                        has_all = all(result['permissions_found'][perm] for perm in search_permissions)
                        if has_all:
                            roles_with_permission.append(result)
                    else:  # OR mode
                        # Role must have ANY permission
                        has_any = any(result['permissions_found'][perm] for perm in search_permissions)
                        if has_any:
                            roles_with_permission.append(result)

        except ClientError as e:
            print(f"Error listing roles: {e}", file=sys.stderr)
            sys.exit(1)

        return roles_with_permission

    def print_results(self, results: List[Dict], search_permissions: List[str]):
        """Print search results in a readable format."""
        if not results:
            print("\nNo roles found with the specified permission(s).")
            return

        print(f"\nFound {len(results)} role(s) with the permission(s):\n")

        for result in results:
            print(f"Role: {result['role_name']}")

            # Show which permissions were found
            if len(search_permissions) > 1:
                found_perms = [perm for perm in search_permissions if result['permissions_found'][perm]]
                print(f"  Permissions found: {', '.join(found_perms)}")

            if result['inline_policies']:
                print("  Inline Policies:")
                for policy_name, perm_dict in result['inline_policies'].items():
                    print(f"    - {policy_name}")
                    for perm, actions in perm_dict.items():
                        print(f"      [{perm}]")
                        for action in actions:
                            print(f"        → {action}")

            if result['managed_policies']:
                print("  Managed Policies:")
                for policy_name, policy_info in result['managed_policies'].items():
                    print(f"    - {policy_name}")
                    print(f"      ARN: {policy_info['arn']}")
                    for perm, actions in policy_info['actions'].items():
                        print(f"      [{perm}]")
                        for action in actions:
                            print(f"        → {action}")

            print()


def main():
    parser = argparse.ArgumentParser(
        description='Search IAM roles for specific permissions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single permission
  %(prog)s --permission ec2:RunInstances

  # Multiple permissions with AND logic (must have ALL)
  %(prog)s --permission ec2:RunInstances --permission ec2:CreateKeyPair --mode AND

  # Multiple permissions with OR logic (must have ANY)
  %(prog)s --permission ec2:RunInstances --permission ec2:CreateKeyPair --mode OR

  # With wildcards
  %(prog)s --permission ec2:* --verbose

  # JSON output
  %(prog)s --permission s3:GetObject --json
        """
    )

    parser.add_argument(
        '--permission', '-p',
        required=True,
        action='append',
        dest='permissions',
        help='Permission to search for (can be specified multiple times). Supports wildcards: ec2:*, *'
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['AND', 'OR'],
        default='OR',
        help='Logic mode for multiple permissions: AND (role must have ALL) or OR (role must have ANY). Default: OR'
    )

    parser.add_argument(
        '--region',
        default=None,
        help='AWS region (default: use default region from AWS config)'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )

    args = parser.parse_args()

    # Create searcher
    searcher = IAMPermissionSearcher(region=args.region, verbose=args.verbose)

    # Search for permissions
    results = searcher.search_all_roles(args.permissions, mode=args.mode)

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        searcher.print_results(results, args.permissions)


if __name__ == '__main__':
    main()
