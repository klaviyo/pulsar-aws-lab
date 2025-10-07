# Troubleshooting Guide

## Common Issues and Solutions

### AMI-Related Issues

#### Issue: "No AMI found matching pattern 'pulsar-base-*'"

**Cause**: AMI has not been built yet in the target region

**Solution**:
```bash
# Build the AMI first
python scripts/build-ami.py build --version 3.0.0 --region us-west-2

# Verify it exists
python scripts/build-ami.py list --region us-west-2
```

**Prevention**: Build AMI as part of initial setup documentation

**Related Code**: `scripts/orchestrator.py:94-157`

#### Issue: AMI validation fails with "Pulsar directory not found"

**Cause**: Packer build failed partially or AMI corrupted

**Solution**:
```bash
# Delete the bad AMI
python scripts/build-ami.py delete --ami-id ami-xxxxx

# Rebuild
python scripts/build-ami.py build --version 3.0.0 --force
```

**Debug**:
- Check Packer build logs for provisioner failures
- Verify all provisioner scripts completed successfully
- Check AMI tags for build metadata

**Related Code**: `scripts/build-ami.py:456-553`

### Infrastructure Provisioning Issues

#### Issue: Terraform apply fails with "InvalidParameterValue"

**Common Causes**:
1. **SSH Key not found**: `ssh_key_name` doesn't exist in AWS region
2. **IAM role missing**: `SSMManagedInstanceCore` doesn't exist
3. **AMI not available**: AMI ID not found in region

**Solution**:
```bash
# Check SSH key exists
aws ec2 describe-key-pairs --key-names pulsar-lab-key --region us-west-2

# Create if missing
aws ec2 create-key-pair --key-name pulsar-lab-key --region us-west-2

# Check IAM role
aws iam get-role --role-name SSMManagedInstanceCore

# Create if missing (AWS managed policy)
aws iam create-role --role-name SSMManagedInstanceCore \
  --assume-role-policy-document file://ssm-role-trust.json
aws iam attach-role-policy --role-name SSMManagedInstanceCore \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
```

**Related Code**: `terraform/modules/compute/main.tf`

#### Issue: "Timeout waiting for instances to reach 'running' state"

**Cause**: Instance launch issues (capacity, limits, configuration)

**Debug**:
```bash
# Check instance state in AWS console
aws ec2 describe-instances --filters "Name=tag:ExperimentID,Values=exp-xxxxx"

# Check for status check failures
aws ec2 describe-instance-status --instance-ids i-xxxxx
```

**Common Reasons**:
- AWS capacity constraints (try different AZ or instance type)
- Service quota limits reached
- Invalid AMI or instance configuration

**Related Code**: `scripts/orchestrator.py:332-373`

### Cluster Health Check Issues

#### Issue: "Timeout waiting for SSM agent registration"

**Cause**: SSM agent not running or IAM role missing

**Debug**:
```bash
# Check SSM agent status
aws ssm describe-instance-information \
  --filters "Key=tag:ExperimentID,Values=exp-xxxxx"

# Check instance has IAM role
aws ec2 describe-instances --instance-ids i-xxxxx \
  --query 'Reservations[0].Instances[0].IamInstanceProfile'
```

**Solutions**:
1. Verify IAM instance profile attached to instances
2. Check SSM agent is enabled in AMI
3. Verify VPC endpoints for SSM (if using private subnets)
4. Check security groups allow outbound HTTPS

**Related Code**: `scripts/orchestrator.py:375-405`

#### Issue: "Service not active" errors during health checks

**Cause**: Systemd service failed to start

**Debug via SSM**:
```bash
# Get instance ID from experiment
aws ec2 describe-instances \
  --filters "Name=tag:ExperimentID,Values=exp-xxxxx" \
  --query 'Reservations[*].Instances[*].[InstanceId,Tags[?Key==`Component`].Value|[0]]'

# Check service status
aws ssm send-command \
  --instance-ids i-xxxxx \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl status zookeeper.service"]'

# Get command output
aws ssm get-command-invocation \
  --command-id cmd-xxxxx \
  --instance-id i-xxxxx
```

**Common Causes**:
- Configuration errors in systemd service files
- Missing dependencies (Java, Pulsar binaries)
- Port conflicts
- Insufficient memory/resources

**Related Code**: `scripts/orchestrator.py:407-470`

### Testing and Benchmark Issues

#### Issue: Benchmark fails with "Connection refused"

**Cause**: Broker endpoints not accessible from client instance

**Debug**:
```bash
# Check broker connectivity from client
aws ssm send-command \
  --instance-ids <client-instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["nc -zv <broker-ip> 6650"]'
```

**Solutions**:
1. Verify security group allows traffic between client and broker
2. Check broker service is running
3. Verify broker is listening on correct interface (not just localhost)

**Related Code**: `scripts/orchestrator.py:819-913`

#### Issue: "Workload file not found" during benchmark

**Cause**: File upload via SSM failed

**Debug**:
```bash
# Verify workload file uploaded
aws ssm send-command \
  --instance-ids <client-instance-id> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["ls -la /tmp/workload_*.yaml"]'
```

**Solution**:
- Check SSM command succeeded (look for StandardOutputContent)
- Verify file permissions
- Check disk space

**Related Code**: `scripts/orchestrator.py:860-874`

### Resource Cleanup Issues

#### Issue: "Some instances are still running" after teardown

**Cause**: Terraform destroy failed partially

