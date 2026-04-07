# Sara — Fase 5: RAG, Memória Longa e Deploy

> **Objetivo:** Expandir a inteligência da Sara com memória semântica de longo prazo via RAG (Retrieval-Augmented Generation), e realizar o deploy completo do sistema em um servidor na nuvem para que pare de depender da máquina local.

---

## Visão Geral

A Fase 5 resolve dois problemas fundamentais do sistema atual:

**Problema 1 — Memória limitada:** O histórico de conversa carrega apenas as últimas N mensagens. Referências a eventos antigos ("aquele compromisso que agendei mês passado", "o médico que falei em março") não são encontrados.

> **Nota:** O deploy em produção foi antecipado para a Fase 3. Esta fase assume que o sistema já está rodando em um VPS com PostgreSQL + pgvector disponível.

A solução para o Problema 1 é um sistema de busca semântica — embeddings das conversas armazenados em banco vetorial, recuperados por similaridade quando relevantes.

---

## Parte 1: RAG e Memória Semântica

### O que é RAG neste contexto

RAG (Retrieval-Augmented Generation) aqui significa: antes de cada mensagem, buscar no histórico completo as conversas mais semanticamente relevantes para o contexto atual, e injetá-las junto com as últimas N mensagens.

```
Usuário: "E aquela reunião com o Rafael?"
        ↓
Sistema gera embedding da pergunta
        ↓
Busca no banco vetorial as conversas mais similares
        ↓
Recupera: "Reunião com Rafael marcada para 15/03 às 14h"
        ↓
Injeta no contexto junto com o histórico recente
        ↓
Agente responde com informação correta
```

Sem RAG, a Sara responderia "não tenho informação sobre isso" se a reunião foi mencionada há mais de N mensagens atrás.

---

### Stack Tecnológico (RAG)

| Componente | Tecnologia | Justificativa |
|---|---|---|
| Embeddings | OpenAI text-embedding-3-small ou Groq | Vetores semânticos de alta qualidade |
| Banco vetorial | pgvector (extensão do PostgreSQL) | Sem novo serviço — usa o banco já existente |
| Busca semântica | Similaridade cosseno via pgvector | Integrado ao SQLAlchemy |

### Por que pgvector e não Pinecone/Weaviate

O projeto já usa PostgreSQL. O pgvector adiciona suporte a vetores como uma extensão — sem novo container, sem nova conta, sem novo custo. Para o volume de dados de um usuário pessoal, é mais do que suficiente.

---

### Requisitos Funcionais (RAG)

#### RF-01 — Indexação automática do histórico
Cada mensagem salva no histórico deve gerar automaticamente um embedding e ser armazenada no banco vetorial.

**Critério de aceitação:**
- Ao salvar uma mensagem em `conversation_history`, um embedding é gerado e armazenado
- O processo de geração de embedding não bloqueia a resposta ao usuário (assíncrono)
- Embeddings já existentes não são regerados

#### RF-02 — Recuperação semântica por contexto
Antes de cada chamada ao agente, o sistema recupera as mensagens históricas mais relevantes para a mensagem atual.

**Critério de aceitação:**
- O sistema gera embedding da mensagem atual do usuário
- Busca as K mensagens mais similares no banco vetorial (K configurável)
- Mensagens recuperadas são injetadas no contexto antes do histórico recente
- Mensagens já presentes no histórico recente não são duplicadas no contexto

#### RF-03 — Contexto de tarefas por similaridade
Quando o usuário menciona uma tarefa de forma vaga, o sistema deve encontrá-la semanticamente.

**Critério de aceitação:**
- "Aquela tarefa do médico" encontra a tarefa "Consulta médica com Dr. João"
- "O compromisso da semana passada" recupera o contexto relevante do histórico
- A busca funciona mesmo com palavras diferentes das usadas originalmente

---

### Modelo de Dados (novas tabelas)

#### Tabela `message_embeddings`
Armazena os vetores semânticos das mensagens do histórico.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária |
| conversation_id | UUID FK | Sim | Referência à mensagem em conversation_history |
| user_id | VARCHAR(20) | Sim | Identificador do usuário |
| embedding | VECTOR(1536) | Sim | Vetor semântico da mensagem |
| created_at | TIMESTAMP | Sim | Data de criação |

O tipo `VECTOR(1536)` requer a extensão `pgvector` no PostgreSQL.

---

### Fluxo com RAG

```
Mensagem do usuário
        ↓
Gerar embedding da mensagem atual
        ↓
Buscar K mensagens similares em message_embeddings
        ↓
Montar contexto:
  [system_prompt]
  [mensagens semanticamente relevantes]    ← NOVO
  [últimas N mensagens do histórico]
  [mensagem atual]
        ↓
Chamada ao Groq
        ↓
Resposta ao usuário
        ↓
Salvar mensagem + gerar embedding de forma assíncrona
```

