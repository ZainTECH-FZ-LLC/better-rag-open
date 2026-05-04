output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.aks.name
}

output "aks_resource_group" {
  value = azurerm_resource_group.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "pg_host" {
  value     = azurerm_postgresql_flexible_server.pg.fqdn
  sensitive = true
}

output "redis_host" {
  value     = azurerm_redis_cache.redis.hostname
  sensitive = true
}

output "redis_port" {
  value = azurerm_redis_cache.redis.ssl_port
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "workload_identity_client_id" {
  value = azurerm_user_assigned_identity.workload.client_id
}

output "blob_account_url" {
  value = azurerm_storage_account.blob.primary_blob_endpoint
}

output "oidc_issuer_url" {
  value = azurerm_kubernetes_cluster.aks.oidc_issuer_url
}

output "grafana_endpoint" {
  value = azurerm_dashboard_grafana.grafana.endpoint
}
