# Design Patterns Memory Bank

## Core Patterns

### 1. Immutable Infrastructure Pattern

**Implementation**: Pre-baked AMIs with Packer

**Key Characteristics**:
- Infrastructure components never modified after creation
- Changes require building new AMI version
- Configuration baked into image, not applied at runtime
- Version control via AMI tags

**Benefits**:
- Eliminates configuration drift
- Faster deployment (no runtime setup)
- Easier rollback (switch AMI ID)
- Testable infrastructure (validate AMI before use)

**Files**:
- `packer/pulsar-base.pkr.hcl`: AMI build definition
- `scripts/build-ami.py`: AMI lifecycle management

### 2. SSM-First Operations Pattern

**Implementation**: All remote operations via AWS Systems Manager

**Key Characteristics**:
- No SSH key management
- IAM-based authentication
- Works in private subnets without bastion
- Integrated CloudWatch logging

**Pattern**:
```python
def _ssm_run_command(ssm_client, instance_id, commands):
    # Send command
    response = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': commands}
    )
    command_id = response['Command']['CommandId']

    # Poll for completion
    while True:
        invocation = ssm_client.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id
        )
        if invocation['Status'] == 'Success':
            return invocation['StandardOutputContent']
        elif invocation['Status'] in ['Failed', 'Cancelled', 'TimedOut']:
            raise Error(invocation['StandardErrorContent'])
        time.sleep(backoff)
```

**Files**:
- `scripts/orchestrator.py:944-1036`: SSM command execution
- `scripts/orchestrator.py:512-578`: Service status checks via SSM

### 3. Tag-Based Resource Management

**Implementation**: Uniform tagging for lifecycle and cost tracking

**Required Tags** (all resources):
- `ExperimentID`: Unique experiment identifier
- `Component`: Resource role (zookeeper|bookkeeper|broker|client)
- `ManagedBy`: terraform
- `Project`: pulsar-aws-lab

**Benefits**:
- Enables emergency cleanup without Terraform state
- Cost tracking via AWS Cost Explorer
- Cross-region resource discovery
- Organized AWS console views

**Pattern**:
```hcl
# Terraform default tags
default_tags {
  tags = merge(
    {
      Project      = "pulsar-aws-lab"
      ExperimentID = var.experiment_id
      ManagedBy    = "terraform"
    },
    var.additional_tags
  )
}
```

**Files**:
- `terraform/main.tf:22-32`: Default tags definition
- `scripts/cleanup_by_tag.py`: Tag-based resource discovery
- `scripts/orchestrator.py:724-757`: Emergency cleanup implementation

### 4. Exponential Backoff for Status Checks

**Implementation**: Progressive wait times for async operations

**Pattern**:
```python
backoff_seconds = 5
max_backoff = 30

while time.time() - start_time < timeout_seconds:
    # Check status
    if ready:
        return

    time.sleep(backoff_seconds)
    backoff_seconds = min(backoff_seconds * 1.5, max_backoff)
```

**Applied To**:
- Instance state transitions (pending → running)
- SSM agent registration
- Service startup verification
- Health endpoint checks

**Files**:
- `scripts/orchestrator.py:307-470`: Cluster readiness checks
- `scripts/build-ami.py:770-814`: Instance state waits

### 5. Experiment Lifecycle Pattern

**Implementation**: Isolated, reproducible experiment environments

**Structure**:
```
~/.pulsar-aws-lab/
├── latest -> exp-20241007-143022/    # Symlink to latest
├── exp-20241007-143022/              # Experiment directory
│   ├── orchestrator.log              # Execution log
│   ├── terraform.tfvars.json         # Generated Terraform vars
│   └── benchmark_results/            # Test results
│       ├── test1.json
│       └── test2.json
└── ami-cache/                        # AMI list cache
    └── ami-list.json
```

**Key Features**:
- Each experiment gets unique ID: `exp-YYYYMMDD-HHMMSS`
- "latest" symlink for easy access
- All experiment data self-contained
- Results persist after teardown

**Files**:
- `scripts/orchestrator.py:48-78`: Experiment initialization
- `scripts/orchestrator.py:1100-1105`: Resolve "latest" symlink

### 6. Modular Terraform Pattern

**Implementation**: Separate modules for independent scaling

**Structure**:
```
terraform/
├── main.tf                  # Module orchestration
├── modules/
│   ├── network/            # VPC, subnets, security groups
│   ├── compute/            # EC2 instances
│   └── storage/            # EBS volumes for BookKeeper
```

**Benefits**:
- Independent module testing
- Reusable across projects
- Clear dependency boundaries
- Easy to swap implementations

**Module Interface Pattern**:
```hcl
# Module outputs become inputs for dependent modules
module "network" {
  source = "./modules/network"
}

module "compute" {
  source    = "./modules/compute"
  vpc_id    = module.network.vpc_id        # Dependency
  subnet_id = module.network.public_subnet_id
}
```

**Files**:
- `terraform/main.tf:62-123`: Module declarations
- `terraform/modules/*/main.tf`: Module implementations

### 7. Health Check Cascade Pattern

**Implementation**: Multi-stage validation before declaring cluster ready

