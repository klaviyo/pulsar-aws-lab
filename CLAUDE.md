# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pulsar AWS Lab is a reproducible, ephemeral Apache Pulsar testing framework on AWS. It automates the complete lifecycle of Pulsar cluster deployment, load testing using OpenMessaging Benchmark framework, comprehensive reporting, and teardown with cost tracking.

## Architecture

### High-Level Components

1. **Configuration System** (`config/`)
   - Infrastructure config: EC2 instance types, storage, IOPS, networking (defaults to smallest viable instances)
   - Pulsar cluster config: bookies, brokers, zookeepers counts and JVM settings
   - Test plans: workload matrices and variation strategies
   - JSON schemas for validation

2. **Infrastructure as Code** (`terraform/`)
   - Modular Terraform design (network, compute, storage)
   - Cost allocation tags on all resources (experiment-id, component, timestamp)
   - Supports spot instances for cost optimization
   - State management with S3 backend

3. **Configuration Management** (`ansible/`)
   - Roles for each Pulsar component (ZooKeeper, BookKeeper, Broker, Client)
   - Dynamic inventory from Terraform outputs
   - Installs and configures Pulsar cluster
   - Deploys OpenMessaging Benchmark framework on client nodes

4. **Orchestration** (`scripts/`)
   - Python-based workflow: setup → test → report → teardown
   - Test matrix execution engine
   - AWS cost tracking integration
   - Report generation (offline HTML package)

5. **Testing**
   - Leverages OpenMessaging Benchmark framework
   - Configurable workloads: topics, partitions, message sizes, producer/consumer counts
   - Test types: fixed rate, ramp up, scale to failure, latency sensitivity

### Workflow

1. **Setup**: Terraform provisions EC2 infrastructure with cost tags
2. **Deploy**: Ansible configures and starts Pulsar cluster
3. **Test**: Orchestrator runs test matrix, collecting metrics
4. **Report**: Generates comprehensive offline report with costs
5. **Teardown**: Destroys all resources systematically

### Key Design Principles

- **Cost Optimization**: Defaults to t3.micro/t3.small instances, supports spot instances
- **Reproducibility**: All configs version controlled, deterministic deployments
- **Safety**: Confirmation prompts, cost estimates before deployment
- **Extensibility**: Modular design for custom metrics and reports

## Development Commands

### Prerequisites
```bash
# Install dependencies
pip install -r scripts/requirements.txt
terraform init
ansible-galaxy install -r ansible/requirements.yml  # If external roles needed
```

### AWS Setup
```bash
# Configure AWS credentials
aws configure
# Or export credentials
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-west-2
```

### Main Operations

```bash
# Full lifecycle (setup → test → report → teardown)
python scripts/orchestrator.py full --test-plan config/test-plans/poc.yaml

# List experiments
python scripts/orchestrator.py list

# Individual steps
python scripts/orchestrator.py setup --config config/infrastructure.yaml
python scripts/orchestrator.py run --test-plan config/test-plans/poc.yaml --experiment-id latest
python scripts/orchestrator.py report --experiment-id latest
python scripts/orchestrator.py teardown --experiment-id latest

# Terraform operations (manual)
cd terraform
terraform plan -var-file=../config/infrastructure.tfvars
terraform apply -var-file=../config/infrastructure.tfvars
terraform destroy -var-file=../config/infrastructure.tfvars

# Ansible operations (manual)
cd ansible
ansible-playbook -i inventory/terraform-inventory playbooks/deploy.yaml
ansible-playbook -i inventory/terraform-inventory playbooks/configure-pulsar.yaml
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

- Logs: Check `~/.pulsar-aws-lab/logs/<experiment-id>/`
- Terraform state: `terraform/terraform.tfstate`
- Ansible output: Verbose mode with `-vvv`
- Stuck resources: Manual cleanup via AWS console or `terraform destroy -force`
