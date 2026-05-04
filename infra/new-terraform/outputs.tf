output "redis_primary_key" {
  value     = azurerm_redis_cache.redis.primary_access_key
  sensitive = true
}

output "redis_hostname" {
  value = azurerm_redis_cache.redis.hostname
}

output "postgres_username" {
  value = "pgadmin"
}

output "postgres_password" {
  value     = var.pg_admin_password#data.azurerm_key_vault_secret.pg_admin_password.value
  sensitive = true
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.postgres.fqdn
}

output "postgres_db" {
  value = azurerm_postgresql_flexible_server_database.db.name
}

