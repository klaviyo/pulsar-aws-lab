# AMI Build and Management Guide

This guide covers the `build-ami.py` script for managing Pulsar AMIs.

## Overview

The `build-ami.py` script provides a comprehensive CLI interface for:
- Building new Pulsar AMIs using Packer
- Listing existing AMIs with detailed metadata
- Validating AMI integrity by launching test instances
- Deleting AMIs and associated snapshots
- Finding the latest AMI ID

## Prerequisites

### Required Tools

1. **Python 3.7+** with dependencies:
   ```bash
   pip install -r scripts/requirements.txt
   ```

2. **Packer** (for building AMIs):
   ```bash
   # macOS
   brew install packer

   # Linux - download from https://www.packer.io/downloads
   # Or use package manager
   sudo apt-get install packer  # Debian/Ubuntu
   sudo yum install packer      # RHEL/CentOS

   # Windows
   choco install packer
   ```

3. **AWS Credentials** configured:
   ```bash
   # Option 1: AWS CLI
   aws configure

   # Option 2: Environment variables
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-west-2

   # Option 3: IAM role (if running on EC2)
   # No configuration needed
   ```

### IAM Permissions

The script requires the following IAM permissions:

**For building AMIs:**
- `ec2:DescribeImages`
- `ec2:CreateImage`
- `ec2:DeregisterImage`
- `ec2:RunInstances`
- `ec2:TerminateInstances`
- `ec2:CreateTags`
- `ec2:DescribeInstances`
- `ec2:DescribeSnapshots`
- `ec2:CreateSnapshot`
- `ec2:DeleteSnapshot`

**For validation:**
- `ssm:SendCommand`
- `ssm:GetCommandInvocation`
- `ssm:DescribeInstanceInformation`
- `ec2:RunInstances`
- `ec2:TerminateInstances`
- `ec2:DescribeInstances`

**For listing and deletion:**
- `ec2:DescribeImages`
- `ec2:DeregisterImage`
- `ec2:DeleteSnapshot`

## Usage

### Basic Command Structure

```bash
python scripts/build-ami.py [--region REGION] [--dry-run] [--debug] COMMAND [OPTIONS]
```

**Global Options:**
- `--region REGION`: AWS region (default: us-west-2)
- `--dry-run`: Simulate operations without making changes (useful for testing)
- `--debug`: Enable verbose debug logging

### Commands

#### 1. Build a New AMI

Build a fresh Pulsar AMI from the Packer template.

```bash
python scripts/build-ami.py build --version VERSION [--instance-type TYPE]
```

**Options:**
- `--version VERSION`: Apache Pulsar version to install (required, e.g., "3.0.0")
- `--instance-type TYPE`: EC2 instance type for building (default: t3.small)

**Example:**
```bash
# Build AMI with Pulsar 3.0.0 in us-west-2
python scripts/build-ami.py --region us-west-2 build --version 3.0.0

# Build with custom instance type
python scripts/build-ami.py build --version 3.0.0 --instance-type t3.medium

# Dry-run to see what would happen
python scripts/build-ami.py --dry-run build --version 3.0.0
```

**Build Process:**
1. Validates prerequisites (Packer, AWS credentials, template)
2. Initializes Packer plugins
3. Runs Packer build (10-15 minutes typical)
4. Extracts and displays AMI ID
5. Invalidates local cache

**Output:**
```
Building Pulsar AMI
Pulsar Version: 3.0.0
Instance Type: t3.small
Region: us-west-2

Initializing Packer plugins...
✓ Packer plugins initialized

Starting Packer build (this will take 10-15 minutes)...
[Packer output streamed in real-time...]

✓ AMI built successfully!
  AMI ID: ami-0a1b2c3d4e5f67890
  Build time: 12m 34s
  Region: us-west-2
```

#### 2. List Existing AMIs

List all Pulsar AMIs in the region with metadata.

```bash
python scripts/build-ami.py list [--no-cache]
```

**Options:**
- `--no-cache`: Skip cache and fetch fresh data from AWS

**Example:**
```bash
# List AMIs (uses 5-minute cache)
python scripts/build-ami.py --region us-west-2 list

# Force refresh from AWS
python scripts/build-ami.py list --no-cache
```

