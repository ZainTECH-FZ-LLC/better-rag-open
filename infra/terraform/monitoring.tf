# ── Monitoring Stack ──

resource "azurerm_log_analytics_workspace" "main" {
  name                = "law-better-rag"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_monitor_workspace" "prometheus" {
  name                = "prom-better-rag"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  name                = "dcr-prometheus"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  data_sources {
    prometheus_forwarder {
      name    = "PrometheusDataSource"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }

  destinations {
    monitor_account {
      monitor_account_id = azurerm_monitor_workspace.prometheus.id
      name               = "MonitoringAccount"
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["MonitoringAccount"]
  }

  tags = var.tags
}

resource "azurerm_dashboard_grafana" "grafana" {
  name                = "grafana-better-rag"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"

  azure_monitor_workspace_integrations {
    resource_id = azurerm_monitor_workspace.prometheus.id
  }

  tags = var.tags
}