**Solution**:
```bash
# Emergency cleanup by experiment ID
python scripts/cleanup_by_tag.py \
  --experiment-id exp-xxxxx \
  --execute

# Or via orchestrator
python scripts/orchestrator.py teardown --experiment-id exp-xxxxx
```

**Prevention**:
- Always use `emergency_cleanup()` in exception handlers
- Tag all resources consistently
- Enable termination protection only when needed

**Related Code**:
- `scripts/orchestrator.py:724-757`: Emergency cleanup
- `scripts/cleanup_by_tag.py`: Standalone cleanup tool

#### Issue: "No Terraform state found" during teardown

**Cause**: State file lost or corrupted

**Solution**:
```bash
# Use tag-based cleanup (doesn't need Terraform state)
python scripts/cleanup_by_tag.py \
  --experiment-id exp-xxxxx \
  --region us-west-2 \
  --execute
```

**Prevention**:
- Consider S3 backend for Terraform state
- Backup state files after successful applies
- Use consistent ExperimentID tagging

**Related Code**: `scripts/orchestrator.py:758-817`

## Debugging Techniques

### Log Analysis

**Orchestrator Logs**:
```bash
# Latest experiment logs
tail -f ~/.pulsar-aws-lab/latest/orchestrator.log

# Search for errors
grep -i error ~/.pulsar-aws-lab/latest/orchestrator.log

# Search for specific experiment
grep -i "experiment.*exp-xxxxx" ~/.pulsar-aws-lab/*/orchestrator.log
```

**CloudWatch Logs** (if configured):
- SSM command output: `/aws/ssm/`
- Instance system logs: `/var/log/messages`

### AWS Resource Inspection

**Find all resources for experiment**:
```bash
# EC2 instances
aws ec2 describe-instances \
  --filters "Name=tag:ExperimentID,Values=exp-xxxxx"

# EBS volumes
aws ec2 describe-volumes \
  --filters "Name=tag:ExperimentID,Values=exp-xxxxx"

# Security groups
aws ec2 describe-security-groups \
  --filters "Name=tag:ExperimentID,Values=exp-xxxxx"

# VPCs
aws ec2 describe-vpcs \
  --filters "Name=tag:ExperimentID,Values=exp-xxxxx"
```

### Performance Debugging

**Instance Performance**:
```bash
# Connect via SSM session (if configured)
aws ssm start-session --target i-xxxxx

# Inside instance:
top                    # CPU/memory usage
iostat -x 1 10        # Disk I/O
netstat -an           # Network connections
journalctl -u zookeeper.service  # Service logs
```

**Cluster Health**:
```bash
# ZooKeeper status
echo ruok | nc <zk-ip> 2181

# BookKeeper list bookies
/opt/pulsar/bin/bookkeeper shell listbookies -rw

# Broker health
curl http://<broker-ip>:8080/admin/v2/brokers/health

# Pulsar topics
/opt/pulsar/bin/pulsar-admin topics list public/default
```

## Error Messages and Solutions

### "cloud-init status --wait timed out"

**Cause**: Instance initialization taking too long

**Solution**: Increase timeout or check instance type has sufficient resources

### "InvalidParameterException: IAM Instance Profile 'SSMManagedInstanceCore' does not exist"

**Cause**: Required IAM role not created

**Solution**: Create IAM role with SSM managed policy (see Infrastructure Issues above)

### "Error: Terraform state lock acquisition failed"

**Cause**: Another Terraform operation in progress or crashed

**Solution**:
```bash
# Force unlock (use with caution)
cd terraform
terraform force-unlock <lock-id>
```

### "ValidationException: Provided role does not have a trust relationship"

**Cause**: IAM role trust policy incorrect

**Solution**: Ensure role trusts EC2 service:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
```

## Prevention Best Practices

1. **Always validate AMI** before production experiments
   ```bash
   python scripts/build-ami.py validate --ami-id ami-xxxxx
   ```

2. **Use dry-run mode** for destructive operations
   ```bash
   python scripts/cleanup_by_tag.py --experiment-id exp-xxxxx --dry-run
   ```

3. **Check AWS service quotas** before large deployments
   ```bash
   aws service-quotas list-service-quotas --service-code ec2
   ```

4. **Monitor experiment costs** during execution
   ```bash
   aws ce get-cost-and-usage \
     --time-period Start=2024-10-01,End=2024-10-07 \
     --granularity DAILY \
     --metrics UnblendedCost \
     --filter file://cost-filter.json
   ```

5. **Test configurations** with minimal resources first
   - Use t3.micro for initial validation
   - Single node of each component
   - Short test duration

6. **Enable termination protection** for critical experiments
   ```bash
   aws ec2 modify-instance-attribute \
     --instance-id i-xxxxx \
     --disable-api-termination
   ```

## Getting Help

1. **Check orchestrator logs**: `~/.pulsar-aws-lab/latest/orchestrator.log`
2. **Check AWS CloudWatch** for SSM command outputs
3. **Review Terraform state**: `terraform/terraform.tfstate`
4. **List AWS resources by tag**: `aws resourcegroupstaggingapi get-resources`
5. **Check GitHub issues**: Look for similar problems and solutions

## Known Limitations

1. **Single Region**: AMIs must be built per region
2. **No Concurrent Experiments**: One experiment per AWS account
3. **SSM Agent Required**: All operations depend on SSM connectivity
4. **Eventual Consistency**: AWS tag propagation may be delayed
5. **Manual AMI Cleanup**: Old AMIs must be deleted manually