**Output:**
```
Fetching AMI list from AWS...
✓ Found 3 AMI(s)

┌─────────────────────┬──────────────────────────────┬─────────┬───────────┬──────────────────┐
│ AMI ID              │ Name                         │ Version │ State     │ Created          │
├─────────────────────┼──────────────────────────────┼─────────┼───────────┼──────────────────┤
│ ami-0a1b2c3d4e5f678 │ pulsar-base-3.0.0-20251006   │ 3.0.0   │ available │ 2025-10-06 14:23 │
│ ami-09876543210fedc │ pulsar-base-2.11.0-20250920  │ 2.11.0  │ available │ 2025-09-20 10:15 │
│ ami-0abcdef12345678 │ pulsar-base-2.10.0-20250815  │ 2.10.0  │ available │ 2025-08-15 09:30 │
└─────────────────────┴──────────────────────────────┴─────────┴───────────┴──────────────────┘
```

**Performance:**
- First call: ~1-2 seconds (API call)
- Cached calls: <100ms (local read)
- Cache TTL: 5 minutes
- Cache invalidated after build/delete operations

#### 3. Validate an AMI

Verify AMI integrity by launching a test instance and running health checks.

```bash
python scripts/build-ami.py validate --ami-id AMI_ID [--instance-type TYPE]
```

**Options:**
- `--ami-id AMI_ID`: AMI ID to validate (required)
- `--instance-type TYPE`: Instance type for validation (default: t3.micro)

**Example:**
```bash
# Validate AMI
python scripts/build-ami.py validate --ami-id ami-0a1b2c3d4e5f67890

# Validate with larger instance
python scripts/build-ami.py validate --ami-id ami-0a1b2c3d4e5f67890 --instance-type t3.small
```

**Validation Process:**
1. Launches test EC2 instance from AMI
2. Waits for instance to reach 'running' state (~30-60s)
3. Waits for SSM agent to be online (~60-120s)
4. Runs validation checks:
   - `/opt/pulsar` directory exists
   - Pulsar binaries present (`pulsar`, `bookkeeper`)
   - Systemd templates installed in `/opt/pulsar-templates/systemd`
   - Java runtime available
   - OpenMessaging Benchmark installed
5. Terminates test instance (always runs, even on failure)
6. Reports validation result

**Output:**
```
Validating AMI
AMI ID: ami-0a1b2c3d4e5f67890
Instance Type: t3.micro
Region: us-west-2

Step 1/6: Launching test instance...
  ✓ Instance launched: i-0123456789abcdef0

Step 2/6: Waiting for instance to be running...
  ✓ Instance is running

Step 3/6: Waiting for SSM agent...
  ✓ SSM agent is online

Step 4/6: Running validation checks...
  Checking: Pulsar directory exists... ✓
  Checking: Pulsar binaries present... ✓
  Checking: Systemd templates installed... ✓
  Checking: Java installed... ✓
  Checking: OpenMessaging Benchmark installed... ✓
  ✓ All validation checks passed

Step 5/6: Terminating test instance...
  ✓ Test instance terminated

Step 6/6: Validation complete

✓ AMI VALIDATION PASSED
```

**Exit Codes:**
- `0`: Validation passed
- `1`: Validation failed

**Time Complexity:** ~2-3 minutes total

#### 4. Delete an AMI

Delete an AMI and optionally its associated snapshots.

```bash
python scripts/build-ami.py delete --ami-id AMI_ID [--keep-snapshots]
```

**Options:**
- `--ami-id AMI_ID`: AMI ID to delete (required)
- `--keep-snapshots`: Keep EBS snapshots (default: delete snapshots)

**Example:**
```bash
# Delete AMI and snapshots
python scripts/build-ami.py delete --ami-id ami-0a1b2c3d4e5f67890

# Delete AMI but keep snapshots
python scripts/build-ami.py delete --ami-id ami-0a1b2c3d4e5f67890 --keep-snapshots

# Dry-run deletion
python scripts/build-ami.py --dry-run delete --ami-id ami-0a1b2c3d4e5f67890
```

**Deletion Process:**
1. Retrieves AMI metadata
2. Identifies associated EBS snapshots
3. Deregisters AMI
4. Deletes snapshots (unless `--keep-snapshots` specified)
5. Invalidates local cache

**Output:**
```
Deleting AMI: ami-0a1b2c3d4e5f67890
  AMI Name: pulsar-base-3.0.0-20251006
  State: available
  Snapshots: 1

  Deregistering AMI... ✓
  Deleting 1 snapshot(s)...
    Deleting snap-0123456789abcdef... ✓

✓ AMI deleted successfully
```

**Warning:** This operation is irreversible. Always validate before deleting production AMIs.

#### 5. Get Latest AMI

Display the ID of the most recently created Pulsar AMI.

```bash
python scripts/build-ami.py latest
```

**Example:**
```bash
# Get latest AMI in us-west-2
python scripts/build-ami.py --region us-west-2 latest

# Get latest AMI in us-east-1
python scripts/build-ami.py --region us-east-1 latest
```

