
resource "azurerm_resource_group" "zaingpt-dev" {
  name     = "zaingpt-dev"
  location = "UAE North"
}

resource "azurerm_virtual_network" "aks" {
  name                = "vnet-aks-dev"
  location            = azurerm_resource_group.zaingpt-dev.location
  resource_group_name = azurerm_resource_group.zaingpt-dev.name
  address_space       = ["10.0.0.0/16"]
}

resource "azurerm_subnet" "aks" {
  name                 = "subnet-aks-dev"
  resource_group_name  = azurerm_resource_group.zaingpt-dev.name
  virtual_network_name = azurerm_virtual_network.aks.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "aks" {
  name                = "nsg-aks-dev"
  location            = azurerm_resource_group.zaingpt-dev.location
  resource_group_name = azurerm_resource_group.zaingpt-dev.name

  security_rule {
    name                       = "AllowHTTPS"
    priority                   = 1002
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range           = "*"
    destination_port_range      = "443"
    source_address_prefix       = "*"
    destination_address_prefix  = "*"
  }
  security_rule {
    name                       = "AllowNeo4J"
    priority                   = 1001
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range           = "*"
    destination_port_range      = "7687"
    source_address_prefix       = "*"
    destination_address_prefix  = "*"
  }
  security_rule {
    name                       = "AllowNeo4JBrowser"
    priority                   = 1007
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range           = "*"
    destination_port_range      = "7474"
    source_address_prefix       = "*"
    destination_address_prefix  = "*"
  }
  security_rule {
    name                       = "AllowHTTP"
    priority                   = 1003
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range           = "*"
    destination_port_range      = "80"
    source_address_prefix       = "*"
    destination_address_prefix  = "*"
  }
  tags = {
    environment = "dev"
  }
}

# Associate NSG with subnet
resource "azurerm_subnet_network_security_group_association" "aks" {
  subnet_id                 = azurerm_subnet.aks.id
  network_security_group_id = azurerm_network_security_group.aks.id
}

resource "azurerm_container_registry" "acr" {
  name                     = "acrzaingpt"
  resource_group_name      = azurerm_resource_group.zaingpt-dev.name
  location                 = azurerm_resource_group.zaingpt-dev.location
  sku                      = "Basic"  # Can be Standard or Premium
  admin_enabled            = false   # best practice: disable admin account

  tags = {
    environment = "dev"
  }
}


resource "azurerm_kubernetes_cluster" "aks" {
  name                = "aks-dev-cluster"
  location            = azurerm_resource_group.zaingpt-dev.location
  resource_group_name = azurerm_resource_group.zaingpt-dev.name
  dns_prefix          = "aksdev"

  default_node_pool {
    name           = "system"
    node_count     = 2
    vm_size        = "Standard_D4s_v5"
    vnet_subnet_id = azurerm_subnet.aks.id
  }

  identity {
    type = "SystemAssigned"
  }  
  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "10m" # How often to poll AKV for changes
  }
  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"

    pod_cidr     = "192.168.0.0/16"
    service_cidr = "172.16.0.0/16"
    dns_service_ip = "172.16.0.10"

    load_balancer_sku = "standard"
  }

  role_based_access_control_enabled = true

  tags = {
    environment = "dev"
  }
  #workload_identity_enabled = true  # << important
  oidc_issuer_enabled       = true  # << required
}

resource "helm_release" "nginx_ingress" {
  name       = "ingress-nginx"
  namespace  = "ingress-nginx"

  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"

  create_namespace = true

  set = [
    {
      name  = "controller.replicaCount"
      value = "1"
    },
    {
      name  = "controller.service.type"
      value = "LoadBalancer"
    },
    {
      name  = "controller.service.annotations.service\\.beta\\.kubernetes\\.io/azure-load-balancer-health-probe-request-path"
      value = "/healthz"
    }
  ]
}


resource "azurerm_user_assigned_identity" "aks_kv" {
  name                = "aks-kv-identity"
  resource_group_name = azurerm_resource_group.zaingpt-dev.name
  location            = azurerm_resource_group.zaingpt-dev.location
}

resource "azurerm_role_assignment" "kv_access" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.aks_kv.principal_id
}

# Grant the AKS Kubelet Identity access to Key Vault Secrets
resource "azurerm_role_assignment" "aks_kv_secrets_user" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  # Use the Kubelet Identity's object_id (principal_id)
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name = "AcrPull"
  scope                = azurerm_container_registry.acr.id
  
  # Recommended: Skip the AAD check to avoid "Principal not found" errors during creation
  skip_service_principal_aad_check = true
}


#resource "azurerm_role_assignment" "aks_acr_pull" {
#  principal_id   = azurerm_kubernetes_cluster.aks.identity[0].principal_id
#  role_definition_name = "AcrPull"
#  scope          = azurerm_container_registry.acr.id
#}

