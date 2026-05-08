# AWS deployment

```bash
# 1. Build and push image to ECR (one-time setup omitted)
docker build -t spaces-poker -f ../docker/Dockerfile ../..
docker tag spaces-poker:latest <account>.dkr.ecr.us-east-2.amazonaws.com/spaces-poker:latest
docker push <account>.dkr.ecr.us-east-2.amazonaws.com/spaces-poker:latest

# 2. Provision
terraform init
terraform apply \
  -var "image_uri=<account>.dkr.ecr.us-east-2.amazonaws.com/spaces-poker:latest" \
  -var "db_password=$(openssl rand -hex 24)" \
  -var "jwt_secret=$(openssl rand -hex 32)"

# 3. Run migrations from a local container hitting the RDS endpoint
DATABASE_URL=postgresql+asyncpg://poker:...@<rds-host>:5432/poker alembic upgrade head
```

For production: add an ACM cert + HTTPS listener, route via Route53, switch RDS to Multi-AZ, and bump task CPU/memory.
