output "public_ip" {
  description = "Point your DNS A record here"
  value       = aws_eip.screen.public_ip
}

output "ssh" {
  description = "Copy-paste to get on the box"
  value       = "ssh -i ${var.key_name}.pem ubuntu@${aws_eip.screen.public_ip}"
}
