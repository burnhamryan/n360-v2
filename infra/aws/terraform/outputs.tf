output "instance_id" {
  value = aws_instance.server.id
}

output "bucket" {
  value = aws_s3_bucket.this.bucket
}

output "public_ip" {
  description = "Point the DNS A record for var.domain here (only when a domain is set)."
  value       = local.public ? aws_eip.server[0].public_ip : null
}

output "grafana_url" {
  value = local.public ? "https://${var.domain}" : "tunnel only: code/ops/tunnel.sh, then http://localhost:13000"
}

output "operator_policy_arn" {
  description = "Attach to your own IAM user or role for the tunnel, parameters, bucket and update.sh."
  value       = aws_iam_policy.operator.arn
}

output "next_steps" {
  value = <<-EOT
    1. Attach ${aws_iam_policy.operator.arn} to your IAM user or role.
    2. ${local.public ? "Create a DNS A record ${var.domain} -> ${aws_eip.server[0].public_ip}." : "No domain: Grafana is reachable through the tunnel only."}
    3. ${var.google_client_id != "" ? "Store the Google client secret: aws ssm put-parameter --name /n360/${var.name}/google_client_secret --type SecureString --value '...', then run update.sh." : "Google sign-in off (google_client_id empty)."}
    4. Wait ~5 minutes for first boot (log: /var/log/n360-bootstrap.log on the server).
    5. From your laptop: N360_NAME=${var.name} code/ops/tunnel.sh ; then eval "$(N360_NAME=${var.name} code/ops/remote_env.sh)" and load.
    Later changes to these variables: terraform apply, then on the server run /opt/n360/infra/aws/host/update.sh
    (or: aws ssm send-command --document-name AWS-RunShellScript --instance-ids ${aws_instance.server.id} --parameters 'commands=["/opt/n360/infra/aws/host/update.sh"]').
  EOT
}
