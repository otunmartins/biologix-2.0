variable "region" {
  description = "AWS region. us-east-2 because the Neon database is there: the app opens a connection per request, so a cross-region hop is paid on every one."
  type        = string
  default     = "us-east-2"
}

variable "name" {
  description = "Name for everything this stack creates"
  type        = string
  default     = "excipient-screen"
}

variable "bundle_id" {
  description = "Lightsail plan. large_3_0 is 2 vCPU, 8 GB RAM, 160 GB SSD (about $44/month): the RDKit API and the Next.js build need the 8 GB. List others with: aws lightsail get-bundles --region us-east-2"
  type        = string
  default     = "large_3_0"
}

variable "blueprint_id" {
  description = "The OS image. Ubuntu 24.04 LTS, supported to 2029."
  type        = string
  default     = "ubuntu_24_04"
}

variable "ssh_public_key_path" {
  description = "Public half of the key you and the deploy workflow SSH in with. Make it once: ssh-keygen -t ed25519 -N \"\" -f ~/.ssh/excipient-screen"
  type        = string
  default     = "~/.ssh/excipient-screen.pub"
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH. Leave it open: the deploy workflow SSHes in from GitHub's runners, whose addresses change, so YOUR_IP/32 blocks every deploy. Key-only login keeps it safe; the deploy pins the host key via EC2_HOST_KEY."
  type        = string
  default     = "0.0.0.0/0"
}
