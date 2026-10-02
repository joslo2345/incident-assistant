data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "${var.name}-rg"
  location = var.location
  tags     = var.tags
}

# ------------------------------------------------------------------------------------- network
resource "azurerm_virtual_network" "main" {
  name                = "${var.name}-vnet"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = ["10.40.0.0/16"]
  tags                = var.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.0.0/20"]
  # Lets Key Vault accept the cluster (External Secrets) while staying closed to the internet.
  service_endpoint { service = "Microsoft.KeyVault" }
}

# Postgres lives in its own delegated subnet with no public endpoint.
resource "azurerm_subnet" "postgres" {
  name                 = "postgres"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.40.16.0/24"]
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${var.name}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                = "postgres"
  private_dns_zone_id = azurerm_private_dns_zone.postgres.id
  virtual_network_id  = azurerm_virtual_network.main.id
}

# ------------------------------------------------------------------------------------- registry
resource "azurerm_container_registry" "main" {
  name                = replace("${var.name}acr", "-", "")
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false # pulls use the cluster identity, pushes use GitHub OIDC
  tags                = var.tags
}

# ------------------------------------------------------------------------------------- cluster
resource "azurerm_kubernetes_cluster" "main" {
  name                = "${var.name}-aks"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  dns_prefix          = var.name
  sku_tier            = "Free"

  # Entra ID for people and pipelines; no static admin kubeconfig.
  local_account_disabled = true
  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = data.azurerm_client_config.current.tenant_id
  }

  # Workload identity: pods (External Secrets) get Azure tokens without stored credentials.
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                        = "system"
    vm_size                     = var.aks_node_size
    node_count                  = var.aks_node_count
    vnet_subnet_id              = azurerm_subnet.aks.id
    os_disk_type                = "Managed"
    temporary_name_for_rotation = "systemtmp"
    upgrade_settings { max_surge = "1" }
  }

  identity { type = "SystemAssigned" }

  # Fixed node pool (no auto-provisioning): predictable cost.
  node_provisioning_profile { mode = "Manual" }

  network_profile {
    network_plugin = "azure"
    network_policy = "calico" # the chart's NetworkPolicies are enforced
    service_cidr   = "10.41.0.0/16"
    dns_service_ip = "10.41.0.10"
  }

  # Only admins' networks reach the API server. CI deploys don't need it: they run helm through
  # `az aks command invoke`, which goes via the Azure control plane.
  api_server_access_profile {
    authorized_ip_ranges = var.admin_ip_ranges
  }

  automatic_upgrade_channel = "patch"
  tags                      = var.tags
}

resource "azurerm_role_assignment" "aks_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}
