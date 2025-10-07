# Quick Start Guide

Get your Pulsar cluster running in under 30 minutes!

## Prerequisites Checklist

Before you begin, ensure you have:

- ✅ **AWS Account** with administrator access
- ✅ **AWS CLI** installed and configured (`aws configure`)
- ✅ **Terraform** installed (>= 1.0)
- ✅ **Python 3.8+** installed
- ✅ **Packer** installed (for AMI building)
- ✅ **SSH Key Pair** in AWS (or create one below)

## 5-Minute Setup

### Step 1: Install Dependencies

```bash
# Clone the repository
git clone https://github.com/your-org/pulsar-aws-lab.git
cd pulsar-aws-lab

# Install Python dependencies
pip install -r scripts/requirements.txt

# Install Packer (if not already installed)
# macOS
brew install packer

# Linux
# Download from https://www.packer.io/downloads
```

### Step 2: Configure AWS

```bash
# Configure AWS credentials (if not already done)
aws configure

# Create SSH key pair (if you don't have one)
aws ec2 create-key-pair \
  --key-name pulsar-lab-key \
  --region us-west-2 \
  --query 'KeyMaterial' \
  --output text > ~/.ssh/pulsar-lab-key.pem

chmod 400 ~/.ssh/pulsar-lab-key.pem
```

### Step 3: Build Pulsar AMI (One-Time, ~15 minutes)

```bash
# Build the base Pulsar AMI
python scripts/build-ami.py build --version 3.0.0 --region us-west-2

# Wait for build to complete (takes 10-15 minutes)
# The AMI ID will be displayed when complete
```

**Expected Output:**
```
Building Pulsar AMI
Pulsar Version: 3.0.0
Instance Type: t3.small
Region: us-west-2

Initializing Packer plugins...
✓ Packer plugins initialized

Starting Packer build (this will take 10-15 minutes)...
[Packer output...]

✓ AMI built successfully!
  AMI ID: ami-0a1b2c3d4e5f67890
  Build time: 12m 34s
  Region: us-west-2
```

**Validate the AMI (Optional but Recommended):**
```bash
# Get the latest AMI ID
AMI_ID=$(python scripts/build-ami.py latest --region us-west-2)

# Validate it
python scripts/build-ami.py validate --ami-id $AMI_ID
```

### Step 4: Run Your First Test (2-3 minutes)

```bash
# Run a proof-of-concept test (full lifecycle: deploy → test → report → teardown)
python scripts/orchestrator.py full \
  --test-plan config/test-plans/poc.yaml

# This will:
# 1. Deploy infrastructure (60-120 seconds)
# 2. Run a validation test (2 minutes)
# 3. Generate report
# 4. Destroy all resources
```

**What happens during the test:**
- ✅ Terraform creates VPC, subnets, security groups
- ✅ Launches 9 EC2 instances (3 ZK, 3 BK, 2 Brokers, 1 Client)
- ✅ User-data scripts configure and start Pulsar cluster
- ✅ Runs benchmark: 20k msgs/sec, 1KB messages, 2 minutes
- ✅ Generates comprehensive HTML report
- ✅ Cleans up all AWS resources

**Expected Timeline:**
```
00:00 - Setup infrastructure (Terraform)
01:30 - Cluster ready (user-data scripts)
01:45 - Start benchmark test
03:45 - Test complete
04:00 - Generate report
04:30 - Teardown complete
```

### Step 5: View Results

```bash
# Find your experiment directory
ls -lt ~/.pulsar-aws-lab/

# View the HTML report
open ~/.pulsar-aws-lab/latest/report/index.html
```

**Report includes:**
- 📊 Throughput metrics (msgs/sec, MB/sec)
- 📉 Latency percentiles (p50, p95, p99, p99.9, max)
- 💰 Cost breakdown
- 📈 Interactive charts

## What's Next?

### Run More Comprehensive Tests

The POC test is minimal. For realistic testing:

```bash
# Run baseline test plan (multiple scenarios)
python scripts/orchestrator.py full \
  --test-plan config/test-plans/baseline.yaml

# This runs multiple tests:
# - Baseline performance
# - High load
# - Large messages
# - Multi-topic
```

### Customize Your Infrastructure

Edit `config/infrastructure.yaml`:

```yaml
experiment:
  id: "my-experiment"
  name: "My Pulsar Test"

pulsar_version: "3.0.0"

aws:
  region: "us-west-2"
  use_spot_instances: true  # Save ~70% on costs

compute:
  zookeeper:
    count: 3
    instance_type: "t3.micro"

  bookkeeper:
    count: 3
    instance_type: "t3.small"
    storage:
      volume_size: 20
      volume_type: "gp3"

  broker:
    count: 2
    instance_type: "t3.small"

  client:
    count: 1
    instance_type: "t3.small"
```

