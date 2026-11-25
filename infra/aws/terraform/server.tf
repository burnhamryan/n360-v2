data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${startswith(var.instance_type, "t4g") || can(regex("^[a-z]+[0-9]+g", var.instance_type)) ? "arm64" : "x86_64"}"
}

resource "aws_instance" "server" {
  ami                    = data.aws_ssm_parameter.al2023.insecure_value
  instance_type          = var.instance_type
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = aws_iam_instance_profile.server.name

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # containers may reach instance metadata (not needed today)
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true # disposable: everything that matters is on the data volume
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    name     = var.name
    region   = var.region
    repo_url = var.repo_url
    git_ref  = var.git_ref
  })
  user_data_replace_on_change = false # settings change through update.sh, not by replacing the server

  lifecycle {
    ignore_changes = [ami, user_data] # a newer AMI never replaces the server by surprise
  }

  depends_on = [aws_ssm_parameter.config]
  tags       = { Name = var.name }
}

# The server's settings. bootstrap.sh reads this on every run, so: change a variable, `terraform apply`, then run
# update.sh on the server (see the next_steps output). Nothing secret here; secrets stay in Parameter Store
# SecureStrings the server writes itself (or you write: google_client_secret).
resource "aws_ssm_parameter" "config" {
  name = "/n360/${var.name}/config"
  type = "String"
  value = join("\n", [
    "N360_NAME='${var.name}'",
    "AWS_REGION='${var.region}'",
    "REPO_URL='${var.repo_url}'",
    "GIT_REF='${var.git_ref}'",
    "DATA_VOLUME_ID='${aws_ebs_volume.data.id}'",
    "BUCKET='${aws_s3_bucket.this.bucket}'",
    "N360_DOMAIN='${var.domain}'",
    "GOOGLE_CLIENT_ID='${var.google_client_id}'",
    "GOOGLE_ALLOWED_DOMAINS='${var.google_allowed_domains}'",
    "GOOGLE_HOSTED_DOMAIN='${var.google_hosted_domain}'",
    "SERVER_LOADS='${var.server_loads}'",
  ])
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.server.id
}

# A fixed public address when Grafana is public, so the DNS record never changes.
resource "aws_eip" "server" {
  count    = local.public ? 1 : 0
  instance = aws_instance.server.id
  domain   = "vpc"
  tags     = { Name = var.name }
}
