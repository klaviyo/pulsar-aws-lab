# Compute Module - EC2 Instances

variable "experiment_id" {
  description = "Experiment ID"
  type        = string
}

variable "ami_id" {
  description = "AMI ID"
  type        = string
}

variable "ssh_key_name" {
  description = "SSH key pair name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID"
  type        = string
}

variable "use_spot_instances" {
  description = "Use spot instances"
  type        = bool
}

variable "spot_max_price" {
  description = "Max spot price"
  type        = string
  default     = null
}

variable "pulsar_version" {
  description = "Apache Pulsar version"
  type        = string
}

variable "cluster_name" {
  description = "Pulsar cluster name"
  type        = string
}

variable "zookeeper_count" {
  description = "Number of ZooKeeper instances"
  type        = number
}

variable "zookeeper_instance_type" {
  description = "ZooKeeper instance type"
  type        = string
}

variable "zookeeper_heap_size" {
  description = "ZooKeeper JVM heap size"
  type        = string
}

variable "bookkeeper_count" {
  description = "Number of BookKeeper instances"
  type        = number
}

variable "bookkeeper_instance_type" {
  description = "BookKeeper instance type"
  type        = string
}

variable "bookkeeper_volume_ids" {
  description = "BookKeeper EBS volume IDs"
  type        = list(string)
}

variable "bookkeeper_heap_size" {
  description = "BookKeeper JVM heap size"
  type        = string
}

variable "bookkeeper_direct_memory_size" {
  description = "BookKeeper JVM direct memory size"
  type        = string
}

variable "broker_count" {
  description = "Number of Broker instances"
  type        = number
}

variable "broker_instance_type" {
  description = "Broker instance type"
  type        = string
}

variable "broker_heap_size" {
  description = "Broker JVM heap size"
  type        = string
}

variable "broker_direct_memory_size" {
  description = "Broker JVM direct memory size"
  type        = string
}

variable "client_count" {
  description = "Number of Client instances"
  type        = number
}

variable "client_instance_type" {
  description = "Client instance type"
  type        = string
}

