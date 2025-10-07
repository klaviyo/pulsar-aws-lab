# Pulsar Base AMI with Packer

This directory contains Packer templates and scripts to build a base AMI for Apache Pulsar deployments.

## Overview

The base AMI includes:
- Amazon Linux 2023
- Java 17 (Amazon Corretto) - LTS version with better performance
- Apache Pulsar 3.0.0 (configurable)
- OpenMessaging Benchmark framework
- System utilities (wget, tar, vim, htop, sysstat, net-tools, nmap-ncat, git, jq, etc.)
- Optimized system configuration for Pulsar workloads
- Generic systemd service templates

## Prerequisites

1. **Install Packer**:
   ```bash
   # macOS
   brew install packer

   # Linux
   wget https://releases.hashicorp.com/packer/1.9.4/packer_1.9.4_linux_amd64.zip
   unzip packer_1.9.4_linux_amd64.zip
   sudo mv packer /usr/local/bin/
   ```

2. **AWS Credentials**:
   ```bash
   export AWS_ACCESS_KEY_ID="your-access-key"
   export AWS_SECRET_ACCESS_KEY="your-secret-key"
   export AWS_DEFAULT_REGION="us-west-2"

   # Or use AWS CLI configuration
   aws configure
   ```

3. **AWS Permissions**: Ensure your IAM user/role has permissions to:
   - Create/describe/delete EC2 instances
   - Create/describe/deregister AMIs
   - Create/delete EBS snapshots
   - Systems Manager Session Manager access
   - Pass IAM role (for SSMManagedInstanceCore)

4. **SSM Session Manager Plugin** (required):
   ```bash
   # macOS
   brew install --cask session-manager-plugin

   # Linux/Windows
   # See: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html
   ```

5. **IAM Instance Profile**: Ensure `SSMManagedInstanceCore` IAM role exists:
   ```bash
   # Check if role exists
   aws iam get-role --role-name SSMManagedInstanceCore

   # If not, create it
   aws iam create-role --role-name SSMManagedInstanceCore \
     --assume-role-policy-document '{
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Principal": {"Service": "ec2.amazonaws.com"},
         "Action": "sts:AssumeRole"
       }]
     }'

   # Attach SSM managed policy
   aws iam attach-role-policy --role-name SSMManagedInstanceCore \
     --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

   # Create instance profile
   aws iam create-instance-profile --instance-profile-name SSMManagedInstanceCore
   aws iam add-role-to-instance-profile \
     --instance-profile-name SSMManagedInstanceCore \
     --role-name SSMManagedInstanceCore
   ```

## Usage

### Build with Default Settings

```bash
cd packer
packer init pulsar-base.pkr.hcl
packer build pulsar-base.pkr.hcl
```

This will:
- Use Amazon Linux 2023 as base
- Install Pulsar 3.0.0
- Build in us-west-2 region
- Use t3.small instance for building

### Build with Custom Variables

```bash
# Using variables.pkrvars.hcl file
packer build -var-file=variables.pkrvars.hcl pulsar-base.pkr.hcl

# Or using command-line variables
packer build \
  -var "region=us-east-1" \
  -var "pulsar_version=3.1.0" \
  -var "instance_type=t3.medium" \
  pulsar-base.pkr.hcl
```

### Validate Template

```bash
packer validate pulsar-base.pkr.hcl
```

### Format Template

```bash
packer fmt pulsar-base.pkr.hcl
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `region` | us-west-2 | AWS region to build AMI |
| `pulsar_version` | 3.0.0 | Apache Pulsar version |
| `instance_type` | t3.small | Instance type for building |
| `ami_prefix` | pulsar-base | AMI name prefix |

## Output

After successful build:
- AMI name: `pulsar-base-{version}-{timestamp}`
- AMI tags:
  - `Name`: Full AMI name
  - `PulsarVersion`: Pulsar version installed
  - `Environment`: lab
  - `ManagedBy`: packer
  - `BuildDate`: Build timestamp

## Directory Structure

```
packer/
├── pulsar-base.pkr.hcl           # Main Packer template
├── variables.pkrvars.hcl         # Variable definitions (optional)
├── README.md                      # This file
├── scripts/
│   ├── configure-system.sh       # System configuration
│   ├── install-pulsar.sh         # Pulsar installation
│   └── install-benchmark.sh      # OpenMessaging Benchmark setup
└── files/
    └── systemd/
        ├── zookeeper.service.tpl # ZooKeeper systemd template
        ├── bookkeeper.service.tpl # BookKeeper systemd template
        └── broker.service.tpl    # Broker systemd template