### Create Custom Test Plans

Edit `config/test-plans/my-test.yaml`:

```yaml
name: "my-test-plan"
description: "Custom test scenarios"

base_workload:
  topics: 1
  partitions_per_topic: 16
  message_size: 1024
  producers_per_topic: 1
  consumers_per_topic: 1
  test_duration_minutes: 5

test_runs:
  - name: "baseline"
    type: "fixed_rate"
    producer_rate: 10000

  - name: "high-load"
    type: "fixed_rate"
    producer_rate: 50000
    workload_overrides:
      producers_per_topic: 4
      consumers_per_topic: 4
```

Run it:
```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/my-test.yaml
```

### Manual Workflow (For Development)

Instead of the full lifecycle, run steps individually:

```bash
# 1. Setup infrastructure only
python scripts/orchestrator.py setup \
  --config config/infrastructure.yaml

# Experiment ID will be displayed (e.g., exp-20251006-143056)

# 2. Run tests on existing infrastructure
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml \
  --experiment-id latest

# 3. Generate report
python scripts/orchestrator.py report \
  --experiment-id latest

# 4. Teardown (when done)
python scripts/orchestrator.py teardown \
  --experiment-id latest
```

## Common Operations

### List Experiments

```bash
python scripts/orchestrator.py list
```

Output:
```
Experiment ID         Created              Status      Region
──────────────────────────────────────────────────────────────
exp-20251006-143056  2025-10-06 14:30:56  completed   us-west-2
exp-20251006-120015  2025-10-06 12:00:15  running     us-west-2
exp-20251005-210430  2025-10-05 21:04:30  completed   us-east-1
```

### Manage AMIs

```bash
# List available AMIs
python scripts/build-ami.py list --region us-west-2

# Build new AMI
python scripts/build-ami.py build --version 3.1.0 --region us-west-2

# Delete old AMI
python scripts/build-ami.py delete --ami-id ami-xxxxx
```

### Emergency Cleanup

If something goes wrong and resources aren't cleaned up:

```bash
# Dry-run to see what would be deleted
python scripts/cleanup_by_tag.py --experiment-id exp-20251006-143056

# Actually delete
python scripts/cleanup_by_tag.py --experiment-id exp-20251006-143056 --execute
```

## Cost Management

### Typical Costs (us-west-2, on-demand pricing)

**Infrastructure per hour:**
- 3x t3.micro ZooKeeper: $0.031/hr
- 3x t3.small BookKeeper: $0.063/hr
- 2x t3.small Broker: $0.042/hr
- 1x t3.small Client: $0.021/hr
- **Total: ~$0.16/hour** or **~$3.80/day**

**AMI Storage:**
- ~3 GB snapshot: ~$0.15/month

**Cost Optimization Tips:**
1. **Use spot instances** (save ~70%):
   ```yaml
   aws:
     use_spot_instances: true
     spot_max_price: "0.05"
   ```

2. **Always teardown** when done:
   ```bash
   python scripts/orchestrator.py teardown --experiment-id latest
   ```

3. **Use smaller instances** for POC:
   ```yaml
   compute:
     broker:
       instance_type: "t3.micro"  # Instead of t3.small
   ```

4. **Delete old AMIs**:
   ```bash
   python scripts/build-ami.py list
   python scripts/build-ami.py delete --ami-id ami-old-xxxxx
   ```

### Track Costs

```bash
# View costs for an experiment
python scripts/cost_tracker.py exp-20251006-143056
```

## Troubleshooting

### Problem: No AMI found

**Error:**
```
ERROR: No Pulsar AMI found matching pattern 'pulsar-base-*'
```

**Solution:**
```bash
# Build the AMI first
python scripts/build-ami.py build --version 3.0.0 --region us-west-2
```

### Problem: Terraform state issues

**Error:**
```
Error acquiring the state lock
```

**Solution:**
```bash
cd terraform
terraform init -reconfigure
cd ..
```

### Problem: Cluster not starting

**Solution:**
```bash
# Check cloud-init logs on instances
# SSH to an instance
ssh -i ~/.ssh/pulsar-lab-key.pem ec2-user@<instance-ip>

# View cloud-init logs
sudo tail -f /var/log/cloud-init-output.log

# Check service status
sudo systemctl status zookeeper
sudo systemctl status bookkeeper
sudo systemctl status broker
```

### Problem: Tests failing

