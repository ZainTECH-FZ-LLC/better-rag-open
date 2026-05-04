# ── Azure Database for PostgreSQL Flexible Server ──

resource "azurerm_private_dns_zone" "postgres" {
  name                = "better-rag.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "pg-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  resource_group_name   = azurerm_resource_group.main.name
  virtual_network_id    = azurerm_virtual_network.main.id
}

resource "azurerm_postgresql_flexible_server" "pg" {
  name                          = "better-rag-pg"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  sku_name                      = "GP_Standard_D4s_v3"
  storage_mb                    = 131072 # 128 GB
  administrator_login           = "pgadmin"
  administrator_password        = var.pg_admin_password
  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  high_availability {
    mode = "ZoneRedundant"
  }

  tags = var.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "betterrag" {
  name      = "betterrag"
  server_id = azurerm_postgresql_flexible_server.pg.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  server_id = azurerm_postgresql_flexible_server.pg.id
  name      = "azure.extensions"
  value     = "vector,pg_trgm,btree_gin"
}

resource "azurerm_postgresql_flexible_server_configuration" "shared_preload" {
  server_id = azurerm_postgresql_flexible_server.pg.id
  name      = "shared_preload_libraries"
  value     = "pg_stat_statements"
}

# ── Azure Cache for Redis ──

resource "azurerm_redis_cache" "redis" {
  name                          = "better-rag-redis"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  capacity                      = 1
  family                        = "P"
  sku_name                      = "Premium"
  non_ssl_port_enabled          = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false

  redis_configuration {
    maxmemory_policy = "allkeys-lru"
  }

  tags = var.tags
}

resource "azurerm_private_endpoint" "redis" {
  name                = "pe-redis"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.redis.id

  private_service_connection {
    name                           = "redis-connection"
    private_connection_resource_id = azurerm_redis_cache.redis.id
    subresource_names              = ["redisCache"]
    is_manual_connection           = false
  }

  tags = var.tags
}
