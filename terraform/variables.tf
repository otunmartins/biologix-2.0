variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name tag / prefix for everything this stack creates"
  type        = string
  default     = "excipient-screen"
}

variable "ami_id" {
  description = "Leave empty to use the current Ubuntu 22.04 LTS AMI for the region (looked up automatically). Set this to a Deep Learning AMI ID when you move to a GPU instance for Stage 3."
  type        = string
  default     = ""
}

variable "key_name" {
  description = "Existing EC2 key pair name to SSH in with"
  type        = string
}

variable "instance_type" {
  description = "Nothing in api/ touches the GPU yet, so this defaults to a cheap CPU box. Switch to g5.xlarge (plus a Deep Learning ami_id and a G/VT quota increase) when the OpenMM stage starts."
  type        = string
  default     = "t3.large"
}

variable "root_volume_gb" {
  description = "Root volume size. The RDKit and Node images need well over the 8 GB default."
  type        = number
  default     = 40
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH. Set this to YOUR_IP/32 — the default leaves port 22 open to the internet."
  type        = string
  default     = "0.0.0.0/0"
}