```

## What Gets Installed

### System Configuration
- SELinux disabled
- Timezone set to UTC
- System limits optimized for Pulsar (file descriptors, processes)
- Sysctl tuning for network performance
- Base directories created

### Apache Pulsar
- Installed to: `/opt/pulsar`
- Version: Configurable (default 3.0.0)
- Binaries symlinked to: `/usr/local/bin/`
- Configuration backed up to: `/opt/pulsar/conf/backup/`

### OpenMessaging Benchmark
- Installed to: `/opt/openmessaging-benchmark`
- Built with Maven (tests skipped)
- Helper scripts: `/usr/local/bin/run-benchmark`, `/usr/local/bin/benchmark-info`
- Results directory: `/opt/benchmark-results`
- Config directory: `/opt/benchmark-configs`

### Systemd Templates
- Location: `/opt/pulsar-templates/systemd/`
- Generic templates that can be customized at runtime
- Include: zookeeper.service.tpl, bookkeeper.service.tpl, broker.service.tpl

## Integration with Terraform

To use the built AMI with Terraform:

1. After Packer build completes, note the AMI ID
2. Update `config/infrastructure.yaml`:
   ```yaml
   compute:
     ami_id: "ami-xxxxxxxxx"  # Your built AMI
   ```

3. Or configure Terraform to find latest AMI:
   ```hcl
   data "aws_ami" "pulsar_base" {
     most_recent = true
     owners      = ["self"]

     filter {
       name   = "name"
       values = ["pulsar-base-3.0.0-*"]
     }

     filter {
       name   = "tag:ManagedBy"
       values = ["packer"]
     }
   }
   ```

## Benefits of Using Pre-built AMI

1. **Faster Deployment**: Skip Pulsar/Java installation during provisioning (2-3 minutes vs 15-20 minutes)
2. **Consistency**: Same base image across all instances
3. **Simplified Infrastructure**: No runtime configuration management needed
4. **Cost Savings**: Faster instance startup = lower costs
5. **Reproducibility**: Version-tagged AMIs for consistent deployments
6. **Immutable Infrastructure**: Changes require rebuilding AMI, preventing configuration drift
7. **Secure Builds**: Uses AWS SSM instead of SSH (no open ports, IAM-based authentication)

## Estimated Build Time

- Download Pulsar: ~2-3 minutes
- Build OpenMessaging Benchmark: ~5-10 minutes
- System configuration: ~1-2 minutes
- AMI creation: ~5-10 minutes
- **Total**: ~15-25 minutes

## Cost Estimate

Building the AMI incurs minimal costs:
- t3.small instance runtime: ~$0.02/hour
- EBS snapshot storage: ~$0.05/GB/month
- Estimated build cost: **< $0.01** per build

## Troubleshooting

### Build Fails with "timeout waiting for SSM"
- Verify AWS Session Manager plugin is installed: `session-manager-plugin`
- Check IAM role `SSMManagedInstanceCore` exists and has correct permissions
- Ensure instance has internet access (for SSM endpoints)
- Verify VPC has SSM endpoints configured (if using private subnets)
- Check AWS region is correct

### Maven Build Out of Memory
- Increase `instance_type` to t3.medium or larger
- Adjust MAVEN_OPTS in install-benchmark.sh

### Pulsar Download Timeout
- Check internet connectivity from build instance
- Increase timeout in install-pulsar.sh
- Use alternative mirror if archive.apache.org is slow

### AMI Not Found After Build
- Check Packer output for AMI ID
- Verify you're looking in the correct region
- Check AWS console EC2 > AMIs

## Clean Up

Packer automatically cleans up build resources (instances, security groups).

To delete built AMIs:
```bash
# List AMIs
aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:ManagedBy,Values=packer" \
  --query 'Images[*].[ImageId,Name,CreationDate]' \
  --output table

# Deregister AMI
aws ec2 deregister-image --image-id ami-xxxxxxxxx

# Delete associated snapshot
aws ec2 describe-snapshots \
  --owner-ids self \
  --filters "Name=description,Values=*ami-xxxxxxxxx*" \
  --query 'Snapshots[*].[SnapshotId]' \
  --output text | xargs -I {} aws ec2 delete-snapshot --snapshot-id {}
```

## Next Steps

1. Build the AMI: `python scripts/build-ami.py build --version 3.0.0`
2. Validate the AMI: `python scripts/build-ami.py validate --ami-id <ami-id>`
3. Deploy with Terraform (automatically discovers latest AMI)
4. Run tests with orchestrator: `python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml`
5. Create multiple AMI versions for different Pulsar versions as needed

## AMI Management

Use the provided Python CLI tool for AMI lifecycle management:

```bash
# Build new AMI
python scripts/build-ami.py build --version 3.0.0 --region us-west-2

# List all AMIs
python scripts/build-ami.py list --region us-west-2

# Validate AMI (launches test instance)
python scripts/build-ami.py validate --ami-id ami-xxxxx

# Delete old AMI
python scripts/build-ami.py delete --ami-id ami-xxxxx --region us-west-2
```

The orchestrator automatically discovers and uses the latest Pulsar AMI by version tag.
