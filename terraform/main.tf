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

# Lightsail, not EC2: this account's EC2 on-demand vCPU quota is 1 and AWS
# would not raise it, so even a t3.large cannot launch. Lightsail instances do
# not count against that quota. It is still a plain Ubuntu box reached over
# SSH, so everything after `terraform apply` (Docker Compose, Caddy, the deploy
# workflow) is unchanged.

# Only the public half is uploaded. Terraform never sees the private key, so it
# is not in the state file; it lives on your machine and in the EC2_SSH_KEY
# GitHub secret.
resource "aws_lightsail_key_pair" "screen" {
  name       = "${var.name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_lightsail_instance" "screen" {
  name              = var.name
  availability_zone = "${var.region}a"
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.screen.name

  # Runs once, as root, on first boot: installs Docker. Changing it replaces the
  # instance, so leave it alone once the box is up.
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

# Static IP, so the DNS A record and the EC2_HOST secret survive a stop/start
# or a rebuilt instance. Free while attached.
resource "aws_lightsail_static_ip" "screen" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "screen" {
  static_ip_name = aws_lightsail_static_ip.screen.name
  instance_name  = aws_lightsail_instance.screen.name
}

# The instance firewall. This resource owns the whole list: anything not here is
# closed, including the ports Lightsail opens by default.
resource "aws_lightsail_instance_public_ports" "screen" {
  instance_name = aws_lightsail_instance.screen.name

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = [var.ssh_cidr]
  }

  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
    cidrs     = ["0.0.0.0/0"]
  }

  # Caddy needs 443, and 80 above for the ACME challenge.
  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
    cidrs     = ["0.0.0.0/0"]
  }
}
