# ── Workload Identity ──
# Zero-secret authentication: AKS pods authenticate to Azure services
# via federated OIDC tokens, no credentials stored anywhere.

resource "azurerm_user_assigned_identity" "workload" {
  name                = "id-better-rag-workload"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

# Federate the managed identity with the AKS OIDC issuer
resource "azurerm_federated_identity_credential" "workload" {
  name                = "fic-better-rag"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.workload.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.aks.oidc_issuer_url
  subject             = "system:serviceaccount:better-rag:better-rag-sa"
}

# ── Key Vault ──

resource "azurerm_key_vault" "main" {
  name                       = "better-rag-kv"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = true
  enable_rbac_authorization  = true
  tags                       = var.tags
}

# Grant the workload identity access to Key Vault secrets
resource "azurerm_role_assignment" "workload_kv_reader" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

# Grant the Terraform SP access to manage secrets
resource "azurerm_role_assignment" "terraform_kv_admin" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ── Key Vault Secrets ──

resource "azurerm_key_vault_secret" "pg_password" {
  name         = "pg-admin-password"
  value        = var.pg_admin_password
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_key_vault_secret" "redis_key" {
  name         = "redis-primary-key"
  value        = azurerm_redis_cache.redis.primary_access_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_key_vault_secret" "graph_client_secret" {
  name         = "graph-client-secret"
  value        = var.graph_client_secret
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_key_vault_secret" "anthropic_api_key" {
  name         = "anthropic-api-key"
  value        = var.anthropic_api_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_key_vault_secret" "azure_openai_key" {
  name         = "azure-openai-api-key"
  value        = var.azure_openai_api_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_key_vault_secret" "ocr_key" {
  name         = "ocr-azure-key"
  value        = var.ocr_azure_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.terraform_kv_admin]
}

# ── Blob Storage RBAC ──

resource "azurerm_storage_account" "blob" {
  name                     = "betterragblob"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.blob.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "workload_blob" {
  scope                = azurerm_storage_account.blob.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}
