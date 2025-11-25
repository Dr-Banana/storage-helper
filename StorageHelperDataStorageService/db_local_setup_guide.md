# Database Local Setup Guide

## Prerequisites

- Docker installed and running
- Docker Compose installed

## Quick Start

Simply run the initialization script:

```bash
chmod +x scripts/init-db.sh
./scripts/init-db.sh
```

The script will:
1. Check Docker installation
2. Start the MySQL container
3. Wait for MySQL to be ready
4. Display all database tables

## Connection Details

Once the script completes successfully, you can connect to the database:

- **Host**: localhost
- **Port**: 3306
- **User**: root
- **Password**: root
- **Database**: storage_helper

## Verify Connection

Test the connection with:

```bash
docker-compose exec mysql mysql -uroot -proot storage_helper -e "SHOW TABLES;"
```

## Common Commands

**Stop the database:**
```bash
docker-compose down
```

**View database logs:**
```bash
docker-compose logs mysql
```

**Access MySQL CLI:**
```bash
docker-compose exec mysql mysql -uroot -proot storage_helper
```

That's it! Your database is ready to use.
