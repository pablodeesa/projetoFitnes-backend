# Infraestrutura Backend — Projeto Liric
- **Host Proxmox:** 192.168.1.100 (Web: 8006 | SSH: 22)
- **Container LXC 100 (backend-db):** 192.168.1.30 (Ubuntu 24.04) | user: root | pass: Admin@1234
- **PostgreSQL 16:** 192.168.1.30:5432 | db: liric_db | user: liric_admin | pass: Liric@2026Db!
- **String de Conexão:** postgresql://liric_admin:Liric@2026Db!@192.168.1.30:5432/liric_db
