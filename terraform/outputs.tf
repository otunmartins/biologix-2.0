output "public_ip" {
  description = "Point your DNS A record here"
  value       = aws_lightsail_static_ip.screen.ip_address
}

output "ssh" {
  description = "Copy-paste to get on the box"
  value       = "ssh -i ${trimsuffix(var.ssh_public_key_path, ".pub")} ubuntu@${aws_lightsail_static_ip.screen.ip_address}"
}
