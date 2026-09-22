terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# Default VPC — fine for one box. Nothing here needs private subnets yet.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "screen" {
  name        = "${var.name}-sg"
  description = "Excipient Screen — web + ssh"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (Caddy needs this open for the ACME challenge too)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-sg" }
}

# Canonical publishes the current Ubuntu AMI id per region as a public SSM
# parameter, so there's nothing to look up by hand and nothing to go stale.
# When you move to a GPU box, pass a Deep Learning AMI via -var="ami_id=ami-xxxx":
#   aws ec2 describe-images --owners amazon --region us-east-1 \
#     --filters "Name=name,Values=Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*" \
#     --query 'reverse(sort_by(Images,&CreationDate))[:5].[ImageId,Name]' --output table
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
}

resource "aws_instance" "screen" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ssm_parameter.ubuntu.value
  instance_type          = var.instance_type
  key_name               = var.key_name
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.screen.id]

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  # Installs Docker on a plain Ubuntu AMI. It's a no-op on a Deep Learning AMI,
  # which ships Docker and the NVIDIA container toolkit already.
  user_data = <<-EOT
    #!/bin/bash
    set -eux
    if ! command -v docker >/dev/null 2>&1; then
      apt-get update
      apt-get install -y ca-certificates curl git
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    usermod -aG docker ubuntu || true
    systemctl enable --now docker
  EOT

  tags = { Name = var.name }
}

# Static IP, so a DNS A record survives a stop/start of the instance.
resource "aws_eip" "screen" {
  instance = aws_instance.screen.id
  domain   = "vpc"
  tags     = { Name = "${var.name}-eip" }
}
