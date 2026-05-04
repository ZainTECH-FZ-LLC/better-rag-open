# ── Azure Key Vault ───────────────────────────────────────────────────────────

resource "azurerm_key_vault" "main" {
  name                          = "kv-${var.cluster_name}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  soft_delete_retention_days    = 90
  purge_protection_enabled      = true
  public_network_access_enabled = false
  tags                          = var.tags

  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices"
    virtual_network_subnet_ids = [azurerm_subnet.aks.id]
  }
}

# ── Access Policies ───────────────────────────────────────────────────────────

# AKS workload identity — read-only access to secrets
resource "azurerm_key_vault_access_policy" "aks_workload" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.workload.principal_id

  secret_permissions = ["Get", "List"]
}

# Terraform service principal — full management
resource "azurerm_key_vault_access_policy" "terraform" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions      = ["Get", "List", "Set", "Delete", "Purge", "Recover"]
  key_permissions         = ["Get", "List", "Create", "Delete", "Update"]
  certificate_permissions = ["Get", "List", "Create", "Delete", "Update", "Import"]
}

# ── Secrets ───────────────────────────────────────────────────────────────────

resource "azurerm_key_vault_secret" "pg_password" {
  name         = "postgres-admin-password"
  value        = var.pg_admin_password
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "neo4j_password" {
  name         = "neo4j-password"
  value        = var.neo4j_password
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "graph_client_secret" {
  name         = "graph-client-secret"
  value        = var.graph_client_secret
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "anthropic_api_key" {
  name         = "anthropic-api-key"
  value        = var.anthropic_api_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "azure_openai_api_key" {
  name         = "azure-openai-api-key"
  value        = var.azure_openai_api_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "azure_openai_endpoint" {
  name         = "azure-openai-endpoint"
  value        = var.azure_openai_endpoint
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "ocr_azure_key" {
  name         = "ocr-azure-key"
  value        = var.ocr_azure_key
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}

resource "azurerm_key_vault_secret" "ocr_azure_endpoint" {
  name         = "ocr-azure-endpoint"
  value        = var.ocr_azure_endpoint
  key_vault_id = azurerm_key_vault.main.id
  tags         = var.tags

  depends_on = [azurerm_key_vault_access_policy.terraform]
}
