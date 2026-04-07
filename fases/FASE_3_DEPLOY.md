# Sara — Fase 3: Deploy em Produção

> **Objetivo:** Colocar a Sara rodando 24/7 em um servidor na nuvem com HTTPS, para que o bot no Telegram funcione de forma confiável sem depender da máquina local.

---

## Visão Geral

Esta fase resolve o problema mais imediato: ter o bot disponível o tempo todo. Sem deploy, lembretes falham quando a máquina desliga e o webhook depende de ngrok ativo.

O foco aqui é **praticidade** — o mínimo necessário para ter o sistema rodando de forma estável, segura e com observabilidade básica. Features como dashboard web e RAG vêm depois.

---

## Arquitetura de Deploy

```
Internet
    ↓
DNS + SSL (Cloudflare ou Let's Encrypt)
    ↓
VPS (Ubuntu 22.04 / 24.04)
    ├── Nginx (reverse proxy + SSL)
    │   └── / → FastAPI (porta 8000)
    ├── Docker Compose
    │   └── PostgreSQL (com suporte a pgvector, preparado para Fase 5)
    └── systemd
        ├── sara-api.service (FastAPI + Uvicorn)
        └── sara-bot.service (polling fallback — opcional)
```

### Por que não Dockerizar a aplicação Python?

Poderíamos, mas para um projeto pessoal com um único usuário, rodar direto no host com systemd é mais simples de debugar, atualizar e monitorar. Docker Compose fica só para o PostgreSQL.

---

## Requisitos Funcionais

### RF-01 — Aplicação sempre disponível
O sistema deve funcionar 24/7 sem depender da máquina local.

**Critério de aceitação:**
- Mensagens enviadas às 3h da manhã são respondidas normalmente
- Lembretes são disparados no horário correto independente do estado da máquina local
- Em caso de crash, o processo é reiniciado automaticamente pelo systemd

### RF-02 — SSL/HTTPS obrigatório
Toda comunicação deve ser criptografada — o Telegram exige webhook HTTPS.

**Critério de aceitação:**
- API servida exclusivamente via HTTPS
- Certificado SSL válido e renovado automaticamente
- Webhook do Telegram configurado com URL HTTPS válida

### RF-03 — Variáveis de ambiente seguras
Nenhuma credencial no código ou em arquivos versionados.

**Critério de aceitação:**
- Todas as credenciais configuradas via variáveis de ambiente do systemd
- Secrets nunca aparecem em logs

### RF-04 — Logs e monitoramento básico
Identificar problemas sem precisar adivinhar.

**Critério de aceitação:**
- Logs estruturados com nível (INFO, WARNING, ERROR)
- Logs acessíveis via `journalctl`
- Alerta por Telegram quando a aplicação cai

### RF-05 — Backup do banco de dados
Dados do usuário com backup automático.

**Critério de aceitação:**
- Backup diário do PostgreSQL em arquivo comprimido
- Backups armazenados por pelo menos 7 dias
- Processo de restore documentado

---

## Passo a Passo de Implementação

### 1. Provisionar VPS

**Opções recomendadas:**

| Provedor | Plano | Custo | Observação |
|---|---|---|---|
| **Oracle Cloud** | Always Free (4 vCPU, 24GB RAM) | Grátis | Melhor custo-benefício, mas disponibilidade varia por região |
| **Hetzner** | CX22 (2 vCPU, 4GB) | ~€4/mês | Excelente, datacenter na Europa |
| **DigitalOcean** | Droplet 2GB | ~$12/mês | Interface simples, boa documentação |

**Configuração inicial:**
```bash
# Criar VPS com Ubuntu 22.04 LTS ou 24.04

# Acessar via SSH com chave (desativar login por senha)
ssh-keygen -t ed25519 -C "sara-vps"
ssh-copy-id root@<IP_DO_VPS>

# Criar usuário não-root
adduser sara
usermod -aG sudo sara

# Configurar firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable

# Desativar login por senha no SSH
# Editar /etc/ssh/sshd_config: PasswordAuthentication no
systemctl restart sshd
```

### 2. Instalar Dependências no Servidor

```bash
# Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker sara

# Nginx + Certbot
apt install nginx certbot python3-certbot-nginx -y

# Python 3.12
apt install python3.12 python3.12-venv python3.12-dev -y

# Verificar instalações
docker --version
python3.12 --version
nginx -v
```

### 3. Configurar Domínio

**Opção A — Domínio próprio:**
- Registrar domínio (ex: `sara.russo.dev`)
- Apontar A record para IP do VPS
- Usar Cloudflare (grátis) para DNS + proxy SSL

**Opção B — Subdomínio gratuito:**
- DuckDNS, Afraid.org, ou similar
- Apontar para IP do VPS

**Gerar certificado SSL:**
```bash
# Com Let's Encrypt direto (sem Cloudflare proxy)
certbot --nginx -d sara.russo.dev

# Com Cloudflare: usar certbot com plugin DNS ou deixar Cloudflare gerenciar SSL
```

