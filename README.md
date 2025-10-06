# Pulsar AWS Lab

A reproducible, ephemeral Apache Pulsar testing framework on AWS with automated infrastructure deployment, load testing using OpenMessaging Benchmark, comprehensive reporting, and cost tracking.

## Features

- ✅ **Automated Infrastructure**: Terraform-based EC2 provisioning with cost optimization
- ✅ **Pulsar Deployment**: Ansible playbooks for complete cluster setup
- ✅ **Load Testing**: Integration with OpenMessaging Benchmark framework
- ✅ **Cost Tracking**: AWS Cost Explorer integration with detailed reporting
- ✅ **Comprehensive Reports**: HTML/CSV/JSON export with all metrics
- ✅ **Systematic Testing**: Test plan matrices for exploring parameter spaces
- ✅ **Full Lifecycle**: Automated setup → test → report → teardown

## Architecture

### Components

- **ZooKeeper**: Cluster coordination (default: 3 nodes)
- **BookKeeper**: Message storage layer (default: 3 bookies)
- **Broker**: Message routing and serving (default: 2 brokers)
- **Client**: OpenMessaging Benchmark execution (default: 1 node)

### Directory Structure

```
pulsar-aws-lab/
├── config/               # Configuration files
│   ├── infrastructure.yaml
│   ├── pulsar-cluster.yaml
│   ├── schema/          # JSON schemas
│   └── test-plans/      # Test scenario definitions
├── terraform/           # Infrastructure as Code
│   └── modules/         # Modular components
├── ansible/             # Configuration management
│   ├── playbooks/
│   └── roles/           # Component roles
├── scripts/             # Orchestration scripts
│   ├── orchestrator.py
│   ├── cost_tracker.py
│   └── report_generator.py
├── workloads/           # Benchmark workloads
└── reporting/           # Report templates
```

## Prerequisites

### Required Tools

```bash
# AWS CLI
aws --version  # >= 2.0

# Terraform
terraform --version  # >= 1.0

# Ansible
ansible --version  # >= 2.9

# Python
python3 --version  # >= 3.8
```

### AWS Setup

1. **Configure AWS credentials**:
   ```bash
   aws configure
   # Or use environment variables:
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-west-2
   ```

2. **SSH Key Setup** (automatic):
   The orchestrator will automatically create the SSH key pair if it doesn't exist.
   Default key name: `pulsar-lab-key` (saved to `~/.ssh/pulsar-lab-key.pem`)

   To use a different key, edit `config/infrastructure.yaml`:
   ```yaml
   compute:
     ssh_key_name: "your-key-name"
   ```

### Python Dependencies

```bash
cd scripts
pip install -r requirements.txt
```

## Quick Start

### 1. Run Proof of Concept Test

Execute a simple validation test (20k msgs/sec for 2 minutes):

```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/poc.yaml
```

**Add custom tags** to identify resources in a shared AWS account:

```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/poc.yaml \
  --tag team=data-platform \
  --tag owner=john.doe \
  --tag cost-center=engineering
```

This will:
1. Deploy AWS infrastructure
2. Install and configure Pulsar cluster
3. Run a single validation test (20k msgs/sec, 1KB messages, 2 min)
4. Generate comprehensive report
5. Destroy all resources

**For more comprehensive testing**, use the baseline test plan:
```bash
python scripts/orchestrator.py full \
  --test-plan config/test-plans/baseline.yaml
```

### 2. Manual Workflow

For more control, run each phase separately:

```bash
# Setup infrastructure and deploy Pulsar
python scripts/orchestrator.py setup \
  --config config/infrastructure.yaml

# The experiment ID will be displayed. Or list experiments:
python scripts/orchestrator.py list

# Run tests (use 'latest' or specific experiment ID)
python scripts/orchestrator.py run \
  --test-plan config/test-plans/baseline.yaml \
  --experiment-id latest

# Generate report
python scripts/orchestrator.py report \
  --experiment-id latest

# Teardown (when done) - use 'latest' for most recent
python scripts/orchestrator.py teardown \
  --experiment-id latest
```

**Tip**: Use `--experiment-id latest` to reference the most recent experiment, or use `list` to see all experiments.

## Configuration

### Infrastructure Configuration

Edit `config/infrastructure.yaml` to customize:

