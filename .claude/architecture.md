# Architecture Memory Bank

## System Architecture Overview

Pulsar AWS Lab is an **AMI-based ephemeral testing framework** for Apache Pulsar on AWS.

### Core Architectural Pattern: Pre-Baked AMIs

**Key Decision**: Use pre-baked AMIs instead of runtime configuration management (Ansible).

**Rationale**:
- Faster deployment (seconds vs minutes)
- More reliable (no configuration drift)
- Simpler debugging (AMI validation before deployment)
- Better reproducibility (immutable infrastructure)

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AMI Build Phase (Packer)                 │
│  Amazon Linux 2023 → Install Pulsar → Install Benchmark     │
│         → Configure systemd → Create AMI                     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              Infrastructure Phase (Terraform)                │
│  VPC → Subnets → Security Groups → EC2 (from AMI)           │
│         → EBS Volumes → Cost Tags                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│           Cluster Initialization (Orchestrator)              │
│  Wait for EC2 running → Wait for SSM → Verify systemd       │
│         → Health check endpoints                             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              Testing Phase (Orchestrator + SSM)              │
│  Generate workload → Upload via SSM → Run benchmark         │
│         → Download results                                   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                  Teardown Phase (Terraform)                  │
│  Terraform destroy → Tag-based cleanup fallback              │
└─────────────────────────────────────────────────────────────┘
```

## Module Breakdown

### 1. Packer AMI Builder (`packer/`)

**Purpose**: Build immutable, versioned Pulsar AMIs

**Key Files**:
- `packer/pulsar-base.pkr.hcl`: Main Packer template
- `packer/scripts/install-pulsar.sh`: Pulsar installation script
- `packer/scripts/install-benchmark.sh`: Benchmark framework setup
- `packer/files/systemd/*.service.tpl`: Systemd service templates

**Key Patterns**:
- Uses Amazon Linux 2023 as base
- **SSM-based communication**: Uses AWS Systems Manager instead of SSH for build instance access
- Requires `SSMManagedInstanceCore` IAM instance profile
- No SSH ports opened during build (more secure)
- Installs Pulsar to `/opt/pulsar`
- Installs OpenMessaging Benchmark to `/opt/openmessaging-benchmark`
- Places systemd templates in `/opt/pulsar-templates/systemd/`
- Tags AMIs with `PulsarVersion` for discovery

### 2. Terraform Infrastructure (`terraform/`)

**Purpose**: Provision AWS infrastructure with cost tracking

**Modular Design**:
```
terraform/
├── main.tf              # Provider, data sources, module orchestration
├── variables.tf         # Input variable definitions
├── outputs.tf           # Output values for orchestrator
└── modules/
    ├── network/         # VPC, subnets, security groups
    ├── compute/         # EC2 instances (ZK, BK, Broker, Client)
    └── storage/         # EBS volumes for BookKeeper
```

**Key Patterns**:
- Uses `data "aws_ami"` to auto-discover latest Pulsar AMI
- All resources tagged with `ExperimentID` for cleanup
- Component-specific tags: `Component=zookeeper|bookkeeper|broker|client`
- Spot instance support via `use_spot_instances` variable
- Modular design allows swapping components independently

### 3. Orchestrator (`scripts/orchestrator.py`)

**Purpose**: Workflow automation and cluster lifecycle management

**Key Classes**:
- `Orchestrator`: Main workflow controller
  - `setup()`: Terraform apply + wait for cluster
  - `wait_for_cluster()`: Health checks via SSM
  - `run_tests()`: Execute test matrix via SSM SendCommand
  - `teardown()`: Terraform destroy + emergency cleanup
  - `emergency_cleanup()`: Tag-based resource cleanup (no Terraform state needed)

**State Management**:
- Experiment directories: `~/.pulsar-aws-lab/<experiment-id>/`
- Latest symlink: `~/.pulsar-aws-lab/latest`
- Terraform vars: `<experiment-dir>/terraform.tfvars.json`
- Logs: `<experiment-dir>/orchestrator.log`

**Key Patterns**:
- SSM-based operations (no SSH keys needed)
- Exponential backoff for status checks
- Three-phase health validation:
  1. EC2 instance state = running
  2. SSM agent online
  3. Systemd services active
- Health endpoint verification (ZK, BK, Broker)

### 4. AMI Manager (`scripts/build-ami.py`)

**Purpose**: CLI tool for AMI lifecycle operations

**Operations**:
- `build`: Create new AMI with Packer
- `list`: Display available AMIs (with caching)
- `validate`: Launch test instance and verify installation
- `delete`: Deregister AMI and delete snapshots

**Key Patterns**:
- Rich console output with progress bars
- Local caching (5-minute TTL) for list operations
- Comprehensive validation (Pulsar binaries, systemd, Java, benchmark framework)
- Dry-run mode for testing

## Data Flow

### Experiment Lifecycle

```
User Command
    ↓
Orchestrator.full_lifecycle()
    ↓
├── setup()
│   ├── validate_ami_exists()       # Ensure AMI is built
│   ├── _generate_tfvars()          # Config → Terraform vars
│   ├── run_terraform("apply")      # Provision infrastructure
│   └── wait_for_cluster()          # Health checks
│       ├── Wait for EC2 running
│       ├── Wait for SSM online
│       └── Verify systemd services
│
├── run_tests()
│   ├── Load test plan YAML
│   ├── For each test:
│   │   ├── _generate_workload()    # Test plan → workload YAML
│   │   ├── _ssm_run_command()      # Upload workload
│   │   ├── _ssm_run_command()      # Run benchmark
│   │   └── _ssm_run_command()      # Download results
│   └── Save to <experiment-dir>/benchmark_results/
│
├── generate_report()               # TODO: Not implemented yet
│
└── teardown()
    ├── run_terraform("destroy")
    └── emergency_cleanup()         # Fallback: tag-based cleanup
```

## Key Design Decisions

### 1. AMI-Based vs Ansible

**Decision**: Pre-baked AMIs with Packer
**Rejected**: Ansible runtime configuration

**Rationale**:
- Deployment time: 2-3 minutes (AMI) vs 15-20 minutes (Ansible)
- Reliability: Immutable AMI vs potential Ansible failures
- Debugging: Validate AMI once vs troubleshoot per-deployment
- Cost: Faster deployment = lower cloud costs

**Trade-offs**:
- AMI builds take 10-15 minutes (one-time per version)
- Configuration changes require AMI rebuild
- Regional AMI replication needed for multi-region

### 2. SSM-Based Operations

**Decision**: AWS Systems Manager (SSM) for all remote operations
**Rejected**: SSH with key management

**Rationale**:
- No SSH key distribution/rotation needed
- Works with private subnets (no bastion required)
- Integrated AWS logging (CloudWatch)
- IAM-based authentication and authorization
- Command history tracking

**Implementation**:
- All instances require `SSMManagedInstanceCore` IAM role
- Operations use `send_command` + `get_command_invocation` pattern
- Exponential backoff for command status polling

### 3. Tag-Based Resource Cleanup

**Decision**: All resources tagged with `ExperimentID`
**Rejected**: Rely solely on Terraform state

**Rationale**:
- Handles Terraform state loss/corruption
- Enables emergency cleanup without Terraform
- Supports cross-region resource discovery
- Cost tracking via AWS Cost Explorer tags

**Implementation**:
- `cleanup_by_tag.py`: Standalone cleanup tool
- `emergency_cleanup()`: Integrated in orchestrator
- Tag propagation: All resources inherit experiment tags

## Service Architecture

### Pulsar Cluster Layout

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  ZooKeeper   │  │  ZooKeeper   │  │  ZooKeeper   │
│   Node 1     │  │   Node 2     │  │   Node 3     │
│  Port: 2181  │  │  Port: 2181  │  │  Port: 2181  │
└──────────────┘  └──────────────┘  └──────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │ (Coordination)
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  BookKeeper  │  │  BookKeeper  │  │  BookKeeper  │
│   Bookie 1   │  │   Bookie 2   │  │   Bookie 3   │
│  Port: 3181  │  │  Port: 3181  │  │  Port: 3181  │
│ + EBS Volume │  │ + EBS Volume │  │ + EBS Volume │
└──────────────┘  └──────────────┘  └──────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │ (Storage)
        ┌─────────────────┴─────────────────┐
        ↓                                   ↓
┌──────────────┐                    ┌──────────────┐
│    Broker    │                    │    Broker    │
│   Node 1     │                    │   Node 2     │
│ Port: 6650   │                    │ Port: 6650   │
│ Port: 8080   │                    │ Port: 8080   │
└──────────────┘                    └──────────────┘
        │                                   │
        └─────────────────┬─────────────────┘
                          │ (Service)
                          ↓
                  ┌──────────────┐
                  │    Client    │
                  │   Node 1     │
                  │ + Benchmark  │
                  └──────────────┘
```

### Systemd Service Management

Services auto-start via systemd on boot:

**ZooKeeper**: `zookeeper.service`
**BookKeeper**: `bookkeeper.service`
**Broker**: `pulsar-broker.service`
**Client**: No auto-start services (on-demand benchmark execution)

Service templates stored at: `/opt/pulsar-templates/systemd/*.service.tpl`

Terraform user-data scripts:
1. Render templates with instance-specific config (IPs, IDs)
2. Install to `/etc/systemd/system/`
3. Enable and start services

## Testing Strategy

### Test Matrix Execution

Test plans define parameter variations:
- Infrastructure: instance types, storage configs
- Workload: topics, partitions, message sizes, rates
- Pulsar config: JVM settings, replication factors

Each test run:
1. Generates OpenMessaging Benchmark workload YAML
2. Uploads to client instance via SSM
3. Executes benchmark via `bin/benchmark` command
4. Downloads JSON results
5. Stores in `<experiment-dir>/benchmark_results/`

### OpenMessaging Benchmark Integration

**Installation**: Pre-installed in AMI at `/opt/openmessaging-benchmark/`

**Driver Config**: Pulsar driver config at `/opt/benchmark-configs/pulsar-driver.yaml`

**Execution Pattern**:
```bash
cd /opt/openmessaging-benchmark/benchmark-framework
sudo bin/benchmark \
  --drivers /opt/benchmark-configs/pulsar-driver.yaml \
  /tmp/workload.yaml \
  --output /opt/benchmark-results/test.json
```

## Cost Management

### Tagging Strategy

All resources tagged with:
- `ExperimentID`: Unique experiment identifier
- `Component`: zookeeper|bookkeeper|broker|client
- `ManagedBy`: terraform
- `Project`: pulsar-aws-lab

Custom tags supported via `--tag` CLI argument:
```bash
--tag team=engineering --tag cost_center=perf-testing
```

### Cost Tracking

- Pre-deployment: Terraform plan shows resource counts
- Post-deployment: AWS Cost Explorer API queries by `ExperimentID` tag
- Report generation: Cost per experiment, cost per million messages

### Cost Optimization Features

- Default to smallest viable instances (t3.micro, t3.small)
- Spot instance support (`use_spot_instances=true`)
- Automatic teardown on completion/failure
- Emergency cleanup for stuck resources

## Future Architecture Considerations

### Current Limitations

1. **Single Region**: AMIs are region-specific
2. **No Multi-Tenancy**: One experiment per AWS account at a time
3. **Manual AMI Versioning**: No automated AMI lifecycle management
4. **Limited Observability**: No integrated monitoring/dashboards

### Planned Improvements

1. **Report Generation**: HTML/PDF reports with graphs (currently stub)
2. **AMI Replication**: Cross-region AMI copying for multi-region tests
3. **Metrics Collection**: CloudWatch/Prometheus integration
4. **Cost Forecasting**: Estimate costs before deployment
5. **Test Result Database**: Store historical test results for comparison