**Output:**
```
Latest AMI: ami-0a1b2c3d4e5f67890
```

**Exit Codes:**
- `0`: AMI found
- `1`: No AMIs found

**Use Case:** Integration with automation scripts that need the latest AMI ID.

## Advanced Usage

### Multi-Region AMI Management

Build and manage AMIs across multiple regions:

```bash
# Build in multiple regions
for region in us-west-2 us-east-1 eu-west-1; do
    python scripts/build-ami.py --region $region build --version 3.0.0
done

# List AMIs in all regions
for region in us-west-2 us-east-1 eu-west-1; do
    echo "=== $region ==="
    python scripts/build-ami.py --region $region list
done
```

### Integration with Orchestrator

The orchestrator automatically uses the latest AMI:

```python
# In orchestrator.py
ami_id = self.validate_ami_exists(region, ami_name_pattern="pulsar-base-*")
```

To explicitly specify an AMI for testing:

```bash
# Build new AMI
AMI_ID=$(python scripts/build-ami.py build --version 3.0.0 | grep "AMI ID:" | awk '{print $3}')

# Validate it
python scripts/build-ami.py validate --ami-id $AMI_ID

# Use with orchestrator (requires code modification)
# Set ami_id in infrastructure config or pass as variable
```

### Automated Validation Pipeline

Create a CI/CD pipeline to validate new AMIs:

```bash
#!/bin/bash
# ami-validation-pipeline.sh

set -e

VERSION=$1
REGION=${2:-us-west-2}

echo "Building AMI for Pulsar $VERSION..."
AMI_ID=$(python scripts/build-ami.py --region $REGION build --version $VERSION \
    | grep "AMI ID:" | awk '{print $3}')

echo "Validating AMI $AMI_ID..."
if python scripts/build-ami.py --region $REGION validate --ami-id $AMI_ID; then
    echo "✓ AMI validation passed - ready for production"

    # Tag AMI as validated
    aws ec2 create-tags --region $REGION \
        --resources $AMI_ID \
        --tags Key=Validated,Value=true Key=ValidationDate,Value=$(date -u +%Y-%m-%d)

    exit 0
else
    echo "✗ AMI validation failed - deleting AMI"
    python scripts/build-ami.py --region $REGION delete --ami-id $AMI_ID
    exit 1
fi
```

### Cost Management

AMI storage costs money (EBS snapshots). Regularly clean up old AMIs:

```bash
#!/bin/bash
# cleanup-old-amis.sh

REGION=${1:-us-west-2}
KEEP_LATEST_N=3

echo "Listing all AMIs..."
python scripts/build-ami.py --region $REGION list --no-cache > /tmp/ami-list.txt

# Extract AMI IDs (skip the latest N)
AMI_IDS=$(grep "ami-" /tmp/ami-list.txt | awk '{print $1}' | tail -n +$((KEEP_LATEST_N + 1)))

for AMI_ID in $AMI_IDS; do
    echo "Deleting old AMI: $AMI_ID"
    python scripts/build-ami.py --region $REGION delete --ami-id $AMI_ID
done
```

## Troubleshooting

### Packer Build Failures

**Problem:** Packer build fails with timeout or SSH errors

**Solution:**
1. Check security groups allow outbound internet access
2. Verify the base AMI (Amazon Linux 2023) is available in your region
3. Increase instance size: `--instance-type t3.medium`
4. Check Packer logs for specific errors

**Problem:** Packer can't find plugins

**Solution:**
```bash
# Manually initialize Packer plugins
cd packer
packer init pulsar-base.pkr.hcl
```

### AMI Validation Failures

**Problem:** SSM agent timeout during validation

**Solution:**
1. Ensure the IAM role `SSMManagedInstanceCore` exists
2. Check VPC allows outbound HTTPS to SSM endpoints
3. Wait longer - first SSM connection can take 2-3 minutes
4. Verify instance has internet connectivity

**Problem:** Validation checks fail

**Solution:**
1. Review Packer provisioning scripts
2. Check `/var/log/cloud-init-output.log` on test instance
3. Manually launch instance and SSH in to debug
4. Ensure Packer build completed successfully

### AWS Credential Issues

**Problem:** "No credentials found" error

**Solution:**
```bash
# Verify credentials are configured
aws sts get-caller-identity

# If fails, configure credentials
aws configure

# Or use environment variables
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
```

**Problem:** "Access Denied" errors

**Solution:**
1. Verify IAM user/role has required permissions (see Prerequisites)
2. Check if MFA is required
3. Verify credentials haven't expired
4. Try with `--debug` flag to see full error

