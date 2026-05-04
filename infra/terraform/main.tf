terraform {
  required_version = ">= 1.7.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "betterragterraform"
    container_name       = "tfstate"
    key                  = "better-rag.tfstate"
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

# ── Virtual Network ──

resource "azurerm_virtual_network" "main" {
  name                = "vnet-${var.cluster_name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = ["10.0.0.0/16"]
  tags                = var.tags
}

resource "azurerm_subnet" "aks" {
  name                 = "snet-aks"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.0.0/20"]
}

resource "azurerm_subnet" "data" {
  name                 = "snet-data"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.16.0/24"]

  delegation {
    name = "pg-delegation"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "redis" {
  name                 = "snet-redis"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.17.0/24"]
}

# ── AKS Cluster ──

resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Standard"

  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                         = "system"
    vm_size                      = "Standard_D4s_v5"
    node_count                   = 3
    only_critical_addons_enabled = true
    vnet_subnet_id               = azurerm_subnet.aks.id
    os_disk_size_gb              = 128
    os_disk_type                 = "Managed"

    upgrade_settings {
      max_surge = "33%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "calico"
    service_cidr   = "10.1.0.0/16"
    dns_service_ip = "10.1.0.10"
  }

  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    rotation_poll_interval   = "5m"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }

  tags = var.tags
}

# ── AKS Node Pools ──

resource "azurerm_kubernetes_cluster_node_pool" "apppool" {
  name                  = "apppool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"
  min_count             = 3
  max_count             = 6
  auto_scaling_enabled  = true
  vnet_subnet_id        = azurerm_subnet.aks.id
  os_disk_size_gb       = 128

  node_labels = {
    "workload" = "app"
  }

  upgrade_settings {
    max_surge = "33%"
  }

  tags = var.tags
}

resource "azurerm_kubernetes_cluster_node_pool" "heavypool" {
  name                  = "heavypool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"
  min_count             = 2
  max_count             = 4
  auto_scaling_enabled  = true
  vnet_subnet_id        = azurerm_subnet.aks.id
  os_disk_size_gb       = 256

  node_labels = {
    "workload" = "heavy"
  }

  node_taints = [
    "workload=heavy:NoSchedule"
  ]

  upgrade_settings {
    max_surge = "33%"
  }

  tags = var.tags
}

resource "azurerm_kubernetes_cluster_node_pool" "datapool" {
  name                  = "datapool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_E8s_v5"
  node_count            = 2
  vnet_subnet_id        = azurerm_subnet.aks.id
  os_disk_size_gb       = 256

  node_labels = {
    "workload" = "data"
  }

  node_taints = [
    "workload=data:NoSchedule"
  ]

  tags = var.tags
}

# ── Azure Container Registry ──

resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}
