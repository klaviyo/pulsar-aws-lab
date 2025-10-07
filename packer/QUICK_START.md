# Quick Start Guide - Pulsar Base AMI

## 1. Prerequisites

```bash
# Install Packer
brew tap hashicorp/tap
brew install hashicorp/tap/packer
# or download from https://www.packer.io/downloads

# Install AWS Session Manager plugin (required for SSM communication)
brew install --cask session-manager-plugin
# or see: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

# Configure AWS credentials
aws configure
# OR
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-west-2"

# Ensure SSMManagedInstanceCore IAM role exists
aws iam get-role --role-name SSMManagedInstanceCore || {
  echo "Creating SSMManagedInstanceCore role..."
  # See packer/README.md for full setup instructions
}
```

## 2. Build the AMI

### Option A: Use the Build Script (Recommended)

```bash
cd packer

# Build with defaults (Pulsar 3.0.0, us-west-2, t3.small)
./build.sh

# Build with custom options
./build.sh --region us-east-1 --version 3.1.0 --instance-type t3.medium

# Validate only (no build)
./build.sh --validate

# View help
./build.sh --help
```

### Option B: Use Packer Directly

```bash
cd packer

# Initialize Packer
packer init pulsar-base.pkr.hcl

# Validate template
packer validate pulsar-base.pkr.hcl

# Build with defaults
packer build pulsar-base.pkr.hcl

# Build with custom variables
packer build \
  -var "region=us-east-1" \
  -var "pulsar_version=3.1.0" \
  pulsar-base.pkr.hcl
```

## 3. Find Your AMI

After successful build:

```bash
# Get AMI ID from Packer output (last line)
# Example: amazon-ebs.pulsar_base: AMI: ami-0abcdef1234567890

# Or query AWS
aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:ManagedBy,Values=packer" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].[ImageId,Name]' \
  --output table
```

## 4. Use AMI in Terraform

Update `config/infrastructure.yaml`:

```yaml
compute:
  # Use your built AMI
  ami_id: "ami-0abcdef1234567890"
```

Or update Terraform to auto-discover:

```hcl
# In terraform/main.tf or appropriate module
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

resource "aws_instance" "pulsar_node" {
  ami           = data.aws_ami.pulsar_base.id
  instance_type = var.instance_type
  # ... rest of configuration
}
```

## 5. What's Included

The AMI contains:
- Amazon Linux 2023
- Java 11 (Corretto)
- Apache Pulsar 3.0.0 in `/opt/pulsar`
- OpenMessaging Benchmark in `/opt/openmessaging-benchmark`
- System tools: wget, tar, vim, htop, sysstat, net-tools, git, maven
- Optimized system settings (limits, sysctl)
- Systemd service templates in `/opt/pulsar-templates/systemd/`

## 6. Verify Installation (After Launch)

SSH into an instance using the AMI:

```bash
# Check Pulsar installation
pulsar version

# Check Java version
java -version

# Check OpenMessaging Benchmark
benchmark-info

# Verify directories
ls -la /opt/pulsar
ls -la /opt/openmessaging-benchmark
ls -la /opt/pulsar-templates/systemd/
```

## 7. Troubleshooting

### Build fails with SSH timeout
- Check AWS credentials and permissions
- Verify default VPC exists in the region
- Ensure security groups allow SSH (automated by Packer)

### Maven build fails (OOM)
- Increase instance type: `./build.sh --instance-type t3.medium`

### Pulsar download timeout
- Check network connectivity
- Try different region closer to Apache mirrors

### Can't find AMI after build
- Verify you're searching in the correct region
- Check Packer output for actual AMI ID
- Look in AWS Console: EC2 > AMIs

## 8. Clean Up

Delete old AMIs:

```bash
# List all Packer-built AMIs
aws ec2 describe-images \
  --owners self \
  --filters "Name=tag:ManagedBy,Values=packer" \
  --query 'Images[*].[ImageId,Name,CreationDate]' \
  --output table

# Deregister specific AMI
aws ec2 deregister-image --image-id ami-xxxxxxxxx

# Delete associated snapshot
aws ec2 describe-snapshots \
  --owner-ids self \
  --filters "Name=description,Values=*ami-xxxxxxxxx*" \
  --query 'Snapshots[*].SnapshotId' \
  --output text | \
  xargs -I {} aws ec2 delete-snapshot --snapshot-id {}
```

## 9. Next Steps

1. **Validate your AMI**: `python scripts/build-ami.py validate --ami-id <ami-id>`
2. **Deploy cluster**: `python scripts/orchestrator.py setup --config config/infrastructure.yaml`
3. **Run benchmarks**: `python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml`
4. **Build multiple versions**: Create AMIs for different Pulsar releases as needed
5. **Automate builds**: Set up CI/CD pipeline for automated AMI builds
6. **Manage lifecycle**: Use `build-ami.py` to list, validate, and delete old AMIs

## Estimated Costs

- Build time: 15-25 minutes
- Build cost: < $0.01 per build (t3.small runtime)
- Storage: ~$0.05/GB/month (EBS snapshot)
- Total monthly cost: ~$0.50/month per AMI (10 GB snapshot)

## Support

For issues or questions:
1. Check Packer output for error messages
2. Review scripts in `packer/scripts/` for installation details
3. Validate template: `./build.sh --validate`
4. Run with debug: `./build.sh --debug`
