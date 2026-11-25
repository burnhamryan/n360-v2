# The server's own permissions: Session Manager (admin and tunnels, no SSH), the bucket, its Parameter Store prefix.
resource "aws_iam_role" "server" {
  name = "${var.name}-server"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "server" {
  name = "${var.name}-server"
  role = aws_iam_role.server.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ListBucket", Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.this.arn },
      { Sid = "ReadExports", Effect = "Allow", Action = "s3:GetObject", Resource = "${aws_s3_bucket.this.arn}/exports/*" },
      { Sid = "Backups", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.this.arn}/backups/*" },
      {
        Sid      = "OwnParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/n360/${var.name}/*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "server" {
  name = "${var.name}-server"
  role = aws_iam_role.server.name
}

# For you (the operator) on your laptop: attach this policy to your IAM user or role. It allows the tunnel
# (Session Manager port forwarding to this instance only), reading the generated passwords, using the bucket,
# and running update.sh. Terraform itself needs broader rights; this is for day-to-day work.
resource "aws_iam_policy" "operator" {
  name        = "${var.name}-operator"
  description = "N360 day-to-day: tunnel to the server, read its parameters, use its bucket, run update.sh"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "FindInstance", Effect = "Allow", Action = "ec2:DescribeInstances", Resource = "*" },
      {
        Sid    = "TunnelAndCommands"
        Effect = "Allow"
        Action = ["ssm:StartSession", "ssm:SendCommand"]
        Resource = [
          aws_instance.server.arn,
          "arn:aws:ssm:${var.region}::document/AWS-StartPortForwardingSession",
          "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
        ]
      },
      { Sid = "OwnSessions", Effect = "Allow", Action = ["ssm:TerminateSession", "ssm:ResumeSession"], Resource = "arn:aws:ssm:*:*:session/$${aws:userid}-*" },
      { Sid = "CommandResults", Effect = "Allow", Action = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"], Resource = "*" },
      {
        Sid      = "Parameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/n360/${var.name}/*"
      },
      { Sid = "Bucket", Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.this.arn },
      { Sid = "Objects", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.this.arn}/*" },
    ]
  })
}