# ZooKeeper Instances
resource "aws_instance" "zookeeper" {
  count = var.zookeeper_count

  ami                         = var.ami_id
  instance_type               = var.zookeeper_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true

  # User data for ZooKeeper configuration
  user_data = base64encode(templatefile("${path.module}/../../user-data/zookeeper.sh.tpl", {
    cluster_name   = var.cluster_name
    pulsar_version = var.pulsar_version
    zk_heap_size   = var.zookeeper_heap_size
    zk_id          = count.index + 1
    zk_servers     = join(",", [for i in range(var.zookeeper_count) : "${cidrhost(data.aws_subnet.selected.cidr_block, 10 + i)}:2888:3888"])
  }))

  root_block_device {
    volume_size = 20  # GB - Pulsar binary + OS + working space
    volume_type = "gp3"
  }

  # Spot instance configuration
  instance_market_options {
    market_type = var.use_spot_instances ? "spot" : null

    dynamic "spot_options" {
      for_each = var.use_spot_instances ? [1] : []
      content {
        max_price          = var.spot_max_price
        spot_instance_type = "one-time"
      }
    }
  }

  tags = {
    Name      = "pulsar-lab-zk-${count.index + 1}-${var.experiment_id}"
    Component = "zookeeper"
    Role      = "zookeeper"
    ZkID      = count.index + 1
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Data source to get subnet info for IP calculations
data "aws_subnet" "selected" {
  id = var.subnet_id
}

# BookKeeper Instances
resource "aws_instance" "bookkeeper" {
  count = var.bookkeeper_count

  ami                         = var.ami_id
  instance_type               = var.bookkeeper_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true

  # User data for BookKeeper configuration
  user_data = base64encode(templatefile("${path.module}/../../user-data/bookkeeper.sh.tpl", {
    cluster_name           = var.cluster_name
    pulsar_version         = var.pulsar_version
    bk_heap_size           = var.bookkeeper_heap_size
    bk_direct_memory_size  = var.bookkeeper_direct_memory_size
    zk_servers             = join(",", [for i in range(var.zookeeper_count) : "${aws_instance.zookeeper[i].private_ip}:2181"])
  }))

  root_block_device {
    volume_size = 20  # GB - Pulsar binary + OS + working space
    volume_type = "gp3"
  }

  # Spot instance configuration
  instance_market_options {
    market_type = var.use_spot_instances ? "spot" : null

    dynamic "spot_options" {
      for_each = var.use_spot_instances ? [1] : []
      content {
        max_price          = var.spot_max_price
        spot_instance_type = "one-time"
      }
    }
  }

  tags = {
    Name      = "pulsar-lab-bk-${count.index + 1}-${var.experiment_id}"
    Component = "bookkeeper"
    Role      = "bookkeeper"
    BkID      = count.index + 1
  }

  lifecycle {
    ignore_changes = [tags_all]
  }

  depends_on = [aws_instance.zookeeper]
}

# Attach EBS volumes to BookKeeper instances
resource "aws_volume_attachment" "bookkeeper" {
  count = var.bookkeeper_count

  device_name = "/dev/sdf"
  volume_id   = var.bookkeeper_volume_ids[count.index]
  instance_id = aws_instance.bookkeeper[count.index].id
}

# Broker Instances
resource "aws_instance" "broker" {
  count = var.broker_count

  ami                         = var.ami_id
  instance_type               = var.broker_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true

  # User data for Broker configuration
  user_data = base64encode(templatefile("${path.module}/../../user-data/broker.sh.tpl", {
    cluster_name             = var.cluster_name
    pulsar_version           = var.pulsar_version
    broker_heap_size         = var.broker_heap_size
    broker_direct_memory_size = var.broker_direct_memory_size
    zk_servers               = join(",", [for i in range(var.zookeeper_count) : "${aws_instance.zookeeper[i].private_ip}:2181"])
  }))

  root_block_device {
    volume_size = 20  # GB - Pulsar binary + OS + working space
    volume_type = "gp3"
  }

  # Spot instance configuration
  instance_market_options {
    market_type = var.use_spot_instances ? "spot" : null

    dynamic "spot_options" {
      for_each = var.use_spot_instances ? [1] : []
      content {
        max_price          = var.spot_max_price
        spot_instance_type = "one-time"
      }
    }
  }

  tags = {
    Name      = "pulsar-lab-broker-${count.index + 1}-${var.experiment_id}"
    Component = "broker"
    Role      = "broker"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }

  depends_on = [aws_instance.zookeeper, aws_instance.bookkeeper]
}

# Client Instances
resource "aws_instance" "client" {
  count = var.client_count

  ami                         = var.ami_id
  instance_type               = var.client_instance_type
  key_name                    = var.ssh_key_name
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  associate_public_ip_address = true

  # User data for Client configuration
  user_data = base64encode(templatefile("${path.module}/../../user-data/client.sh.tpl", {
    cluster_name   = var.cluster_name
    pulsar_version = var.pulsar_version
    broker_urls    = join(",", [for i in range(var.broker_count) : "pulsar://${aws_instance.broker[i].private_ip}:6650"])
    http_urls      = join(",", [for i in range(var.broker_count) : "http://${aws_instance.broker[i].private_ip}:8080"])
  }))

  root_block_device {
    volume_size = 20  # GB - Pulsar binary + OS + working space
    volume_type = "gp3"
  }

  # Spot instance configuration
  instance_market_options {
    market_type = var.use_spot_instances ? "spot" : null

    dynamic "spot_options" {
      for_each = var.use_spot_instances ? [1] : []
      content {
        max_price          = var.spot_max_price
        spot_instance_type = "one-time"
      }
    }
  }

  tags = {
    Name      = "pulsar-lab-client-${count.index + 1}-${var.experiment_id}"
    Component = "client"
    Role      = "benchmark"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }

  depends_on = [aws_instance.broker]
}

# Outputs
output "zookeeper_instance_ids" {
  description = "ZooKeeper instance IDs"
  value       = aws_instance.zookeeper[*].id
}

output "zookeeper_private_ips" {
  description = "ZooKeeper private IPs"
  value       = aws_instance.zookeeper[*].private_ip
}

output "zookeeper_public_ips" {
  description = "ZooKeeper public IPs"
  value       = aws_instance.zookeeper[*].public_ip
}

output "bookkeeper_instance_ids" {
  description = "BookKeeper instance IDs"
  value       = aws_instance.bookkeeper[*].id
}

output "bookkeeper_private_ips" {
  description = "BookKeeper private IPs"
  value       = aws_instance.bookkeeper[*].private_ip
}

output "bookkeeper_public_ips" {
  description = "BookKeeper public IPs"
  value       = aws_instance.bookkeeper[*].public_ip
}

output "broker_instance_ids" {
  description = "Broker instance IDs"
  value       = aws_instance.broker[*].id
}

output "broker_private_ips" {
  description = "Broker private IPs"
  value       = aws_instance.broker[*].private_ip
}

output "broker_public_ips" {
  description = "Broker public IPs"
  value       = aws_instance.broker[*].public_ip
}

output "client_instance_ids" {
  description = "Client instance IDs"
  value       = aws_instance.client[*].id
}

output "client_private_ips" {
  description = "Client private IPs"
  value       = aws_instance.client[*].private_ip
}

output "client_public_ips" {
  description = "Client public IPs"
  value       = aws_instance.client[*].public_ip
}
