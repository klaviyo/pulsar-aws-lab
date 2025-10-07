# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pulsar AWS Lab is a reproducible, ephemeral Apache Pulsar testing framework on AWS. It automates the complete lifecycle of Pulsar cluster deployment, load testing using OpenMessaging Benchmark framework, comprehensive reporting, and teardown with cost tracking.

**Architecture Note**: This project uses **pre-baked AMIs** built with Packer. There is NO Ansible deployment - all Pulsar services are pre-installed and configured to start automatically via systemd on AMI-based instances.

## Architecture

### High-Level Components

1. **AMI Build System** (`packer/`, `scripts/build-ami.py`)
   - Packer templates build Amazon Linux 2023 AMIs with pre-installed Pulsar
   - Includes Pulsar binaries, Java runtime, OpenMessaging Benchmark framework
   - Systemd service templates for automatic service startup
   - Python CLI tool for AMI lifecycle management (build, list, validate, delete)
   - AMIs tagged with Pulsar version for easy discovery

2. **Configuration System** (`config/`)
   - Infrastructure config: EC2 instance types, storage, IOPS, networking (defaults to smallest viable instances)
   - Pulsar cluster config: bookies, brokers, zookeepers counts and JVM settings
   - Test plans: workload matrices and variation strategies
   - Pulsar version configured in `infrastructure.yaml`

3. **Infrastructure as Code** (`terraform/`)
   - Modular Terraform design (network, compute, storage modules)
   - Cost allocation tags on all resources (experiment-id, component, timestamp)
   - Supports spot instances for cost optimization
   - Uses data source to automatically find latest Pulsar AMI
   - State management (local by default, S3 backend optional)

4. **Orchestration** (`scripts/orchestrator.py`)
   - Python-based workflow: setup → wait_for_cluster → test → report → teardown
   - AMI validation before deployment
   - SSM-based health checks (no interactive SSH sessions)
   - Test matrix execution using SSM SendCommand
   - AWS cost tracking integration
   - Emergency cleanup via tag-based resource discovery

5. **Testing**
   - Leverages OpenMessaging Benchmark framework (pre-installed in AMI)
   - Workload files uploaded via SSM SendCommand
   - Configurable workloads: topics, partitions, message sizes, producer/consumer counts
   - Test types: fixed rate, ramp up, scale to failure, latency sensitivity

### Workflow

1. **Build AMI** (one-time): Packer builds base AMI with Pulsar pre-installed
2. **Setup**: Terraform provisions EC2 infrastructure using pre-baked AMI
3. **Wait**: Orchestrator waits for instances to boot and services to start (systemd)
4. **Test**: Orchestrator runs test matrix via SSM, collecting metrics
5. **Report**: Generates comprehensive offline report with costs
6. **Teardown**: Destroys all resources via Terraform (with emergency tag-based cleanup)

### Key Design Principles

- **Cost Optimization**: Defaults to t3.micro/t3.small instances, supports spot instances
- **Reproducibility**: All configs version controlled, deterministic deployments
- **Safety**: Confirmation prompts, cost estimates before deployment
- **Extensibility**: Modular design for custom metrics and reports

## Development Commands

### Prerequisites
```bash
# Install Python dependencies
pip install -r scripts/requirements.txt

# Install Packer (for AMI building)
# macOS: brew install packer
# Linux: See https://www.packer.io/downloads
# Windows: choco install packer

# Install Terraform (for infrastructure)
# macOS: brew install terraform
# Linux: See https://www.terraform.io/downloads

# Configure AWS credentials
aws configure
# Or export credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-west-2
```

### AMI Management

```bash
# Build a new Pulsar AMI (one-time setup or version upgrades)
python scripts/build-ami.py build --version 3.0.0 --region us-west-2

# List available AMIs
python scripts/build-ami.py list --region us-west-2

# Validate an AMI (launches test instance, verifies installation)
python scripts/build-ami.py validate --ami-id ami-0123456789abcdef0

# Delete an old AMI
python scripts/build-ami.py delete --ami-id ami-0123456789abcdef0 --region us-west-2
```

