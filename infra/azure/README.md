# Azure deployment

```bash
# 1. Build and push to ACR (one-time setup omitted)
docker build -t spaces-poker -f ../docker/Dockerfile ../..
az acr login --name <your-acr>
docker tag spaces-poker:latest <your-acr>.azurecr.io/spaces-poker:latest
docker push <your-acr>.azurecr.io/spaces-poker:latest

# 2. Provision
terraform init
terraform apply \
  -var "image_uri=<your-acr>.azurecr.io/spaces-poker:latest" \
  -var "db_password=$(openssl rand -hex 24)" \
  -var "jwt_secret=$(openssl rand -hex 32)"

# 3. Migrations: open a temporary public endpoint or use a jumpbox
DATABASE_URL=... alembic upgrade head
```

For production: enable HA replica on Postgres Flex, switch Redis to Premium with private endpoint, add Front Door for global routing.
