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

# User data debugging output (shows example of rendered template)
output "user_data_test" {
  description = "Example rendered user data for debugging (ZooKeeper instance 1)"
  value = length(module.compute.zookeeper_instance_ids) > 0 ? templatefile("${path.module}/user-data/zookeeper.sh.tpl", {
    cluster_name   = var.cluster_name
    pulsar_version = var.pulsar_version
    zk_heap_size   = var.zookeeper_heap_size
    zk_id          = 1
    zk_servers     = join(",", [for i in range(var.zookeeper_count) : "10.0.1.${10 + i}:2888:3888"])
  }) : "No ZooKeeper instances"
  sensitive = false
}

# Connection info
output "connection_info" {
  description = "Connection information"
  value = {
    ssh_key            = var.ssh_key_name
    zookeeper_connect  = join(",", [for ip in module.compute.zookeeper_private_ips : "${ip}:2181"])
    broker_service_url = length(module.compute.broker_private_ips) > 0 ? "pulsar://${module.compute.broker_private_ips[0]}:6650" : ""
    broker_http_url    = length(module.compute.broker_private_ips) > 0 ? "http://${module.compute.broker_private_ips[0]}:8080" : ""
  }
}
