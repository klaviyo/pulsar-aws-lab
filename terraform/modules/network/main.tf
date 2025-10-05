# Network Module - VPC, Subnets, Security Groups

variable "experiment_id" {
  description = "Experiment ID"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
}

variable "public_subnet_cidr" {
  description = "Public subnet CIDR"
  type        = string
}

variable "availability_zone" {
  description = "Availability zone"
  type        = string
}

variable "allowed_ssh_cidrs" {
  description = "Allowed SSH CIDR blocks"
  type        = list(string)
}

# VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "pulsar-lab-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "pulsar-lab-igw-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Public Subnet
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true

  tags = {
    Name = "pulsar-lab-public-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Route Table
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "pulsar-lab-rt-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Route Table Association
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Security Group
resource "aws_security_group" "pulsar" {
  name        = "pulsar-lab-sg-${var.experiment_id}"
  description = "Security group for Pulsar cluster"
  vpc_id      = aws_vpc.main.id

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
    description = "SSH access"
  }

  # ZooKeeper client port
  ingress {
    from_port   = 2181
    to_port     = 2181
    protocol    = "tcp"
    self        = true
    description = "ZooKeeper client"
  }

  # ZooKeeper peer ports
  ingress {
    from_port   = 2888
    to_port     = 2888
    protocol    = "tcp"
    self        = true
    description = "ZooKeeper peer"
  }

  ingress {
    from_port   = 3888
    to_port     = 3888
    protocol    = "tcp"
    self        = true
    description = "ZooKeeper leader election"
  }

  # BookKeeper port
  ingress {
    from_port   = 3181
    to_port     = 3181
    protocol    = "tcp"
    self        = true
    description = "BookKeeper"
  }

  # Pulsar broker binary protocol
  ingress {
    from_port   = 6650
    to_port     = 6650
    protocol    = "tcp"
    self        = true
    description = "Pulsar broker binary"
  }

  # Allow external access to broker for testing
  ingress {
    from_port   = 6650
    to_port     = 6650
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Pulsar broker binary (external)"
  }

  # Pulsar broker HTTP
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    self        = true
    description = "Pulsar broker HTTP"
  }

  # Allow external access to broker HTTP for testing
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Pulsar broker HTTP (external)"
  }

  # Pulsar broker TLS (optional)
  ingress {
    from_port   = 6651
    to_port     = 6651
    protocol    = "tcp"
    self        = true
    description = "Pulsar broker TLS"
  }

  ingress {
    from_port   = 8443
    to_port     = 8443
    protocol    = "tcp"
    self        = true
    description = "Pulsar broker HTTPS"
  }

  # Allow all outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name = "pulsar-lab-sg-${var.experiment_id}"
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_id" {
  description = "Public subnet ID"
  value       = aws_subnet.public.id
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.pulsar.id
}
