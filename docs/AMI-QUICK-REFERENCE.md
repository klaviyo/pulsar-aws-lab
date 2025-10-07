# AMI Management Quick Reference

## Quick Start

```bash
# Install dependencies
pip install -r scripts/requirements.txt

# Install Packer (macOS)
brew install packer

# Configure AWS credentials
aws configure
```

## Common Commands

### Build New AMI
```bash
# Standard build
python scripts/build-ami.py build --version 3.0.0

# Custom region
python scripts/build-ami.py --region us-east-1 build --version 3.0.0

# Dry-run (no changes)
python scripts/build-ami.py --dry-run build --version 3.0.0
```

### List AMIs
```bash
# List all AMIs (cached)
python scripts/build-ami.py list

# Force refresh
python scripts/build-ami.py list --no-cache

# Different region
python scripts/build-ami.py --region us-east-1 list
```

### Validate AMI
```bash
# Validate specific AMI
python scripts/build-ami.py validate --ami-id ami-0123456789abcdef

# Validate with custom instance type
python scripts/build-ami.py validate --ami-id ami-0123456789abcdef --instance-type t3.small
```

### Delete AMI
```bash
# Delete AMI and snapshots
python scripts/build-ami.py delete --ami-id ami-0123456789abcdef

# Delete AMI but keep snapshots
python scripts/build-ami.py delete --ami-id ami-0123456789abcdef --keep-snapshots
```

### Get Latest AMI
```bash
# Get latest AMI ID
python scripts/build-ami.py latest

# Different region
python scripts/build-ami.py --region us-east-1 latest
```

## Command Cheat Sheet

| Command | Purpose | Time | Cost |
|---------|---------|------|------|
| `build --version X.Y.Z` | Build new AMI | 10-15 min | ~$0.01 |
| `list` | Show all AMIs | 1-2 sec | Free |
| `validate --ami-id ami-XXX` | Verify AMI works | 2-3 min | ~$0.005 |
| `delete --ami-id ami-XXX` | Remove AMI | 5-10 sec | Free |
| `latest` | Get newest AMI | 1-2 sec | Free |

## Global Options

```bash
--region REGION     # AWS region (default: us-west-2)
--dry-run           # Simulate without changes
--debug             # Verbose logging
```

## Common Workflows

### Build and Validate
```bash
# Build new AMI
AMI_ID=$(python scripts/build-ami.py build --version 3.0.0 | grep "AMI ID:" | awk '{print $3}')

# Validate it
python scripts/build-ami.py validate --ami-id $AMI_ID

# If validation passes, use it
echo "Ready to use: $AMI_ID"
```

### Clean Up Old AMIs
```bash
# List all AMIs
python scripts/build-ami.py list

# Delete old ones (manual)
python scripts/build-ami.py delete --ami-id ami-OLD123456
python scripts/build-ami.py delete --ami-id ami-OLD789012
```

### Multi-Region Deployment
```bash
# Build in multiple regions
for region in us-west-2 us-east-1 eu-west-1; do
    python scripts/build-ami.py --region $region build --version 3.0.0
done
```

## Exit Codes

- `0` - Success
- `1` - Error (validation failed, AMI not found, etc.)
- `130` - Cancelled by user (Ctrl+C)

## Troubleshooting

### Packer not found
```bash
# Install Packer
brew install packer  # macOS
sudo apt install packer  # Linux
```

### AWS credentials not configured
```bash
# Configure credentials
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
```

### Validation timeout
```bash
# Ensure SSMManagedInstanceCore role exists
aws iam get-role --role-name SSMManagedInstanceCore

# Create if missing (requires admin permissions)
aws iam create-role --role-name SSMManagedInstanceCore \
    --assume-role-policy-document file://ssm-trust-policy.json

aws iam attach-role-policy --role-name SSMManagedInstanceCore \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
```

## Performance Tips

1. **Use cache**: Don't use `--no-cache` unless necessary
2. **Right-size instances**:
   - Build: t3.small (good balance)
   - Validate: t3.micro (cheapest)
3. **Clean up regularly**: Old AMIs cost $0.05/GB/month
4. **Enable debug sparingly**: `--debug` creates verbose output

## Integration with Orchestrator

The orchestrator automatically finds the latest AMI:

```bash
# Orchestrator uses latest AMI automatically
python scripts/orchestrator.py setup --config config/infrastructure.yaml

# Build new AMI first if needed
python scripts/build-ami.py build --version 3.0.0
python scripts/orchestrator.py setup --config config/infrastructure.yaml
```

## File Locations

- **Script**: `/home/kazamatzuri/projects/pulsar-aws-lab/scripts/build-ami.py`
- **Cache**: `~/.pulsar-aws-lab/ami-cache/ami-list.json`
- **Packer template**: `/home/kazamatzuri/projects/pulsar-aws-lab/packer/pulsar-base.pkr.hcl`
- **Documentation**: `/home/kazamatzuri/projects/pulsar-aws-lab/docs/BUILD-AMI-GUIDE.md`

## IAM Permissions Summary

Minimum required permissions:
- EC2: DescribeImages, CreateImage, DeregisterImage, RunInstances, TerminateInstances, CreateTags
- SSM: SendCommand, GetCommandInvocation, DescribeInstanceInformation
- Snapshots: DescribeSnapshots, CreateSnapshot, DeleteSnapshot

## Cost Breakdown

- **Build**: ~$0.01 per build (15 min × t3.small)
- **Validate**: ~$0.005 per validation (3 min × t3.micro)
- **Storage**: ~$0.15/month per 3GB AMI
- **List/Delete**: Free (API calls only)

## Support

- Full guide: `docs/BUILD-AMI-GUIDE.md`
- Packer issues: Check `packer/` directory
- AWS issues: Verify credentials and permissions
- Orchestrator integration: See `CLAUDE.md`