### 4. Configurar PostgreSQL com Docker Compose

Criar `/opt/sara/docker-compose.yaml`:
```yaml
services:
  postgres:
    image: postgres:16
    container_name: sara-postgres
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_NAME}
    volumes:
      - sara-pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"  # Apenas localhost
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  sara-pgdata:
```

> **Nota:** Usar imagem PostgreSQL 16 com suporte a `pgvector` já incluso (extensão disponível via `CREATE EXTENSION vector`). Preparado para a Fase 5 (RAG).

```bash
# Subir banco
cd /opt/sara
docker compose up -d

# Verificar
docker compose ps
```

### 5. Deploy da Aplicação

```bash
# Clonar repositório
cd /opt
git clone <repo-url> sara-app
cd sara-app

# Criar ambiente virtual
python3.12 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Rodar migrações
alembic upgrade head
```

### 6. Configurar Variáveis de Ambiente (systemd)

**Não usar arquivo `.env` no servidor** — variáveis devem ser injetadas pelo systemd.

Criar `/etc/systemd/system/sara-api.service`:
```ini
[Unit]
Description=Sara Virtual Secretary — FastAPI
After=network.target sara-postgres.service
Wants=sara-postgres.service

[Service]
Type=simple
User=sara
WorkingDirectory=/opt/sara-app
Environment="PATH=/opt/sara-app/venv/bin"
ExecStart=/opt/sara-app/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1
Restart=always
RestartSec=5

# Variáveis de ambiente
Environment="GROQ_API_KEY=<sua-chave>"
Environment="GROQ_MODEL=llama-3.3-70b-versatile"
Environment="GROQ_TEMPERATURE=0.3"
Environment="GROQ_MAX_TOKENS=1024"
Environment="DATABASE_URL=postgresql://<user>:<pass>@localhost:5432/<db>"
Environment="TELEGRAM_BOT_TOKEN=<token>"
Environment="BRIEFING_HORA=08:00"
Environment="TIMEZONE=America/Sao_Paulo"
Environment="HISTORICO_LIMITE=10"
Environment="WEBHOOK_URL=https://sua-url.ngrok-free.app"
Environment="ENVIRONMENT=production"
Environment="LOG_LEVEL=INFO"

# Segurança
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/sara-app

[Install]
WantedBy=multi-user.target
```

> **Importante:** Em produção, substitua `WEBHOOK_URL` pela URL real (ex: `https://sara.russo.dev`).

### 7. Configurar Nginx

Criar `/etc/nginx/sites-available/sara`:
```nginx
server {
    listen 80;
    server_name sara.russo.dev;

    # Redirect HTTP → HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name sara.russo.dev;

    ssl_certificate /etc/letsencrypt/live/sara.russo.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sara.russo.dev/privkey.pem;

    # Proxy para FastAPI
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts para respostas longas do LLM
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

```bash
# Ativar site
ln -s /etc/nginx/sites-available/sara /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 8. Iniciar Serviços

```bash
# Habilitar e iniciar API
systemctl daemon-reload
systemctl enable sara-api
systemctl start sara-api

# Verificar status
systemctl status sara-api
journalctl -u sara-api -f --no-pager
```

### 9. Configurar Webhook do Telegram

```bash
# Registrar webhook com URL HTTPS
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://sua-url/webhook/telegram"}'

# Verificar status
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Resposta esperada:
```json
{
  "ok": true,
  "result": {
    "url": "https://sua-url/webhook/telegram",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "last_error_date": 0,
    "last_error_message": ""
  }
}
```

### 10. Bot em Modo Polling (Fallback)

**Opcional** — útil como backup se o webhook falhar.

Criar `/etc/systemd/system/sara-bot.service`:
```ini
[Unit]
Description=Sara Virtual Secretary — Bot Polling Fallback
After=network.target sara-api.service

[Service]
Type=simple
User=sara
WorkingDirectory=/opt/sara-app
Environment="PATH=/opt/sara-app/venv/bin"
ExecStart=/opt/sara-app/venv/bin/python bot_polling.py
Restart=always
RestartSec=10

# Mesmas variáveis de ambiente do sara-api
Environment="GROQ_API_KEY=<sua-chave>"
Environment="DATABASE_URL=postgresql://<user>:<pass>@localhost:5432/<db>"
Environment="TELEGRAM_BOT_TOKEN=<token>"
# ... (demais variáveis)

[Install]
WantedBy=multi-user.target
```

> **Atenção:** Não rodar polling e webhook simultaneamente — o Telegram aceita apenas um por vez. Usar polling apenas se desregistrar o webhook primeiro.

### 11. Configurar Backups

Criar script `/opt/sara/backup.sh`:
```bash
#!/bin/bash
BACKUP_DIR="/opt/sara/backups"
RETENTION_DAYS=7
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Dump do PostgreSQL
docker exec sara-post pg_dump -U $DB_USER -d $DB_NAME | gzip > $BACKUP_DIR/sara_$DATE.sql.gz

