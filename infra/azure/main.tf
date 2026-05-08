terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.6" }
  }
}

provider "azurerm" {
  features {}
}

variable "location"           { default = "eastus2" }
variable "project"            { default = "spakespoker" }  # azure name limits, no dash
variable "env"                { default = "dev" }
variable "image_uri"          { description = "ACR image URI for the backend container" }
variable "db_password"        { sensitive = true }
variable "jwt_secret"         { sensitive = true }
variable "x_client_id"        { sensitive = true; default = "" }
variable "x_client_secret"    { sensitive = true; default = "" }
variable "elevenlabs_api_key" { sensitive = true; default = "" }

locals {
  name = "${var.project}${var.env}"
  tags = { project = var.project, env = var.env, managed_by = "terraform" }
}

resource "azurerm_resource_group" "main" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

# ---------- Networking ----------
resource "azurerm_virtual_network" "main" {
  name                = "${local.name}-vnet"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = ["10.10.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.10.1.0/24"]
  delegation {
    name = "ca-delegation"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }
}

resource "azurerm_subnet" "data" {
  name                 = "data"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.10.2.0/24"]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "fs-delegation"
    service_delegation {
      name = "Microsoft.DBforPostgreSQL/flexibleServers"
    }
  }
}

# ---------- Postgres Flexible Server ----------
resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.name}-pg-link"
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
  resource_group_name   = azurerm_resource_group.main.name
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "${local.name}-pg"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  administrator_login           = "poker"
  administrator_password        = var.db_password
  zone                          = "1"
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  public_network_access_enabled = false
  depends_on                    = [azurerm_private_dns_zone_virtual_network_link.postgres]
  tags                          = local.tags
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "poker"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ---------- Redis ----------
resource "azurerm_redis_cache" "main" {
  name                = "${local.name}-redis"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  capacity            = 0
  family              = "C"
  sku_name            = "Basic"
  non_ssl_port_enabled = false
  minimum_tls_version = "1.2"
  tags                = local.tags
}

# ---------- Container Apps ----------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${local.name}-env"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  tags                       = local.tags
}

resource "azurerm_container_app" "main" {
  name                         = "${local.name}-app"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  template {
    min_replicas = 1
    max_replicas = 3
    container {
      name   = "app"
      image  = var.image_uri
      cpu    = 0.5
      memory = "1Gi"

      env { name = "ENV"; value = var.env }
      env {
        name  = "DATABASE_URL"
        value = "postgresql+asyncpg://poker:${var.db_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/poker"
      }
      env {
        name  = "REDIS_URL"
        value = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:6380/0"
      }
      env { name = "JWT_SECRET";         value = var.jwt_secret }
      env { name = "X_CLIENT_ID";        value = var.x_client_id }
      env { name = "X_CLIENT_SECRET";    value = var.x_client_secret }
      env { name = "ELEVENLABS_API_KEY"; value = var.elevenlabs_api_key }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  tags = local.tags
}

output "app_url" {
  value = "https://${azurerm_container_app.main.ingress[0].fqdn}"
}
