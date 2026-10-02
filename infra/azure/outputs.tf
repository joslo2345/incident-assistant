output "resource_group" { value = azurerm_resource_group.main.name }
output "aks_name" { value = azurerm_kubernetes_cluster.main.name }
output "acr_login_server" { value = azurerm_container_registry.main.login_server }
output "postgres_host" { value = azurerm_postgresql_flexible_server.main.fqdn }
output "key_vault_uri" { value = azurerm_key_vault.main.vault_uri }
output "external_secrets_client_id" { value = azurerm_user_assigned_identity.external_secrets.client_id }
output "tenant_id" { value = data.azurerm_client_config.current.tenant_id }

# Values for the GitHub environment's variables (not secrets: they identify, they don't
# authenticate).
output "github_variables" {
  value = {
    AZURE_CLIENT_ID       = azuread_application.github.client_id
    AZURE_TENANT_ID       = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID = var.subscription_id
    AZURE_RESOURCE_GROUP  = azurerm_resource_group.main.name
    AKS_NAME              = azurerm_kubernetes_cluster.main.name
    ACR_LOGIN_SERVER      = azurerm_container_registry.main.login_server
    POSTGRES_HOST         = azurerm_postgresql_flexible_server.main.fqdn
  }
}