# Deletar backups antigos
find $BACKUP_DIR -name "sara_*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "Backup concluído: sara_$DATE.sql.gz"
```

```bash
chmod +x /opt/sara/backup.sh

# Agendar via cron (diário às 03:00)
crontab -e
# Adicionar: 0 3 * * * /opt/sara/backup.sh >> /var/log/sara-backup.log 2>&1
```

### 12. Monitoramento Básico

Criar script `/opt/sara/healthcheck.sh`:
```bash
#!/bin/bash
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" https://sua-url/health)

if [ "$RESPONSE" != "200" ]; then
    # Enviar alerta via Telegram
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -H "Content-Type: application/json" \
      -d "{\"chat_id\": 6751112445, \"text\": \"⚠️ Sara API fora do ar! HTTP $RESPONSE\"}"
fi
```

```bash
chmod +x /opt/sara/healthcheck.sh

# Verificar a cada 5 minutos
crontab -e
# Adicionar: */5 * * * * /opt/sara/healthcheck.sh
```

---

## Variáveis de Ambiente (produção)

| Variável | Valor | Observação |
|---|---|---|
| `GROQ_API_KEY` | Sua chave | Nunca commitar |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Pode manter o default |
| `DATABASE_URL` | `postgresql://user:pass@localhost:5432/sara` | Localhost, porta 5432 |
| `TELEGRAM_BOT_TOKEN` | Token do BotFather | Nunca commitar |
| `WEBHOOK_URL` | `https://seu-dominio` | URL HTTPS real |
| `BRIEFING_HORA` | `08:00` | Ajustável |
| `TIMEZONE` | `America/Sao_Paulo` | Fuso do usuário |
| `ENVIRONMENT` | `production` | Usado para condicionais |
| `LOG_LEVEL` | `INFO` | `DEBUG` só em dev |

---

## Fluxo de Deploy (Resumo)

```
1. Provisionar VPS → Ubuntu 22.04/24.04
2. Instalar deps → Docker, Python, Nginx, Certbot
3. Configurar domínio + SSL
4. Subir PostgreSQL via Docker Compose
5. Clonar app + instalar deps + rodar migrações
6. Configurar systemd + Nginx
7. Iniciar API + verificar /health
8. Registrar webhook HTTPS no Telegram
9. Testar: enviar mensagem pelo bot
10. Configurar backups + healthcheck
```

---

## Comandos Úteis no Servidor

```bash
# Ver logs da API em tempo real
journalctl -u sara-api -f --no-pager

# Reiniciar API
systemctl restart sara-api

# Ver status do PostgreSQL
docker compose -f /opt/sara/docker-compose.yaml ps

# Acessar banco diretamente
docker exec -it sara-postgres psql -U <user> -d <db>

# Verificar webhook
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo" | python3 -m json.tool

# Backup manual
/opt/sara/backup.sh
```

---

## Critérios de Aceitação da Fase

A Fase 3 está concluída quando:

- [ ] VPS provisionada com Ubuntu, Docker, Nginx e SSL configurados
- [ ] PostgreSQL rodando via Docker Compose com volume persistente
- [ ] Aplicação rodando via systemd com restart automático
- [ ] Webhook HTTPS registrado no Telegram
- [ ] Mensagem enviada pelo Telegram é respondida corretamente
- [ ] Lembretes são disparados no horário correto
- [ ] Briefing diário enviado no horário configurado
- [ ] Backup diário do banco configurado e funcionando
- [ ] Healthcheck enviando alertas por Telegram
- [ ] `cli.py` continua funcionando localmente (conectando ao banco remoto via SSH tunnel ou replicando `.env`)
- [ ] Nenhuma credencial hardcoded ou versionada

---

## Próximos Passos (pós-deploy)

Uma vez que o bot esteja rodando 24/7:

1. **Monitorar uso real** — ver como o bot se comporta com mensagens do dia a dia
2. **Ajustar prompts e ferramentas** — refinamentos baseados em uso real
3. **Fase 4: Dashboard** — interface web para gerenciar tarefas e preferências
4. **Fase 5: RAG** — memória semântica de longo prazo com pgvector

---

## Observações Técnicas

- O systemd `Restart=always` garante que a API volta sozinha após crash — não precisa de supervisor extra.
- O PostgreSQL via Docker Compose isola o banco e facilita upgrades futuros (ex: adicionar pgvector).
- Nginx como reverse proxy permite servir o frontend da Fase 4 na mesma máquina sem conflitos de porta.
- Backups locais são o mínimo — considerar S3/Backblaze para redundância geográfica no futuro.
- O webhook do Telegram tolera falhas temporárias — se a API cair e voltar, o Telegram reenvia updates pendentes.
- Para atualizar a aplicação: `git pull`, `pip install -r requirements.txt`, `alembic upgrade head`, `systemctl restart sara-api`.