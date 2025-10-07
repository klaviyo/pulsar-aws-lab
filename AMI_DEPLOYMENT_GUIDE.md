# AMI-Based Deployment Guide

## Quick Start

### Prerequisites

1. **Build the Pulsar base AMI** (one-time per region):
   ```bash
   cd packer
   packer build pulsar-base.pkr.hcl
   ```

2. **AWS credentials configured**:
   ```bash
   aws configure
   # Or export AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
   ```

3. **Python dependencies installed**:
   ```bash
   pip install -r scripts/requirements.txt
   ```

### Deploy and Test

```bash
# Full lifecycle: setup → test → teardown
python scripts/orchestrator.py full \
    --config config/infrastructure.yaml \
    --test-plan config/test-plans/poc.yaml

# Or run steps individually:
python scripts/orchestrator.py setup --config config/infrastructure.yaml
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml --experiment-id latest
python scripts/orchestrator.py teardown --experiment-id latest
```

## How It Works

### 1. AMI Validation
Before running Terraform, orchestrator validates that `pulsar-base-*` AMI exists:

```
Validating AMI availability in us-west-2...
Searching for AMI with pattern: pulsar-base-*
✓ Found AMI: pulsar-base-20250106-1234 (ami-0abc123)
  Created: 2025-01-06T12:34:56.000Z
  State: available
```

**Error if AMI not found:**
```
No AMI found matching pattern 'pulsar-base-*' in us-west-2.
Please ensure you have built the Pulsar base AMI using Packer.
Run: cd packer && packer build pulsar-base.pkr.hcl
Or check that the AMI exists in region us-west-2
```

### 2. Infrastructure Provisioning
Terraform provisions EC2 instances using the pre-baked AMI:

```bash
terraform init
terraform plan -var-file=<experiment>/terraform.tfvars.json
terraform apply -auto-approve
```

The AMI contains:
- Pulsar 3.0.0 pre-installed
- Systemd units configured (zookeeper, bookkeeper, pulsar-broker)
- Services set to auto-start on boot
- OpenMessaging Benchmark framework on client nodes

### 3. Cluster Readiness Check
Orchestrator waits for cluster to be ready in 3 stages:

#### Stage 1: EC2 Instances Running
```
Step 1/3: Waiting for EC2 instances to reach 'running' state...
Found 8 instances:
  i-0abc123: running
  i-0def456: running
  ...
✓ All instances are running
```

#### Stage 2: SSM Agent Registration
```
Step 2/3: Waiting for SSM agent registration...
SSM status: 8/8 instances online
✓ All instances registered with SSM
  i-0abc123: Online
  i-0def456: Online
  ...
```

#### Stage 3: Service Health Checks
```
Step 3/3: Waiting for Pulsar services to be active...
✓ i-0abc123 (zookeeper): zookeeper.service is active
✓ i-0def456 (bookkeeper): bookkeeper.service is active
✓ i-0ghi789 (broker): pulsar-broker.service is active

Verifying service health endpoints...
  ✓ ZooKeeper health check passed (ruok → imok)
  ✓ BookKeeper port check passed (3181)
  ✓ Broker health endpoint returned 200 (/admin/v2/brokers/health)

============================================================
CLUSTER READY! (Total time: 145s)
============================================================
```

### 4. Benchmark Execution
Tests run via AWS SSM RunCommand (no SSH required):

```bash
# Upload workload config
cat > /tmp/workload_baseline.yaml << 'EOF'
name: baseline
topics: 10
...
EOF

# Run benchmark
cd /opt/openmessaging-benchmark/benchmark-framework
bin/benchmark --drivers /opt/benchmark-configs/pulsar-driver.yaml \
    /tmp/workload_baseline.yaml --output /opt/benchmark-results/baseline.json

# Download results
cat /opt/benchmark-results/baseline.json
```

## Health Check Details

### ZooKeeper
- **Service**: `zookeeper.service`
- **Port**: 2181
- **Health check**: `echo ruok | nc localhost 2181`
- **Expected**: `imok`

### BookKeeper
- **Service**: `bookkeeper.service`
- **Port**: 3181
- **Health check**: `nc -zv localhost 3181`
- **Expected**: Connection successful

### Broker
- **Service**: `pulsar-broker.service`
- **Port**: 8080 (HTTP), 6650 (binary)
- **Health check**: `curl http://localhost:8080/admin/v2/brokers/health`
- **Expected**: HTTP 200

### Client
- **Service**: None (benchmark execution node)
- **Port**: N/A
- **Health check**: None

## Troubleshooting

### AMI Not Found
```
Error: No AMI found matching pattern 'pulsar-base-*' in us-west-2
```

**Solution:**
1. Check AMI exists: `aws ec2 describe-images --owners self --filters Name=name,Values=pulsar-base-*`
2. Build AMI: `cd packer && packer build pulsar-base.pkr.hcl`
3. Check correct region: AMIs are region-specific

### Service Not Starting
```
✗ i-0abc123 (broker): pulsar-broker.service not active (inactive)
```

