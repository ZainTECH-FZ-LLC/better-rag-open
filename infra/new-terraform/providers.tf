terraform {
    required_providers {
        azurerm = {
            source  = "hashicorp/azurerm"
            version = "=4.1.0"
        }
        postgresql = {
          source  = "cyrilgdn/postgresql"
          version = "~> 1.16"
        }
    }
}

# Configure the Microsoft Azure Provider
provider "azurerm" {
    resource_provider_registrations = "none" 
    subscription_id = "a0392be1-0cba-4c5f-8042-fcafc8c0151f"
    features {}
}

provider "helm" {
  kubernetes  = {
    host                   = azurerm_kubernetes_cluster.aks.kube_config[0].host
    client_certificate     = base64decode(azurerm_kubernetes_cluster.aks.kube_config[0].client_certificate)
    client_key             = base64decode(azurerm_kubernetes_cluster.aks.kube_config[0].client_key)
    cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.aks.kube_config[0].cluster_ca_certificate)
  }
}

provider "postgresql" {
  host     = azurerm_postgresql_flexible_server.postgres.fqdn
  username = "pgadmin"  # no @server needed when using env vars
  password = data.azurerm_key_vault_secret.pg_admin_password.value
  database = "postgres" # connect to default DB first
  sslmode  = "require"
  superuser = false
}
