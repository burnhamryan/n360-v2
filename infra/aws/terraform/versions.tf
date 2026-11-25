terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.74" # 6.74.0 = latest on 25 Nov 2026
    }
  }
  # State: keep it somewhere safe and shared, e.g. an S3 bucket you own (see backend.tf.example).
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge({ Project = "n360", ManagedBy = "terraform" }, var.tags)
  }
}