**Solution:**
1. Check logs: `python scripts/orchestrator.py run --experiment-id latest`
2. SSH to instance: `aws ssm start-session --target i-0abc123`
3. Check service status: `systemctl status pulsar-broker.service`
4. Check logs: `journalctl -u pulsar-broker.service -n 100`

### Timeout Waiting for Cluster
```
Error: Timeout waiting for Pulsar services to be ready after 600s
```

**Solution:**
1. Increase timeout: Edit `wait_for_cluster(timeout_seconds=600)` in orchestrator.py
2. Check instance types: t3.micro may be too small for heavy workloads
3. Check AMI: Rebuild AMI to ensure services are properly configured

### SSM Agent Not Registering
```
Error: Timeout waiting for SSM agent registration after 600s
```

**Solution:**
1. Check IAM role: Instances need `AmazonSSMManagedInstanceCore` policy
2. Check VPC: Instances need internet access or VPC endpoints for SSM
3. Check security group: Allow outbound HTTPS (443) for SSM communication

## Performance Optimization

### Faster Deployments
The AMI-based approach is significantly faster than Ansible:

| Phase                  | Ansible-based | AMI-based | Savings |
|------------------------|---------------|-----------|---------|
| Terraform provision    | 2-3 min       | 2-3 min   | 0       |
| Software installation  | 5-7 min       | 0         | 5-7 min |
| Service configuration  | 2-3 min       | 0         | 2-3 min |
| Health checks          | 90s fixed     | 30-90s    | 0-60s   |
| **Total**              | **10-14 min** | **3-5 min**| **7-10 min** |

### Exponential Backoff
Health checks use exponential backoff (5s → 30s max) to balance responsiveness and API rate limits:

```python
backoff_seconds = 5  # Start with 5s
while not ready:
    time.sleep(backoff_seconds)
    backoff_seconds = min(backoff_seconds * 1.5, 30)  # Cap at 30s
```

### Parallel Checks
Service health checks run sequentially by component, but could be parallelized for faster validation.

## Advanced Usage

### Custom AMI Pattern
```python
orchestrator = Orchestrator(experiment_id="my-test")
orchestrator.validate_ami_exists(region="us-west-2", ami_name_pattern="my-custom-ami-*")
```

### Custom Timeout
```python
# In orchestrator.py setup() method:
self.wait_for_cluster(aws_region, timeout_seconds=900)  # 15 minutes
```

### Skip Health Checks
Not recommended, but you can comment out health endpoint verification:

```python
# In wait_for_cluster() method:
# self._verify_health_endpoints(region, component_instances)
```

## Monitoring

### Real-time Progress
```bash
# Watch orchestrator log
tail -f ~/.pulsar-aws-lab/latest/orchestrator.log

# Watch SSM commands
aws ssm list-commands --filters Key=InvokedAfter,Values=$(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S)
```

### Health Dashboard
```bash
# Check all services on all instances
for component in zookeeper bookkeeper broker; do
    echo "=== $component ==="
    aws ec2 describe-instances \
        --filters Name=tag:Component,Values=$component Name=instance-state-name,Values=running \
        --query 'Reservations[].Instances[].InstanceId' \
        --output text | xargs -I{} aws ssm send-command \
            --instance-ids {} \
            --document-name AWS-RunShellScript \
            --parameters "commands=[\"systemctl is-active ${component}.service\"]"
done
```

## Cleanup

### Normal Teardown
```bash
python scripts/orchestrator.py teardown --experiment-id latest
```

### Emergency Cleanup (if Terraform state lost)
```bash
python scripts/cleanup_by_tag.py --experiment-id <exp-id> --execute
```

### Manual Cleanup
```bash
# Find resources by tag
aws ec2 describe-instances --filters Name=tag:ExperimentID,Values=<exp-id>
aws ec2 terminate-instances --instance-ids i-xxx i-yyy
aws ec2 delete-security-group --group-id sg-xxx
aws ec2 delete-subnet --subnet-id subnet-xxx
aws ec2 delete-internet-gateway --internet-gateway-id igw-xxx
aws ec2 delete-vpc --vpc-id vpc-xxx
```

## Best Practices

1. **Always validate AMI before deployment**: The orchestrator does this automatically
2. **Monitor health check logs**: Useful for debugging service startup issues
3. **Use appropriate instance types**: t3.micro is minimal, use t3.small+ for production tests
4. **Tag experiments properly**: Makes cleanup and cost tracking easier
5. **Clean up experiments**: Don't leave instances running accidentally
6. **Test in dev region first**: Validate AMI works before deploying to production region
7. **Version AMIs**: Include date/commit hash in AMI name for tracking

## Next Steps

1. **Build AMI**: `cd packer && packer build pulsar-base.pkr.hcl`
2. **Run first test**: `python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml`
3. **Review results**: Check `~/.pulsar-aws-lab/latest/benchmark_results/`
4. **Iterate**: Modify test plans, re-run, compare results
5. **Clean up**: `python scripts/orchestrator.py teardown --experiment-id latest`
