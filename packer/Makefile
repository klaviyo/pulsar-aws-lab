.PHONY: help init validate build clean fmt check

# Default variables
REGION ?= us-west-2
PULSAR_VERSION ?= 3.0.0
INSTANCE_TYPE ?= t3.small

help:
	@echo "Pulsar Base AMI - Packer Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  make init           - Initialize Packer plugins"
	@echo "  make validate       - Validate Packer template"
	@echo "  make build          - Build AMI with default settings"
	@echo "  make build-custom   - Build AMI with custom variables (see variables)"
	@echo "  make fmt            - Format Packer template"
	@echo "  make check          - Run all checks (fmt, validate)"
	@echo "  make clean          - Clean Packer cache"
	@echo "  make list-amis      - List all Packer-built AMIs"
	@echo "  make help           - Show this help message"
	@echo ""
	@echo "Variables:"
	@echo "  REGION          AWS region (default: $(REGION))"
	@echo "  PULSAR_VERSION  Pulsar version (default: $(PULSAR_VERSION))"
	@echo "  INSTANCE_TYPE   Instance type (default: $(INSTANCE_TYPE))"
	@echo ""
	@echo "Examples:"
	@echo "  make build"
	@echo "  make build REGION=us-east-1 PULSAR_VERSION=3.1.0"
	@echo "  make validate"

init:
	@echo "Initializing Packer..."
	packer init pulsar-base.pkr.hcl

validate: init
	@echo "Validating Packer template..."
	packer validate \
		-var "region=$(REGION)" \
		-var "pulsar_version=$(PULSAR_VERSION)" \
		-var "instance_type=$(INSTANCE_TYPE)" \
		pulsar-base.pkr.hcl

build: validate
	@echo "Building Pulsar base AMI..."
	@echo "Region: $(REGION)"
	@echo "Pulsar Version: $(PULSAR_VERSION)"
	@echo "Instance Type: $(INSTANCE_TYPE)"
	@echo ""
	packer build \
		-var "region=$(REGION)" \
		-var "pulsar_version=$(PULSAR_VERSION)" \
		-var "instance_type=$(INSTANCE_TYPE)" \
		pulsar-base.pkr.hcl

build-custom: validate
	@echo "Building with custom variables file..."
	packer build \
		-var-file=variables.pkrvars.hcl \
		pulsar-base.pkr.hcl

fmt:
	@echo "Formatting Packer template..."
	packer fmt pulsar-base.pkr.hcl

check: fmt validate
	@echo "All checks passed!"

clean:
	@echo "Cleaning Packer cache..."
	rm -rf packer_cache/
	rm -f crash.log
	@echo "Clean complete!"

list-amis:
	@echo "Listing all Packer-built Pulsar AMIs..."
	@aws ec2 describe-images \
		--owners self \
		--filters "Name=tag:ManagedBy,Values=packer" "Name=name,Values=pulsar-base-*" \
		--query 'Images | sort_by(@, &CreationDate) | [].[ImageId,Name,CreationDate,Tags[?Key==`PulsarVersion`].Value|[0]]' \
		--output table || echo "Error: AWS CLI not configured or no AMIs found"

delete-ami:
	@if [ -z "$(AMI_ID)" ]; then \
		echo "Error: AMI_ID not specified"; \
		echo "Usage: make delete-ami AMI_ID=ami-xxxxxxxxx"; \
		exit 1; \
	fi
	@echo "Deregistering AMI: $(AMI_ID)"
	@aws ec2 deregister-image --image-id $(AMI_ID)
	@echo "Deleting associated snapshots..."
	@aws ec2 describe-snapshots \
		--owner-ids self \
		--filters "Name=description,Values=*$(AMI_ID)*" \
		--query 'Snapshots[*].SnapshotId' \
		--output text | \
		xargs -r -I {} aws ec2 delete-snapshot --snapshot-id {}
	@echo "AMI $(AMI_ID) and associated snapshots deleted"