### Main Operations

```bash
# Full lifecycle (setup → test → report → teardown)
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml

# With custom tags for cost tracking
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml --tag team=engineering --tag project=perf-testing

# List experiments
python scripts/orchestrator.py list

# Individual steps
python scripts/orchestrator.py setup --config config/infrastructure.yaml
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml --experiment-id latest
python scripts/orchestrator.py report --experiment-id latest
python scripts/orchestrator.py teardown --experiment-id latest

# Terraform operations (manual - not typically needed)
cd terraform
terraform init
terraform plan
terraform apply
terraform destroy
```

### Configuration

- `config/infrastructure.yaml`: EC2 types, counts, storage, networking, Pulsar version
- `config/pulsar-cluster.yaml`: Pulsar component settings (JVM, storage, etc.)
- `config/test-plans/*.yaml`: Test scenario definitions
- `workloads/*.yaml`: Custom workload specifications

**Note**: Pulsar version is configured in `infrastructure.yaml` and overrides any version set in `pulsar-cluster.yaml`

## Pulsar Components

- **ZooKeeper**: Cluster coordination (default: 3 nodes)
- **BookKeeper**: Message storage layer (default: 3 bookies)
- **Broker**: Message routing and serving (default: 3 brokers)
- **Client**: OpenMessaging Benchmark execution nodes (default: 2 nodes)

### Port Configuration
- 6650: Pulsar broker binary protocol
- 8080: Pulsar broker HTTP
- 2181: ZooKeeper client
- 3181: BookKeeper client

## Test Plans

Test plans define variations to systematically explore:
- Infrastructure: instance types, storage types, cluster size
- Workload: topics, partitions, message size, producer/consumer counts, rates
- Pulsar config: JVM settings, retention policies, replication factors

Each test run generates:
- Throughput metrics (msgs/sec, MB/sec)
- Latency percentiles (p50, p95, p99, p99.9, max)
- Cost analysis (total cost, cost per million messages)
- Offline report package (HTML + raw data + configs)

## Cost Management

- All resources tagged with experiment-id for tracking
- Cost estimates shown before deployment
- Post-experiment cost report via AWS Cost Explorer API
- Automatic cleanup on completion or failure

## Troubleshooting

### Common Issues

**AMI Not Found Error**
```bash
# Build the Pulsar base AMI first
python scripts/build-ami.py build --version 3.0.0 --region us-west-2

# Verify AMI exists
python scripts/build-ami.py list --region us-west-2
```

**Instance Health Check Failures**
- Check orchestrator logs: `~/.pulsar-aws-lab/<experiment-id>/orchestrator.log`
- Verify SSM agent is running on instances
- Check security group rules allow necessary ports
- Ensure IAM role `SSMManagedInstanceCore` exists

**Stuck Resources After Failed Deployment**
```bash
# Emergency cleanup by experiment ID
python scripts/cleanup_by_tag.py --experiment-id <exp-id> --execute

# Or use orchestrator teardown
python scripts/orchestrator.py teardown --experiment-id <exp-id>
```

### Important Files

- Orchestrator logs: `~/.pulsar-aws-lab/<experiment-id>/orchestrator.log`
- Experiment results: `~/.pulsar-aws-lab/<experiment-id>/`
- Terraform state: `terraform/terraform.tfstate`
- Terraform variables: `~/.pulsar-aws-lab/<experiment-id>/terraform.tfvars.json`
- Latest experiment symlink: `~/.pulsar-aws-lab/latest`

### Key Architectural Changes

**⚠️ IMPORTANT: No Ansible in Current Architecture**

This project previously used Ansible for configuration management but has migrated to a **pre-baked AMI approach**:

- ❌ **OLD**: Terraform provisions bare instances → Ansible installs Pulsar
- ✅ **NEW**: Packer builds AMI with Pulsar → Terraform provisions from AMI → systemd auto-starts services

If you see references to Ansible in code or docs, they are outdated and should be removed. The `ansible/` directory exists only for reference and is NOT used in the current workflow.