resource "azurerm_kubernetes_cluster_node_pool" "apppool" {
  name                  = "apppool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"
  node_count            = 1

  vnet_subnet_id = azurerm_subnet.aks.id

  mode = "User"

  auto_scaling_enabled = true
  min_count           = 1
  max_count           = 6

  node_labels = {
    workload = "app"
  }

  node_taints = ["workload=app:NoSchedule"]
  
  tags = {
    environment = "dev"
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "heavypool" {
  name                  = "heavypool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"
  node_count            = 1

  vnet_subnet_id = azurerm_subnet.aks.id

  mode = "User"

  auto_scaling_enabled = true
  min_count           = 1
  max_count           = 6

  node_labels = {
    workload = "heavy"
  }

  node_taints = ["workload=heavy:NoSchedule"]

  tags = {
    environment = "dev"
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "datapool" {
  name                  = "datapool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_E8s_v5"
  node_count            = 1

  vnet_subnet_id = azurerm_subnet.aks.id

  mode = "User"

  auto_scaling_enabled = true
  min_count           = 1
  max_count           = 6

  node_labels = {
    workload = "data"
  }
  
  node_taints = ["workload=data:NoSchedule"]

  tags = {
    environment = "dev"
  }
}

# Keyvault

resource "azurerm_key_vault" "kv" {
  name                       = "zaingpt-kv-dev"
  location                   = azurerm_resource_group.zaingpt-dev.location
  resource_group_name        = azurerm_resource_group.zaingpt-dev.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  enable_rbac_authorization = true
}

# Give current CLI user full secret access: Get, List, Set
resource "azurerm_role_assignment" "kv_admin" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"  # Includes Get, List, Set, Delete
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_key_vault_secret" "pg_password" {
  name         = "pg-password"
  value        = var.pg_password
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin]
}
resource "azurerm_key_vault_secret" "pg_admin_password" {
  name         = "pg-admin-password"
  value        = var.pg_admin_password
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin]
}
resource "azurerm_key_vault_secret" "neo4j_password" {
  name         = "neo4j-password"
  value        = var.neo4j_password
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin]
}


data "azurerm_key_vault_secret" "pg_password" {
  name         = "pg-password"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin, azurerm_key_vault_secret.pg_password]
}

data "azurerm_key_vault_secret" "pg_admin_password" {
  name         = "pg-admin-password"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin, azurerm_key_vault_secret.pg_password]
}


# Redis
resource "azurerm_redis_cache" "redis" {
  name                = "zaingpt-redis"
  location            = azurerm_resource_group.zaingpt-dev.location
  resource_group_name = azurerm_resource_group.zaingpt-dev.name

  capacity            = 1
  family              = "C"
  sku_name            = "Standard"

  minimum_tls_version = "1.2"

  redis_configuration {
    maxmemory_reserved = 50
  }

  tags = {
    environment = "dev"
  }
}

resource "azurerm_key_vault_secret" "redis_key" {
  name         = "redis-password"
  value        = azurerm_redis_cache.redis.primary_access_key
  key_vault_id = azurerm_key_vault.kv.id
  depends_on = [azurerm_role_assignment.kv_admin]
}

# Postgresql

resource "azurerm_postgresql_flexible_server" "postgres" {
  name                   = "zaingpt-postgres"
  resource_group_name    = azurerm_resource_group.zaingpt-dev.name
  location               = azurerm_resource_group.zaingpt-dev.location
  version                = "15"

  administrator_login    = "pgadmin"
  administrator_password = data.azurerm_key_vault_secret.pg_admin_password.value

  storage_mb             = 32768
  sku_name               = "B_Standard_B1ms"
  backup_retention_days  = 7
  public_network_access_enabled = true

  lifecycle {
    ignore_changes = [zone, high_availability]
  }
}

resource "azurerm_postgresql_flexible_server_configuration" "enable_vector" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_all" {
  name             = "allow-all"
  server_id        = azurerm_postgresql_flexible_server.postgres.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "255.255.255.255"
}

resource "azurerm_postgresql_flexible_server_database" "db" {
  name      = "zaingpt"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "postgresql_role" "zaingpt_user" {
  name     = "zaingpt_user"
  login    = true
  password = data.azurerm_key_vault_secret.pg_password.value
  depends_on = [
    azurerm_postgresql_flexible_server.postgres,
    azurerm_postgresql_flexible_server_firewall_rule.allow_all
  ]
}

resource "postgresql_grant" "zaingpt_user_privileges" {
  role       = postgresql_role.zaingpt_user.name
  database   = azurerm_postgresql_flexible_server_database.db.name
  object_type = "schema"
  privileges = ["USAGE"]
  schema     = "public"
  depends_on = [
    azurerm_postgresql_flexible_server.postgres,
    azurerm_postgresql_flexible_server_firewall_rule.allow_all
  ]

}


# Blob Storage

resource "azurerm_storage_account" "storage" {
  name                     = "zaingptblob"
  resource_group_name      = azurerm_resource_group.zaingpt-dev.name
  location                 = azurerm_resource_group.zaingpt-dev.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  tags = {
    environment = "dev"
  }
}
