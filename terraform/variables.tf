# Terraform Variables

variable "experiment_id" {
  description = "Unique experiment identifier"
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.experiment_id))
    error_message = "Experiment ID must contain only lowercase letters, numbers, and hyphens"
  }
}

variable "experiment_name" {
  description = "Human-readable experiment name"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

variable "availability_zone" {
  description = "AWS availability zone (optional)"
  type        = string
  default     = null
}

variable "use_spot_instances" {
  description = "Use spot instances for cost savings"
  type        = bool
  default     = false
}

variable "spot_max_price" {
  description = "Maximum spot instance price"
  type        = string
  default     = null
}

# Network Configuration
variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "Public subnet CIDR block"
  type        = string
  default     = "10.0.1.0/24"
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed for SSH access"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# Compute Configuration
variable "ssh_key_name" {
  description = "AWS SSH key pair name"
  type        = string
}

variable "ami_id" {
  description = "AMI ID (uses latest Amazon Linux 2 if not specified)"
  type        = string
  default     = null
}

# ZooKeeper Configuration
variable "zookeeper_count" {
  description = "Number of ZooKeeper instances"
  type        = number
  default     = 3
}

variable "zookeeper_instance_type" {
  description = "ZooKeeper instance type"
  type        = string
  default     = "t3.micro"
}

# BookKeeper Configuration
variable "bookkeeper_count" {
  description = "Number of BookKeeper instances"
  type        = number
  default     = 3
}

variable "bookkeeper_instance_type" {
  description = "BookKeeper instance type"
  type        = string
  default     = "t3.small"
}

variable "bookkeeper_volume_size" {
  description = "BookKeeper EBS volume size (GB)"
  type        = number
  default     = 20
}

variable "bookkeeper_volume_type" {
  description = "BookKeeper EBS volume type"
  type        = string
  default     = "gp3"
}

variable "bookkeeper_iops" {
  description = "BookKeeper EBS IOPS (for io1/io2)"
  type        = number
  default     = null
}

variable "bookkeeper_throughput" {
  description = "BookKeeper EBS throughput in MB/s (for gp3)"
  type        = number
  default     = 125
}

# Broker Configuration
variable "broker_count" {
  description = "Number of Broker instances"
  type        = number
  default     = 2
}

variable "broker_instance_type" {
  description = "Broker instance type"
  type        = string
  default     = "t3.small"
}

# Client Configuration
variable "client_count" {
  description = "Number of Client instances"
  type        = number
  default     = 1
}

variable "client_instance_type" {
  description = "Client instance type"
  type        = string
  default     = "t3.small"
}

# Tags
variable "additional_tags" {
  description = "Additional tags for resources"
  type        = map(string)
  default     = {}
}
