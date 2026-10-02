terraform {
  required_version = ">= 1.9"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 5.8" }
    azuread = { source = "hashicorp/azuread", version = "~> 3.10" }
    random  = { source = "hashicorp/random", version = "~> 3.9" }
  }
  # State holds generated secrets (they also go to Key Vault): keep it in a private, encrypted
  # storage account with versioning, never in Git. Configure with -backend-config at init.
  backend "azurerm" {}
}

provider "azurerm" {
  features {
    key_vault { purge_soft_delete_on_destroy = false }
  }
  subscription_id = var.subscription_id
}

provider "azuread" {}
