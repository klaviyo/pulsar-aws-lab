# Terraform Outputs

output "vpc_id" {
  description = "VPC ID"
  value       = module.network.vpc_id
}

output "subnet_id" {
  description = "Public subnet ID"
  value       = module.network.public_subnet_id
}

output "security_group_id" {
  description = "Security group ID"
  value       = module.network.security_group_id
}

# ZooKeeper outputs
output "zookeeper_instances" {
  description = "ZooKeeper instance details"
  value = {
    ids         = module.compute.zookeeper_instance_ids
    private_ips = module.compute.zookeeper_private_ips
    public_ips  = module.compute.zookeeper_public_ips
  }
}

# BookKeeper outputs
output "bookkeeper_instances" {
  description = "BookKeeper instance details"
  value = {
    ids         = module.compute.bookkeeper_instance_ids
    private_ips = module.compute.bookkeeper_private_ips
    public_ips  = module.compute.bookkeeper_public_ips
  }
}

# Broker outputs
output "broker_instances" {
  description = "Broker instance details"
  value = {
    ids         = module.compute.broker_instance_ids
    private_ips = module.compute.broker_private_ips
    public_ips  = module.compute.broker_public_ips
  }
}

# Client outputs
output "client_instances" {
  description = "Client instance details"
  value = {
    ids         = module.compute.client_instance_ids
    private_ips = module.compute.client_private_ips
    public_ips  = module.compute.client_public_ips
  }
}

# Ansible inventory format (SSM-based)
output "ansible_inventory" {
  description = "Ansible inventory in INI format with SSM connection"
  value       = <<-EOT
    [zookeeper]
    %{for idx, id in module.compute.zookeeper_instance_ids~}
    zk-${idx + 1} ansible_host=${id} ansible_user=ec2-user zk_id=${idx + 1}
    %{endfor~}

    [bookkeeper]
    %{for idx, id in module.compute.bookkeeper_instance_ids~}
    bk-${idx + 1} ansible_host=${id} ansible_user=ec2-user bk_id=${idx + 1}
    %{endfor~}

    [broker]
    %{for idx, id in module.compute.broker_instance_ids~}
    broker-${idx + 1} ansible_host=${id} ansible_user=ec2-user
    %{endfor~}

    [client]
    %{for idx, id in module.compute.client_instance_ids~}
    client-${idx + 1} ansible_host=${id} ansible_user=ec2-user
    %{endfor~}

    [pulsar:children]
    zookeeper
    bookkeeper
    broker

    [all:vars]
    ansible_connection=amazon.aws.aws_ssm
    ansible_aws_ssm_bucket_name=${module.s3.bucket_name}
    ansible_aws_ssm_region=${var.aws_region}
    ansible_aws_ssm_s3_addressing_style=path
  EOT
}

# SSM Configuration
output "ssm_bucket_name" {
  description = "S3 bucket for Ansible SSM file transfers"
  value       = module.s3.bucket_name
}

output "ssm_bucket_region" {
  description = "S3 bucket region"
  value       = module.s3.bucket_region
}

# Connection info
output "connection_info" {
  description = "Connection information"
  value = {
    ssh_key            = var.ssh_key_name
    ssm_bucket         = module.s3.bucket_name
    zookeeper_connect  = join(",", [for ip in module.compute.zookeeper_private_ips : "${ip}:2181"])
    broker_service_url = length(module.compute.broker_private_ips) > 0 ? "pulsar://${module.compute.broker_private_ips[0]}:6650" : ""
    broker_http_url    = length(module.compute.broker_private_ips) > 0 ? "http://${module.compute.broker_private_ips[0]}:8080" : ""
  }
}
