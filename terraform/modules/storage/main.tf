# Storage Module - EBS Volumes for BookKeeper

variable "experiment_id" {
  description = "Experiment ID"
  type        = string
}

variable "count_nodes" {
  description = "Number of volumes to create"
  type        = number
}

variable "volume_size" {
  description = "Volume size in GB"
  type        = number
}

variable "volume_type" {
  description = "EBS volume type"
  type        = string
}

variable "iops" {
  description = "Provisioned IOPS (for io1/io2)"
  type        = number
  default     = null
}

variable "throughput" {
  description = "Throughput in MB/s (for gp3)"
  type        = number
  default     = null
}

variable "availability_zone" {
  description = "Availability zone"
  type        = string
}

# EBS Volumes for BookKeeper
resource "aws_ebs_volume" "bookkeeper" {
  count = var.count_nodes

  availability_zone = var.availability_zone
  size              = var.volume_size
  type              = var.volume_type

  # Conditionally set IOPS for io1/io2 volumes
  iops = contains(["io1", "io2"], var.volume_type) ? var.iops : null

  # Conditionally set throughput for gp3 volumes
  throughput = var.volume_type == "gp3" ? var.throughput : null

  tags = {
    Name      = "pulsar-lab-bk-volume-${count.index + 1}-${var.experiment_id}"
    Component = "bookkeeper"
    VolumeID  = count.index + 1
  }

  lifecycle {
    ignore_changes = [tags_all]
  }
}

# Outputs
output "volume_ids" {
  description = "List of EBS volume IDs"
  value       = aws_ebs_volume.bookkeeper[*].id
}

output "volume_arns" {
  description = "List of EBS volume ARNs"
  value       = aws_ebs_volume.bookkeeper[*].arn
}