**Common causes:**
1. **Cluster not ready** - Wait 2-3 minutes after infrastructure setup
2. **Insufficient resources** - Increase instance types
3. **Network issues** - Check security groups allow inter-component traffic

**Debug steps:**
```bash
# Check experiment logs
tail -f ~/.pulsar-aws-lab/latest/orchestrator.log

# Check benchmark logs on client instance
ssh -i ~/.ssh/pulsar-lab-key.pem ec2-user@<client-ip>
tail -f /tmp/benchmark-*.log
```

## Tips for Success

### 1. Start Small

Begin with the POC test to validate your setup:
```bash
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml
```

### 2. Use Tags in Shared Accounts

Add custom tags to identify your resources:
```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/poc.yaml \
  --tag team=data-platform \
  --tag owner=john.doe
```

### 3. Build AMI Once per Version

You only need to build the AMI once per Pulsar version:
```bash
# Build once
python scripts/build-ami.py build --version 3.0.0

# Use many times
python scripts/orchestrator.py full --test-plan config/test-plans/test1.yaml
python scripts/orchestrator.py full --test-plan config/test-plans/test2.yaml
```

### 4. Use Spot Instances for Cost Savings

Edit `config/infrastructure.yaml`:
```yaml
aws:
  use_spot_instances: true
  spot_max_price: "0.10"  # Or null for on-demand equivalent
```

### 5. Always Review Reports

Reports are in `~/.pulsar-aws-lab/<experiment-id>/report/index.html`

Key metrics to watch:
- **Throughput**: Did you hit target msgs/sec?
- **Latency p99**: Acceptable for your use case?
- **Error rate**: Should be near 0%
- **Cost efficiency**: Cost per million messages

## Next Steps

### Deep Dive Documentation

- 📖 [Complete README](../README.md) - Full project documentation
- 🔧 [AMI Build Guide](BUILD-AMI-GUIDE.md) - Comprehensive AMI management
- ⚡ [AMI Quick Reference](AMI-QUICK-REFERENCE.md) - Command cheat sheet

### Example Workflows

**Load Testing:**
```bash
# Test with increasing load
for rate in 10000 20000 50000 100000; do
  python scripts/orchestrator.py full \
    --test-plan config/test-plans/baseline.yaml \
    --override producer_rate=$rate
done
```

**Multi-Region Testing:**
```bash
# Build AMIs in multiple regions
for region in us-west-2 us-east-1 eu-west-1; do
  python scripts/build-ami.py build --version 3.0.0 --region $region
done

# Run tests in each region
for region in us-west-2 us-east-1 eu-west-1; do
  python scripts/orchestrator.py full \
    --test-plan config/test-plans/poc.yaml \
    --region $region
done
```

**Pulsar Version Comparison:**
```bash
# Build AMIs for different versions
python scripts/build-ami.py build --version 2.11.0
python scripts/build-ami.py build --version 3.0.0

# Test each version (update infrastructure.yaml between runs)
# Edit config/infrastructure.yaml to set pulsar_version
python scripts/orchestrator.py full --test-plan config/test-plans/baseline.yaml
```

## Getting Help

### Resources

- **GitHub Issues**: Report bugs and request features
- **Documentation**: Check `docs/` directory
- **Logs**: `~/.pulsar-aws-lab/*/orchestrator.log`

### Common Questions

**Q: How long does the first run take?**
A: ~25-30 minutes (15 min AMI build + 10 min test + 5 min teardown)

**Q: How much does it cost?**
A: ~$0.16/hour for default config, ~$3-5 for a full day of testing

**Q: Can I keep the cluster running?**
A: Yes! Skip the teardown step:
```bash
python scripts/orchestrator.py setup --config config/infrastructure.yaml
python scripts/orchestrator.py run --test-plan config/test-plans/baseline.yaml --experiment-id latest
# Don't run teardown - cluster stays up
```

**Q: How do I upgrade Pulsar version?**
A: Build a new AMI and update infrastructure.yaml:
```bash
python scripts/build-ami.py build --version 3.1.0
# Edit config/infrastructure.yaml: set pulsar_version: "3.1.0"
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml
```

**Q: Can I use existing VPC/subnets?**
A: Not yet, but you can modify the Terraform modules to support it

## Success Checklist

After your first run, you should have:

- ✅ AMI built and validated
- ✅ Successful POC test completion
- ✅ HTML report generated
- ✅ All AWS resources cleaned up (if using `full` command)
- ✅ Cost tracking data available

**Congratulations! You're ready to run comprehensive Pulsar performance tests!** 🎉