**Stages**:
1. **EC2 State**: Instances reach 'running' state
2. **SSM Registration**: Agents report online to Systems Manager
3. **Service Status**: Systemd services reach 'active' state
4. **Endpoint Health**: Service ports respond correctly

**Pattern**:
```python
# Stage 1: EC2 running
wait_for_instance_running(instance_ids)

# Stage 2: SSM online
wait_for_ssm_agent(instance_ids)

# Stage 3: Systemd active
for component, instances in component_instances.items():
    for service in component_services[component]:
        check_service_status(instance, service)

# Stage 4: Endpoint health
verify_health_endpoints(component_instances)
```

**Failure Handling**:
- Each stage has independent timeout
- Failures trigger automatic cleanup
- Detailed logging at each stage

**Files**:
- `scripts/orchestrator.py:307-470`: Multi-stage health checks
- `scripts/orchestrator.py:580-667`: Health endpoint verification

### 8. Configuration Override Pattern

**Implementation**: Layered configuration with runtime overrides

**Layers** (lowest to highest precedence):
1. Default values in Terraform variables
2. Configuration YAML files
3. Runtime CLI arguments (`--tag`, `--experiment-id`)

**Example**:
```python
# Base config from YAML
config = load_config('infrastructure.yaml')

# Merge runtime tags
if runtime_tags:
    config_tags = config.get('experiment', {}).get('tags', {})
    merged_tags = {**config_tags, **runtime_tags}
    config['experiment']['tags'] = merged_tags
```

**Benefits**:
- Reusable base configurations
- Environment-specific overrides
- Experiment metadata injection

**Files**:
- `scripts/orchestrator.py:688-694`: Tag merging
- `scripts/orchestrator.py:194-243`: Terraform vars generation

### 9. Dry-Run Pattern

**Implementation**: Simulate operations without side effects

**Pattern**:
```python
class Manager:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    def operation(self):
        if self.dry_run:
            console.print("DRY RUN: Would perform operation")
            return mock_result

        # Actual implementation
        return real_operation()
```

**Applied To**:
- AMI building (`build-ami.py --dry-run`)
- Resource cleanup (`cleanup_by_tag.py --dry-run`)

**Benefits**:
- Safe testing of destructive operations
- Documentation (shows what would happen)
- CI/CD integration

**Files**:
- `scripts/build-ami.py:88-113`: Dry-run initialization
- `scripts/cleanup_by_tag.py`: Dry-run mode for deletions

### 10. Progressive Enhancement Pattern

**Implementation**: Core functionality works, advanced features optional

**Examples**:

**SSM Connectivity**:
- Required: SSM agent for operations
- Optional: IAM role `SSMManagedInstanceCore`
- Fallback: Limited validation if role missing

**AMI Validation**:
- Core: Verify Pulsar directory exists
- Enhanced: Check systemd templates
- Advanced: Test service startup

**Cost Tracking**:
- Required: ExperimentID tag on all resources
- Optional: AWS Cost Explorer API access
- Fallback: Manual cost tracking via AWS Console

**Files**:
- `scripts/build-ami.py:677-768`: Validation with fallbacks
- `scripts/orchestrator.py:724-757`: Emergency cleanup fallback

## Anti-Patterns to Avoid

### ❌ Historical Comments

**Bad**:
```python
# REMOVED: run_ansible() method (lines 332-420)
# Ansible is no longer used - AMI contains pre-configured services
```

**Good**:
```python
# Just remove the code - git history preserves the past
```

### ❌ Conditional Legacy Code Paths

**Bad**:
```python
if use_ansible:  # Legacy path
    run_ansible_playbook()
else:  # New AMI-based path
    wait_for_cluster()
```

**Good**:
```python
# Remove the conditional entirely - commit to new approach
wait_for_cluster()
```

### ❌ Mixed Configuration Sources

**Bad**:
- Some config in YAML files
- Some config in environment variables
- Some config hardcoded
- Unclear precedence

**Good**:
- Clear layering: defaults → config files → CLI args
- Documented precedence order
- Consistent access patterns

### ❌ Silent Failures

**Bad**:
```python
try:
    cleanup_resources()
except Exception:
    pass  # Hope for the best
```

**Good**:
```python
try:
    cleanup_resources()
except Exception as e:
    logger.error(f"Cleanup failed: {e}")
    raise OrchestratorError("Manual cleanup required") from e
```

### ❌ State Coupling

**Bad**:
- Operations depend on Terraform state file existence
- No fallback if state lost/corrupted

**Good**:
- Tag-based resource discovery as fallback
- Emergency cleanup without state
- Self-contained experiment directories

## Pattern Evolution

### Migration Path: Ansible → AMI

**Phase 1**: Dual Mode (COMPLETED)
- Support both Ansible and AMI paths
- Toggle via configuration flag

**Phase 2**: AMI Default (COMPLETED)
- AMI path becomes default
- Ansible path deprecated

**Phase 3**: Cleanup (CURRENT)
- Remove Ansible code entirely
- Update all documentation
- Clean up dependencies

**Phase 4**: Optimization (FUTURE)
- Multi-region AMI support
- AMI lifecycle automation
- Enhanced validation
