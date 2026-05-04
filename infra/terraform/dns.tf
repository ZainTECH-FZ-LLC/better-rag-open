# ── Azure Public DNS Zone ─────────────────────────────────────────────────────
# Only created when dns_zone_name is provided (leave empty to manage DNS externally)

resource "azurerm_dns_zone" "public" {
  count               = var.dns_zone_name != "" ? 1 : 0
  name                = var.dns_zone_name
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

# ── DNS Records ───────────────────────────────────────────────────────────────

# A record for the main application ingress (points to nginx-ingress LB IP)
# The IP is populated after the nginx-ingress Helm release creates the LoadBalancer.
# Use `terraform apply -target=...` or set via a data source after cluster provisioning.

resource "azurerm_dns_a_record" "app" {
  count               = var.dns_zone_name != "" ? 1 : 0
  name                = "@"
  zone_name           = azurerm_dns_zone.public[0].name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = ["0.0.0.0"]  # Placeholder — update after ingress IP is assigned
  tags                = var.tags

  lifecycle {
    ignore_changes = [records]
  }
}

resource "azurerm_dns_a_record" "wildcard" {
  count               = var.dns_zone_name != "" ? 1 : 0
  name                = "*"
  zone_name           = azurerm_dns_zone.public[0].name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = ["0.0.0.0"]  # Placeholder — update after ingress IP is assigned
  tags                = var.tags

  lifecycle {
    ignore_changes = [records]
  }
}

# TXT record for domain ownership verification (ACME / cert-manager)
resource "azurerm_dns_txt_record" "acme_challenge" {
  count               = var.dns_zone_name != "" ? 1 : 0
  name                = "_acme-challenge"
  zone_name           = azurerm_dns_zone.public[0].name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 60
  tags                = var.tags

  record {
    value = "managed-by-cert-manager"
  }
}

# ── Role Assignment for cert-manager DNS01 solver ─────────────────────────────
# cert-manager workload identity needs DNS Zone Contributor to create TXT records

resource "azurerm_role_assignment" "cert_manager_dns" {
  count                = var.dns_zone_name != "" ? 1 : 0
  scope                = azurerm_dns_zone.public[0].id
  role_definition_name = "DNS Zone Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

# If the DNS zone lives in a separate resource group, grant access there too
resource "azurerm_role_assignment" "cert_manager_dns_rg" {
  count = (
    var.dns_zone_name != "" && var.dns_zone_resource_group != "" &&
    var.dns_zone_resource_group != var.resource_group_name
  ) ? 1 : 0

  scope = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${var.dns_zone_resource_group}"
  role_definition_name = "DNS Zone Contributor"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}
