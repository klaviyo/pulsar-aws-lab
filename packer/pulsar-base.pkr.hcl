packer {
  required_plugins {
    amazon = {
      version = ">= 1.2.8"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
  description = "AWS region to build AMI in"
}

variable "pulsar_version" {
  type    = string
  default = "3.0.0"
  description = "Apache Pulsar version to install"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
  description = "Instance type for building AMI"
}

variable "ami_prefix" {
  type    = string
  default = "pulsar-base"
  description = "Prefix for AMI name"
}

# Data source to get latest Amazon Linux 2023 AMI
data "amazon-ami" "amazon_linux_2023" {
  filters = {
    name                = "al2023-ami-2023.*-kernel-6.1-x86_64"
    virtualization-type = "hvm"
    root-device-type    = "ebs"
  }
  owners      = ["amazon"]
  most_recent = true
  region      = var.region
}

locals {
  timestamp = regex_replace(timestamp(), "[- TZ:]", "")
  ami_name  = "${var.ami_prefix}-${var.pulsar_version}-${local.timestamp}"
}

source "amazon-ebs" "pulsar_base" {
  region        = var.region
  source_ami    = data.amazon-ami.amazon_linux_2023.id
  instance_type = var.instance_type
  ssh_username  = "ec2-user"
  ami_name      = local.ami_name

  # Tags for the final AMI
  tags = {
    Name          = local.ami_name
    PulsarVersion = var.pulsar_version
    Environment   = "lab"
    ManagedBy     = "packer"
    BuildDate     = local.timestamp
    Project       = "pulsar-aws-lab"
  }

  # Tags for temporary build instance (for easier cleanup if build fails)
  run_tags = {
    Name          = "packer-build-${local.ami_name}"
    PulsarVersion = var.pulsar_version
    Environment   = "lab"
    ManagedBy     = "packer"
    Project       = "pulsar-aws-lab"
    Purpose       = "ami-build"
    Temporary     = "true"
  }

  # Enable EBS optimization
  ebs_optimized = true

  # Root volume configuration
  launch_block_device_mappings {
    device_name           = "/dev/xvda"
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }
}

build {
  name = "pulsar-base-ami"
  sources = [
    "source.amazon-ebs.pulsar_base"
  ]

  # Wait for cloud-init to complete
  provisioner "shell" {
    inline = [
      "echo 'Waiting for cloud-init to complete...'",
      "cloud-init status --wait"
    ]
  }

  # Update system packages
  provisioner "shell" {
    inline = [
      "echo 'Updating system packages...'",
      "sudo dnf update -y"
    ]
  }

  # Configure system settings
  provisioner "shell" {
    script = "${path.root}/scripts/configure-system.sh"
    environment_vars = [
      "PULSAR_VERSION=${var.pulsar_version}"
    ]
  }

  # Install Pulsar
  provisioner "shell" {
    script = "${path.root}/scripts/install-pulsar.sh"
    environment_vars = [
      "PULSAR_VERSION=${var.pulsar_version}"
    ]
  }

  # Install OpenMessaging Benchmark
  provisioner "shell" {
    script = "${path.root}/scripts/install-benchmark.sh"
    environment_vars = [
      "PULSAR_VERSION=${var.pulsar_version}"
    ]
  }

  # Copy systemd service templates
  provisioner "file" {
    source      = "${path.root}/files/systemd/"
    destination = "/tmp/systemd-templates"
  }

  # Install systemd templates
  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/pulsar-templates/systemd",
      "sudo mv /tmp/systemd-templates/* /opt/pulsar-templates/systemd/",
      "sudo chmod 644 /opt/pulsar-templates/systemd/*.service.tpl"
    ]
  }

  # Clean up
  provisioner "shell" {
    inline = [
      "echo 'Cleaning up...'",
      "sudo dnf clean all",
      "sudo rm -rf /tmp/*",
      "sudo rm -rf /var/tmp/*",
      "sudo rm -f /root/.bash_history",
      "sudo rm -f /home/ec2-user/.bash_history",
      "history -c"
    ]
  }

  # Verify installation
  provisioner "shell" {
    inline = [
      "echo 'Verifying installation...'",
      "java -version",
      "test -f /opt/pulsar/bin/pulsar || exit 1",
      "test -f /opt/pulsar/bin/bookkeeper || exit 1",
      "test -d /opt/openmessaging-benchmark || exit 1",
      "echo 'Installation verified successfully!'"
    ]
  }
}
