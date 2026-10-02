variable "subscription_id" {
  type        = string
  description = "Azure subscription to deploy into."
}

variable "name" {
  type        = string
  default     = "incident-assistant"
  description = "Prefix for resource names (lowercase letters, digits, dashes)."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name))
    error_message = "name: 3-21 lowercase letters, digits or dashes, starting with a letter."
  }
}

variable "location" {
  type    = string
  default = "westus2"
}

variable "github_repository" {
  type        = string
  description = "owner/repo allowed to deploy through GitHub Actions OIDC (no stored secrets)."
}

variable "github_environment" {
  type        = string
  default     = "production"
  description = "Only workflow jobs running in this GitHub environment get the deploy identity."
}

variable "aks_node_size" {
  type        = string
  default     = "Standard_B4ms" # 4 vCPU, 16 GB, burstable: enough for the whole system
  description = "One node runs everything at demo scale; raise node_count for real load."
}

variable "aks_node_count" {
  type    = number
  default = 1
}

variable "postgres_sku" {
  type    = string
  default = "B_Standard_B1ms" # burstable, 1 vCore, 2 GB
}

variable "postgres_storage_mb" {
  type    = number
  default = 32768
}

variable "admin_ip_ranges" {
  type        = list(string)
  description = "CIDRs of the people running Terraform and kubectl (e.g. your office or VPN). Only these reach the Kubernetes API server and Key Vault; deploys go through `az aks command invoke`."
  validation {
    condition     = length(var.admin_ip_ranges) > 0
    error_message = "Give at least one admin CIDR: the API server and Key Vault are closed to everyone else."
  }
}

variable "budget_monthly_usd" {
  type        = number
  default     = 150
  description = "Monthly budget for the resource group; alerts at 50/80/100 percent."
}

variable "budget_alert_email" {
  type        = string
  default     = ""
  description = "Who gets budget alerts. Empty = no budget resource."
}

variable "tags" {
  type    = map(string)
  default = { project = "incident-assistant", managed_by = "terraform" }
}
