# ── Azure Container Registry (Premium — private endpoint + geo-replication) ──

resource "azurerm_container_registry" "acr_premium" {
  name                          = "${var.acr_name}prem"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = false
  tags                          = var.tags

  network_rule_set {
    default_action = "Deny"

    virtual_network {
      action    = "Allow"
      subnet_id = azurerm_subnet.aks.id
    }
  }

  retention_policy {
    days    = 30
    enabled = true
  }

  trust_policy {
    enabled = false
  }
}

# ── AKS pull permission from premium registry ─────────────────────────────────

resource "azurerm_role_assignment" "aks_acr_premium_pull" {
  scope                = azurerm_container_registry.acr_premium.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
}

# ── Build agent push permission (CI/CD service principal) ─────────────────────

resource "azurerm_role_assignment" "ci_acr_push" {
  scope                = azurerm_container_registry.acr_premium.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

# ── Diagnostic settings for ACR ───────────────────────────────────────────────

resource "azurerm_monitor_diagnostic_setting" "acr" {
  name                       = "diag-acr"
  target_resource_id         = azurerm_container_registry.acr_premium.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "ContainerRegistryRepositoryEvents"
  }

  enabled_log {
    category = "ContainerRegistryLoginEvents"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}