```yaml
experiment:
  id: "my-experiment"
  name: "My Pulsar Test"
  # Optional: Add tags to all resources (useful for shared accounts)
  tags:
    team: "data-platform"
    owner: "john.doe"
    cost_center: "engineering"

# Pulsar version to install (e.g., "3.0.0", "3.1.0", "3.2.0")
pulsar_version: "3.0.0"

aws:
  region: "us-west-2"
  use_spot_instances: false  # Set true for cost savings

compute:
  # Adjust instance types and counts
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

### Resource Tagging

**All AWS resources are automatically tagged with:**
- `Project`: "pulsar-aws-lab"
- `ExperimentID`: Auto-generated ID (e.g., "exp-20251005-143056")
- `Experiment`: Your experiment name from config
- `ManagedBy`: "terraform"

**Add custom tags in two ways:**

1. **Config file** (`config/infrastructure.yaml`):
   ```yaml
   experiment:
     tags:
       team: "data-platform"
       owner: "john.doe"
       cost_center: "engineering"
   ```

2. **Command line** (overrides config tags):
   ```bash
   python scripts/orchestrator.py full \
     --test-plan config/test-plans/poc.yaml \
     --tag team=data-platform \
     --tag owner=john.doe
   ```

Tags help identify resources in shared AWS accounts and enable cost tracking per team/owner.

### Pulsar Cluster Configuration

Edit `config/pulsar-cluster.yaml`:

```yaml
pulsar_version: "3.0.0"

zookeeper:
  heap_size: "512M"

bookkeeper:
  heap_size: "768M"
  direct_memory_size: "512M"

broker:
  heap_size: "1G"
  managed_ledger_cache_size_mb: 256
```

### Test Plans

Create custom test plans in `config/test-plans/`:

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

## Workloads

Pre-configured workloads in `workloads/`:

- `poc.yaml`: **Proof of concept** - 20k msgs/sec, 1KB messages, 2 min (recommended for initial validation)
- `simple-test.yaml`: Single topic, 10k msgs/sec
- `multi-topic.yaml`: 10 topics, distributed load
- `high-throughput.yaml`: Stress test, 100k msgs/sec
- `large-messages.yaml`: 64KB messages
- `latency-test.yaml`: Low load latency characterization

### Custom Workloads

Create workloads compatible with OpenMessaging Benchmark:

```yaml
name: my-workload
topics: 1
partitionsPerTopic: 16
messageSize: 1024
producersPerTopic: 1
consumerPerSubscription: 1
testDurationMinutes: 5
producerRate: 10000
```

## Cost Optimization

### Minimize Costs

1. **Use smallest instances**: Default config uses t3.micro/t3.small
2. **Enable spot instances**:
   ```yaml
   aws:
     use_spot_instances: true
     spot_max_price: "0.05"
   ```
3. **Reduce test duration**: Shorter tests = lower costs
4. **Auto-teardown**: Always use `full` lifecycle to ensure cleanup

### Cost Tracking

View costs for an experiment:

```bash
python scripts/cost_tracker.py my-experiment-id
```

Costs are automatically included in reports when using the orchestrator.

## Reports

Reports are generated in `~/.pulsar-aws-lab/<experiment-id>/report/`:

- `index.html`: Interactive HTML report
- `metrics.csv`: Raw metrics in CSV format
- `metrics.json`: JSON export
- `costs.json`: Cost breakdown
- `raw_data/`: Original benchmark outputs

### Metrics Captured

- **Throughput**: Messages/sec, MB/sec
- **Latency**: p50, p95, p99, p99.9, max
- **Errors**: Publish/consume failures
- **Costs**: Total cost, cost per million messages

## Error Handling & Automatic Cleanup

**Automatic Resource Cleanup**: If any error occurs during setup or testing, the orchestrator will **automatically clean up all AWS resources** to prevent unexpected costs.

How it works:
- On error, finds resources by `ExperimentID` tag (doesn't need Terraform state)
- Deletes resources in correct order: instances → volumes → network → VPC
- Logs cleanup progress
- Re-raises the original error for visibility

Example error flow:
```
2025-10-05 20:43:54 - ERROR - Ansible playbook failed
2025-10-05 20:43:54 - WARNING - Initiating automatic cleanup of resources...
============================================================
EMERGENCY CLEANUP: Finding resources by ExperimentID tag
============================================================
Found 9 resources to cleanup
Instances to terminate: ['i-abc123', 'i-def456', ...]
Waiting for instances to terminate...
Instances terminated.
Volumes to delete: ['vol-123', 'vol-456']
...
Emergency cleanup completed
```

You can also manually trigger emergency cleanup:
```bash
python scripts/cleanup_by_tag.py --experiment-id exp-20251005-143056 --execute
```

## Troubleshooting

### Managing Experiments

```bash
# List all experiments
python scripts/orchestrator.py list