---

### Passo a Passo de Implementação (RAG)

#### 1. Ativar pgvector
- Adicionar a extensão pgvector ao PostgreSQL via migration do Alembic
- Verificar suporte no container PostgreSQL (versão 16 suporta nativamente)

#### 2. Modelo de embeddings
- Criar tabela `message_embeddings` via Alembic
- Instalar cliente de embeddings (openai ou alternativa gratuita)
- Implementar função `gerar_embedding(texto)` que retorna vetor de 1536 dimensões

#### 3. Serviço de busca semântica
- Criar `app/services/rag.py`
- Implementar `indexar_mensagem(conversation_id, texto, user_id)` — gera e salva embedding
- Implementar `buscar_contexto(query, user_id, k=5)` — retorna K mensagens similares
- Implementar `contexto_nao_duplicado(recuperadas, historico_recente)` — evita repetição

#### 4. Integrar ao agente
- Modificar `sara_agent.py` para chamar `buscar_contexto` antes de montar o prompt
- Adicionar mensagens recuperadas entre o system prompt e o histórico recente
- Modificar `salvar_historico` para chamar `indexar_mensagem` de forma assíncrona

#### 5. Indexar histórico existente
- Criar script de migração para gerar embeddings das mensagens já existentes
- Rodar uma única vez após a implementação

#### 6. Validação
- Criar tarefa: "Reunião com Rafael na sexta"
- Aguardar vários dias e criar muitas outras mensagens
- Perguntar: "Quando é a reunião com o Rafael?"
- Verificar que a Sara responde corretamente mesmo com o histórico longo

---

## Parte 2: Deploy em Produção

### Arquitetura de Deploy

```
Internet
    ↓
Cloudflare (DNS + SSL)
    ↓
VPS (Ubuntu 22.04)
    ├── Nginx (reverse proxy)
    │   ├── /api → FastAPI (porta 8000)
    │   └── / → Frontend React (arquivos estáticos)
    ├── Docker Compose
    │   ├── PostgreSQL + pgvector
    │   └── Redis
    └── Systemd
        ├── uvicorn (FastAPI)
        └── bot_polling.py (fallback se webhook falhar)
```

### Opções de VPS

| Provedor | Plano recomendado | Custo aproximado | Observação |
|---|---|---|---|
| DigitalOcean | Droplet 2GB RAM | ~$12/mês | Interface simples, boa documentação |
| Hetzner | CX22 (2 vCPU, 4GB) | ~€4/mês | Melhor custo-benefício da Europa |
| Contabo | VPS S | ~€5/mês | Muito RAM por pouco dinheiro |
| Oracle Cloud | Always Free tier | Grátis | 4 vCPUs e 24GB RAM no plano gratuito |

**Recomendação para portfólio:** Oracle Cloud Free Tier — sem custo, recursos generosos, ideal para projeto pessoal.

---

### Requisitos Funcionais (Deploy)

#### RF-01 — Aplicação sempre disponível
O sistema deve funcionar 24/7 sem depender da máquina local do desenvolvedor.

**Critério de aceitação:**
- Mensagens enviadas às 3h da manhã são respondidas normalmente
- Lembretes são disparados no horário correto independente do estado da máquina local
- Em caso de crash da aplicação, o processo é reiniciado automaticamente

#### RF-02 — SSL/HTTPS obrigatório
Toda comunicação deve ser criptografada.

**Critério de aceitação:**
- Frontend e API servidos exclusivamente via HTTPS
- Certificado SSL válido e renovado automaticamente
- Webhook do Telegram configurado com URL HTTPS

#### RF-03 — Variáveis de ambiente seguras
Nenhuma credencial deve estar no código ou em arquivos versionados.

**Critério de aceitação:**
- Todas as credenciais configuradas via variáveis de ambiente do servidor
- Arquivo `.env` não existe no servidor — variáveis são injetadas pelo sistema
- Secrets do banco, Telegram e Google nunca aparecem em logs

#### RF-04 — Logs e monitoramento básico
O sistema deve ter observabilidade mínima para identificar problemas.

**Critério de aceitação:**
- Logs estruturados com nível (INFO, WARNING, ERROR)
- Logs acessíveis via `journalctl` ou arquivo em `/var/log/sara/`
- Alertas por Telegram quando a aplicação cai e reinicia

#### RF-05 — Backup do banco de dados
Os dados do usuário devem ter backup automático.

**Critério de aceitação:**
- Backup diário do PostgreSQL em arquivo comprimido
- Backups armazenados por pelo menos 7 dias
- Processo de restore documentado e testado

---

### Passo a Passo de Implementação (Deploy)

