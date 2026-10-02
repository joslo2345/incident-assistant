# Managed Postgres with TimescaleDB and pgvector, reachable only from the VNet.
resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "${var.name}-pg"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  sku_name                      = var.postgres_sku
  storage_mb                    = var.postgres_storage_mb
  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false
  administrator_login           = "iaadmin"
  administrator_password        = random_password.postgres_admin.result
  backup_retention_days         = 7
  zone                          = "1"
  tags                          = var.tags
  depends_on                    = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "telemetry" {
  name      = "telemetry"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Extensions the migrations create (TimescaleDB needs preloading).
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "TIMESCALEDB,VECTOR"
}

resource "azurerm_postgresql_flexible_server_configuration" "preload" {
  name      = "shared_preload_libraries"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "timescaledb"
}

resource "azurerm_postgresql_flexible_server_configuration" "tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}

# Audit-friendly logging and brute-force throttling.
resource "azurerm_postgresql_flexible_server_configuration" "logging" {
  for_each = {
    log_connections              = "on"
    log_disconnections           = "on"
    log_checkpoints              = "on"
    "connection_throttle.enable" = "on"
  }
  name      = each.key
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = each.value
}
