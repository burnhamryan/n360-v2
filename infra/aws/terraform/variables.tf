variable "name" {
  description = "Name for every resource, the instance's Name tag, and the Parameter Store prefix /n360/<name>/."
  type        = string
  default     = "n360"
}

variable "region" {
  description = "AWS region. Where customer data may live is a company decision (docs/08_DECISIONS.md)."
  type        = string
}

variable "instance_type" {
  description = "EC2 type. ClickHouse wants at least 8 GB RAM; N360's data is small."
  type        = string
  default     = "t3.large"
}

variable "data_volume_gb" {
  description = "Data volume (ClickHouse, Grafana, Caddy certificates, generated passwords). Snapshotted daily."
  type        = number
  default     = 50
}

variable "repo_url" {
  description = "Git repository the server runs."
  type        = string
  default     = "https://github.com/burnhamryan/n360-v2"
}

variable "git_ref" {
  description = "Tag, branch or commit the server runs. Change it and run update.sh (or re-create) to upgrade."
  type        = string
  default     = "main"
}

variable "domain" {
  description = "Public hostname for Grafana over HTTPS, e.g. n360.example.com. Empty = no public access (Session Manager tunnel only)."
  type        = string
  default     = ""
}

variable "https_allowed_cidrs" {
  description = "Who may reach ports 80/443 when a domain is set. Google sign-in protects Grafana either way; narrow this to office or VPN ranges if you can."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "google_client_id" {
  description = "Google OAuth client ID for Grafana sign-in. The client secret goes in Parameter Store at /n360/<name>/google_client_secret (never in Terraform)."
  type        = string
  default     = ""
}

variable "google_allowed_domains" {
  description = "Email domains allowed to sign in with Google, space separated, e.g. example.com."
  type        = string
  default     = ""
}

variable "google_hosted_domain" {
  description = "Google Workspace domain shown on the Google sign-in screen, e.g. example.com."
  type        = string
  default     = ""
}

variable "server_loads" {
  description = "true = the server loads new exports from s3://<bucket>/exports every hour. false = loads run from your laptop through the tunnel."
  type        = bool
  default     = false
}

variable "vpc_cidr" {
  description = "CIDR for the VPC this module creates (ignored when subnet_id is set)."
  type        = string
  default     = "10.60.0.0/24"
}

variable "subnet_id" {
  description = "Use an existing subnet (with outbound internet access) instead of creating a VPC. Empty = create one."
  type        = string
  default     = ""
}

variable "snapshot_retention_days" {
  description = "Days to keep the daily data-volume snapshots (AWS Backup)."
  type        = number
  default     = 35
}

variable "export_retention_days" {
  description = "Days to keep nightly exports in s3://<bucket>/exports (history is in ClickHouse and its backups)."
  type        = number
  default     = 90
}

variable "backup_retention_days" {
  description = "Days to keep history backups in s3://<bucket>/backups."
  type        = number
  default     = 365
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