# Teardown the latest experiment
python scripts/orchestrator.py teardown --experiment-id latest

# Teardown a specific experiment
python scripts/orchestrator.py teardown --experiment-id exp-20251005-123456
```

**Experiment Storage**: All experiments are stored in `~/.pulsar-aws-lab/`
- Each experiment has its own directory with logs and configs
- The `latest` symlink always points to the most recent experiment

### SSH Connection Issues

```bash
# Verify SSH key permissions
chmod 400 ~/.ssh/pulsar-lab-key.pem

# Test SSH access
ssh -i ~/.ssh/pulsar-lab-key.pem ec2-user@<instance-ip>
```

### Terraform State Issues

```bash
# If state gets corrupted
cd terraform
terraform init -reconfigure
```

### Stuck Resources or Lost State

If Terraform state is lost or teardown fails, use the emergency cleanup script:

```bash
# Dry run (see what would be deleted)
python scripts/cleanup_by_tag.py --experiment-id exp-20251005-123456

# Actually delete resources
python scripts/cleanup_by_tag.py --experiment-id exp-20251005-123456 --execute

# Use 'latest' (requires list command first)
python scripts/orchestrator.py list  # Find the experiment ID
python scripts/cleanup_by_tag.py --experiment-id <id> --execute
```

This script finds and deletes all AWS resources tagged with the ExperimentID.

### View Logs

```bash
# Orchestrator logs
tail -f ~/.pulsar-aws-lab/<experiment-id>/orchestrator.log

# SSH to instance and check service logs
ssh -i ~/.ssh/pulsar-lab-key.pem ec2-user@<ip>
sudo journalctl -u zookeeper -f
sudo journalctl -u bookkeeper -f
sudo journalctl -u broker -f
```

## Advanced Usage

### Manual Terraform Operations

```bash
cd terraform

# Plan infrastructure
terraform plan -var-file=../config/infrastructure.tfvars

# Apply
terraform apply -var-file=../config/infrastructure.tfvars

# Destroy
terraform destroy -var-file=../config/infrastructure.tfvars
```

### Manual Ansible Operations

```bash
cd ansible

# Deploy cluster
ansible-playbook -i inventory/terraform-inventory \
  playbooks/deploy.yaml \
  -e @../config/pulsar-cluster.yaml

# Check cluster health
ansible all -i inventory/terraform-inventory \
  -m shell -a "systemctl status broker"
```

### Run Individual Benchmarks

SSH to client node and run:

```bash
# Using wrapper script
run-benchmark workloads/simple-test.yaml my-test

# Or directly
cd /opt/openmessaging-benchmark/benchmark-framework
bin/benchmark \
  --drivers /opt/benchmark-configs/pulsar-driver.yaml \
  workloads/simple-test.yaml
```

## Development

### Validate Configurations

```bash
# Validate against JSON schema
python -c "
import yaml
import jsonschema

with open('config/infrastructure.yaml') as f:
    config = yaml.safe_load(f)

with open('config/schema/infrastructure.schema.json') as f:
    schema = yaml.safe_load(f)

jsonschema.validate(config, schema)
print('✓ Valid')
"
```

### Extend with Custom Metrics

Add custom metrics to `scripts/report_generator.py`:

```python
def calculate_custom_metrics(self, results: Dict) -> Dict:
    # Your custom metric calculations
    pass
```

## Contributing

Contributions welcome! Please:

1. Follow existing code style
2. Update tests for new features
3. Update documentation
4. Test with multiple configurations

## License

MIT License - see LICENSE file

## Resources

- [Apache Pulsar Documentation](https://pulsar.apache.org/docs/)
- [OpenMessaging Benchmark](https://openmessaging.cloud/docs/benchmarks/)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Ansible Documentation](https://docs.ansible.com/)

## Support

For issues and questions:

- GitHub Issues: [Report an issue](#)
- Pulsar Slack: [Join channel](https://pulsar.apache.org/community/#section-discussions)
- Mailing List: users@pulsar.apache.org