### Performance Issues

**Problem:** List command is slow

**Solution:**
- Lists are cached for 5 minutes by default
- Force cache refresh only when needed: `--no-cache`
- Cache stored in `~/.pulsar-aws-lab/ami-cache/ami-list.json`

**Problem:** Build takes too long (>20 minutes)

**Solution:**
1. Use larger instance: `--instance-type t3.medium`
2. Check network latency to package repositories
3. Review Packer provisioning scripts for inefficiencies
4. Consider using pre-cached packages

## Best Practices

### 1. Version Management

Always tag AMIs with Pulsar version:
- AMI naming: `pulsar-base-{VERSION}-{TIMESTAMP}`
- Keep 2-3 recent versions for rollback
- Delete old versions to save costs

### 2. Validation Before Production

Never use an unvalidated AMI:
```bash
# Build -> Validate -> Use
AMI_ID=$(python scripts/build-ami.py build --version 3.0.0 | grep "AMI ID:" | awk '{print $3}')
python scripts/build-ami.py validate --ami-id $AMI_ID && echo "Ready for production"
```

### 3. Cost Optimization

- Use t3.micro for validation (smallest instance)
- Use t3.small for building (faster than t3.micro, still cheap)
- Delete old AMIs regularly
- Consider spot instances for building (requires Packer config change)

### 4. Multi-Region Strategy

- Build AMIs in primary region
- Copy to other regions using AWS AMI copy:
  ```bash
  aws ec2 copy-image \
      --source-region us-west-2 \
      --source-image-id ami-0a1b2c3d4e5f67890 \
      --region us-east-1 \
      --name "pulsar-base-3.0.0-20251006"
  ```

### 5. Disaster Recovery

- Keep at least 2 validated AMIs
- Store AMI IDs in version control
- Document Pulsar version -> AMI ID mappings
- Test AMI restoration periodically

## Integration Examples

### Shell Script Integration

```bash
#!/bin/bash
# build-and-deploy.sh

VERSION="3.0.0"
REGION="us-west-2"

# Build AMI
echo "Building AMI..."
AMI_ID=$(python scripts/build-ami.py --region $REGION build --version $VERSION \
    | grep "AMI ID:" | awk '{print $3}')

# Validate AMI
echo "Validating AMI..."
if ! python scripts/build-ami.py --region $REGION validate --ami-id $AMI_ID; then
    echo "Validation failed, aborting"
    exit 1
fi

# Deploy with orchestrator
echo "Deploying cluster..."
python scripts/orchestrator.py setup --config config/infrastructure.yaml

echo "Deployment complete!"
```

### Python Integration

```python
import subprocess
import json

def get_latest_ami(region='us-west-2'):
    """Get latest Pulsar AMI ID."""
    result = subprocess.run(
        ['python', 'scripts/build-ami.py', '--region', region, 'latest'],
        capture_output=True,
        text=True,
        check=True
    )

    # Extract AMI ID from output
    for line in result.stdout.split('\n'):
        if 'Latest AMI:' in line:
            return line.split()[-1]

    return None

def build_ami(version, region='us-west-2'):
    """Build a new AMI."""
    subprocess.run(
        ['python', 'scripts/build-ami.py', '--region', region,
         'build', '--version', version],
        check=True
    )

# Usage
ami_id = get_latest_ami('us-west-2')
print(f"Using AMI: {ami_id}")
```

## Performance Characteristics

### Time Complexity

| Operation | API Calls | Duration | Notes |
|-----------|-----------|----------|-------|
| Build | O(1) | 10-15 min | Packer build time |
| List | O(n) | 1-2 sec | n = number of AMIs, cached |
| Validate | O(1) | 2-3 min | Instance startup time |
| Delete | O(m) | 5-10 sec | m = number of snapshots |
| Latest | O(n) | 1-2 sec | n = number of AMIs, cached |

### Space Complexity

- AMI storage: ~2-3 GB per AMI (EBS snapshot)
- Cache storage: <1 MB (local JSON cache)
- Build artifacts: Temporary, cleaned up automatically

### Cost Estimates

- AMI storage: ~$0.05/GB/month (EBS snapshot pricing)
- Build instance: ~$0.01 for 15-minute t3.small build
- Validation instance: ~$0.005 for 3-minute t3.micro test
- Typical AMI (3 GB): ~$0.15/month storage cost

## See Also

- [Packer Documentation](https://www.packer.io/docs)
- [AWS AMI Documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html)
- [Orchestrator Guide](../CLAUDE.md#main-operations)
- [Infrastructure Configuration](../config/infrastructure.yaml)