#### 1. Provisionar VPS
- Criar VPS com Ubuntu 22.04 LTS no provedor escolhido
- Configurar acesso SSH com chave (desativar login por senha)
- Criar usuário não-root com sudo
- Configurar firewall (UFW): liberar portas 22, 80 e 443

#### 2. Instalar dependências no servidor
- Docker e Docker Compose
- Nginx
- Certbot (Let's Encrypt)
- Python 3.12 e pip
- Node.js (para build do frontend)

#### 3. Configurar domínio
- Registrar domínio ou usar subdomínio gratuito (ex: DuckDNS)
- Apontar DNS para o IP do VPS
- Gerar certificado SSL com Certbot

#### 4. Configurar Nginx
- Criar virtual host para o domínio
- Configurar proxy reverso para o FastAPI na porta 8000
- Servir os arquivos estáticos do frontend
- Forçar redirect HTTP → HTTPS

#### 5. Deploy da aplicação
- Clonar repositório no servidor
- Configurar variáveis de ambiente (não usar arquivo `.env` — usar `export` ou systemd)
- Subir PostgreSQL e Redis com Docker Compose
- Rodar migrações do Alembic
- Build do frontend React (`npm run build`)
- Iniciar FastAPI com uvicorn gerenciado pelo systemd

#### 6. Configurar systemd
- Criar unit file para o FastAPI (`sara-api.service`)
- Criar unit file para o bot em modo polling como fallback (`sara-bot.service`)
- Configurar restart automático em caso de falha
- Habilitar início automático no boot

#### 7. Configurar webhook do Telegram
- Registrar a URL HTTPS do servidor como webhook
- Verificar que o Telegram consegue entregar mensagens

#### 8. Configurar backups
- Criar script de backup do PostgreSQL com `pg_dump`
- Agendar via cron para rodar diariamente às 3h
- Configurar retenção de 7 dias (deletar backups antigos automaticamente)
- Opcionalmente, enviar backup para armazenamento externo (S3, Backblaze)

#### 9. Monitoramento básico
- Criar script que monitora se a API está respondendo em `/health`
- Configurar cron para verificar a cada 5 minutos
- Enviar alerta via Telegram se a API não responder

#### 10. Validação
- Enviar mensagem pelo Telegram e verificar resposta
- Verificar logs no servidor
- Desligar a máquina local e confirmar que o sistema continua funcionando
- Verificar que backups estão sendo gerados

---

## Critérios de Aceitação da Fase

### RAG
- [ ] Pergunta sobre conversa de há mais de N mensagens é respondida corretamente
- [ ] Embeddings são gerados de forma assíncrona sem atrasar respostas
- [ ] Mensagens recuperadas semanticamente não são duplicadas no contexto
- [ ] Busca por tarefa com palavras diferentes encontra o registro correto

### Deploy
- [ ] Sistema funciona 24/7 sem a máquina local ligada
- [ ] HTTPS ativo com certificado válido
- [ ] Processo reinicia automaticamente após crash
- [ ] Backup diário do banco funcionando e restaurável
- [ ] Logs acessíveis no servidor
- [ ] Alerta enviado quando a aplicação cai

---

## Variáveis de Ambiente (novas)

| Variável | Obrigatório | Descrição |
|---|---|---|
| EMBEDDING_API_KEY | Sim | Chave para geração de embeddings |
| EMBEDDING_MODEL | Não | Modelo de embeddings (default: text-embedding-3-small) |
| RAG_K | Não | Número de mensagens recuperadas por busca (default: 5) |
| RAG_ENABLED | Não | Ativar ou desativar RAG (default: true) |
| ENVIRONMENT | Sim | development ou production |
| LOG_LEVEL | Não | Nível de log (default: INFO em produção) |

---

## Observações Técnicas

- O pgvector suporta índices HNSW e IVFFlat para busca aproximada eficiente. Para volumes pequenos (um usuário pessoal), busca exata com índice simples é suficiente.
- Embeddings de 1536 dimensões (OpenAI) ocupam ~6KB por mensagem. Com 10.000 mensagens, são ~60MB — completamente gerenciável no PostgreSQL.
- O modelo `text-embedding-3-small` da OpenAI tem custo muito baixo (~$0.02 por milhão de tokens). Para uso pessoal, o custo mensal seria de centavos.
- Alternativa gratuita: usar a API do Groq com modelos de embedding open-source, ou `sentence-transformers` localmente.
- Em produção, usar `gunicorn` com múltiplos workers do `uvicorn` para melhor aproveitamento de CPU.
- O certificado Let's Encrypt renova automaticamente via Certbot — não requer intervenção manual.
- Nunca commitar o arquivo `.env` com credenciais de produção — usar variáveis de ambiente do sistema ou um gerenciador de secrets.