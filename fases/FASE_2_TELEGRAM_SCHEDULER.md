# Sara — Fase 2: Integração Telegram + Scheduler

> **Objetivo:** Migrar a interface do terminal para o Telegram, adicionar um scheduler assíncrono que dispara lembretes automaticamente no horário certo, e tornar o sistema multi-usuário baseado em `chat_id`.

---

## Visão Geral

A Fase 2 transforma a Sara de um projeto local em um assistente real e utilizável. O usuário passa a interagir pelo Telegram da mesma forma que interagiria pelo WhatsApp — enviando mensagens em linguagem natural — e a Sara responde diretamente no chat, além de enviar lembretes proativamente quando o horário chegar.

O `cli.py` não é removido — permanece como ferramenta de debug e desenvolvimento. O Telegram é adicionado como canal principal.

---

## Stack Tecnológico

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Bot Telegram | python-telegram-bot v20+ | Biblioteca oficial, suporte a webhooks e polling |
| API Gateway | FastAPI | Recebe webhooks do Telegram com performance |
| Servidor ASGI | Uvicorn | Servidor assíncrono compatível com FastAPI |
| Scheduler | APScheduler | Disparo assíncrono e persistente de lembretes |
| Tunneling (dev) | ngrok | Expõe localhost para o Telegram durante desenvolvimento |

---

## Requisitos Funcionais

### RF-01 — Bot Telegram funcional
O sistema deve receber e responder mensagens via Telegram.

**Critério de aceitação:**
- Mensagem enviada ao bot pelo usuário chega ao servidor FastAPI via webhook.
- O agente processa a mensagem e a resposta é enviada de volta ao chat do usuário no Telegram.
- O tempo de resposta deve ser inferior a 10 segundos em condições normais.

### RF-02 — Autenticação por chat_id
O `user_id` deve ser derivado automaticamente do `chat_id` do Telegram.

**Critério de aceitação:**
- Cada usuário do Telegram é identificado pelo seu `chat_id` único.
- Tarefas e lembretes de um usuário não são visíveis para outro.
- O `USER_ID` fixo do `.env` é substituído pelo `chat_id` recebido no webhook.

### RF-03 — Scheduler de lembretes
O sistema deve verificar periodicamente os lembretes pendentes e enviá-los no horário correto.

**Critério de aceitação:**
- A cada minuto, o scheduler verifica a tabela `reminders` buscando registros com `sent=false` e `remind_at <= agora`.
- Para cada lembrete encontrado, a mensagem é enviada ao usuário via Telegram.
- Após o envio, o campo `sent` é atualizado para `true`.
- O scheduler sobrevive a reinicializações — o estado persiste no banco.

### RF-04 — Briefing diário automático
O sistema deve enviar um resumo das tarefas do dia no horário configurado pelo usuário.

**Critério de aceitação:**
- Existe um job agendado para rodar diariamente no horário definido em variável de ambiente.
- O briefing lista todas as tarefas pendentes do dia atual organizadas por horário.
- Se não houver tarefas para o dia, o briefing não é enviado (ou envia mensagem de dia livre, conforme preferência do usuário).

### RF-05 — Tratamento de erros de entrega
O sistema deve lidar com falhas no envio de mensagens pelo Telegram.

**Critério de aceitação:**
- Se o envio falhar, o lembrete não é marcado como `sent=true`.
- O scheduler tentará novamente na próxima verificação.
- Erros de entrega são logados com nível WARNING.

### RF-06 — Modo polling para desenvolvimento
O sistema deve funcionar sem necessidade de URL pública durante o desenvolvimento.

**Critério de aceitação:**
- Existe um comando alternativo que inicia o bot em modo polling (sem webhook).
- O modo polling não requer ngrok ou URL pública.
- O modo webhook é usado em produção.

---

## Arquitetura

```
Usuário no Telegram
        ↓
Telegram Servers
        ↓ (webhook POST)
ngrok (desenvolvimento) / URL pública (produção)
        ↓
FastAPI — POST /webhook/telegram
        ↓
Extrai chat_id e texto da mensagem
        ↓
sara_agent.chat(mensagem, user_id=chat_id)
        ↓
Resposta enviada via Telegram Bot API

                    ┌──────────────────────────────┐
                    │         APScheduler           │
                    │  A cada 1 minuto:             │
                    │  SELECT reminders WHERE       │
                    │  sent=false AND               │
                    │  remind_at <= now()           │
                    │         ↓                    │
                    │  Envia via Telegram           │
                    │  UPDATE sent=true             │
                    └──────────────────────────────┘
```

---

## Estrutura de Diretórios

```
sara-virtual-secretary/
├── app/
│   ├── config.py                 # Adicionar TELEGRAM_BOT_TOKEN e BRIEFING_HORA
│   ├── agent/                    # Sem alterações da Fase 1
│   ├── db/                       # Sem alterações
│   ├── models/                   # Sem alterações
│   ├── services/
│   │   ├── __init__.py
│   │   └── telegram.py           # Funções de envio de mensagem via Telegram
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py               # Jobs do APScheduler (lembretes + briefing)
│   └── main.py                   # FastAPI app com rota do webhook
├── bot_polling.py                # Alternativa sem webhook (para desenvolvimento)
├── cli.py                        # Mantido para debug
└── ...
```

---

## Novos Endpoints FastAPI

### POST `/webhook/telegram`
Recebe eventos do Telegram e despacha para o agente.

**Payload recebido (Telegram Update):**
- `message.chat.id` — identificador único do usuário
- `message.text` — texto enviado pelo usuário
- `message.from.first_name` — nome do usuário (para logs)

**Comportamento:**
- Retorna HTTP 200 imediatamente (requisito do Telegram)
- Processamento acontece de forma assíncrona
- Ignora mensagens que não sejam texto (áudio, imagem, etc.)

### GET `/health`
Verifica se a API está respondendo.

**Resposta:** `{"status": "ok", "version": "2.0"}`

---

## Serviço Telegram

O módulo `app/services/telegram.py` encapsula toda comunicação com a API do Telegram:

- `enviar_mensagem(chat_id, texto)` — envia texto simples
- `enviar_mensagem_longa(chat_id, texto)` — divide mensagens maiores que 4096 caracteres
- Todas as funções devem tratar erros e registrar falhas no log

---

## Scheduler — Jobs

### Job: verificar_lembretes
- **Frequência:** a cada 1 minuto
- **Ação:** busca lembretes com `sent=false` e `remind_at <= agora`, envia via Telegram, marca como `sent=true`
- **Tratamento de erro:** não marca como enviado se o envio falhar

### Job: briefing_diario
- **Frequência:** diária, no horário definido em `BRIEFING_HORA`
- **Ação:** para cada usuário com tarefas pendentes no dia, envia resumo organizado por horário
- **Configuração:** horário definido via variável de ambiente, padrão 08:00

---

## Variáveis de Ambiente (novas)

| Variável | Obrigatório | Descrição |
|---|---|---|
| TELEGRAM_BOT_TOKEN | Sim | Token gerado pelo BotFather |
| BRIEFING_HORA | Não | Horário do briefing diário (default: 08:00) |
| WEBHOOK_URL | Sim (produção) | URL pública onde o Telegram enviará os updates |

---

## Configuração do Bot no Telegram

### Criação do bot
1. Abrir o Telegram e buscar por `@BotFather`
2. Enviar `/newbot` e seguir as instruções
3. Guardar o token gerado

### Registro do webhook
Após o servidor estar acessível publicamente:

```
POST https://api.telegram.org/bot{TOKEN}/setWebhook
Body: {"url": "https://sua-url.ngrok-free.app/webhook/telegram"}
```

### Modo polling (desenvolvimento)
Em vez de webhook, o bot fica perguntando ao Telegram se há novas mensagens a cada segundo. Não requer URL pública. Usado apenas durante desenvolvimento local.

---

## Passo a Passo de Implementação

### 1. Criar o bot
- Criar o bot via BotFather e obter o token
- Adicionar `TELEGRAM_BOT_TOKEN` no `.env`
- Instalar `python-telegram-bot` e `apscheduler`

### 2. Serviço de envio
- Criar `app/services/telegram.py` com função de envio usando a API do Telegram
- Tratar o limite de 4096 caracteres por mensagem
- Implementar retry simples em caso de falha temporária

### 3. Endpoint FastAPI
- Criar `app/main.py` com FastAPI
- Implementar `POST /webhook/telegram` que extrai `chat_id` e `text` do payload
- Chamar `sara_agent.chat(mensagem, user_id=str(chat_id))`
- Enviar resposta via serviço Telegram

### 4. Scheduler
- Criar `app/scheduler/jobs.py` com APScheduler
- Implementar job de verificação de lembretes rodando a cada minuto
- Implementar job de briefing diário no horário configurado
- Inicializar o scheduler junto com o FastAPI no startup

### 5. Adaptar o agente
- Modificar `sara_agent.chat` para aceitar `user_id` como parâmetro
- Remover o `USER_ID` hardcoded — usar o parâmetro recebido
- Garantir que histórico e tools filtram por `user_id` corretamente

### 6. Bot em modo polling (alternativa)
- Criar `bot_polling.py` como alternativa ao webhook para desenvolvimento
- Inicializar o scheduler junto com o polling

### 7. Configurar ngrok (desenvolvimento)
- Instalar ngrok e autenticar com token
- Rodar `ngrok http 8000` e copiar a URL gerada
- Registrar a URL como webhook no Telegram
- Iniciar o servidor FastAPI com `uvicorn app.main:app --reload`

### 8. Validação
- Enviar mensagem pelo Telegram e verificar resposta
- Criar um lembrete para daqui a 2 minutos e aguardar o disparo
- Verificar no banco que `sent` foi atualizado para `true`
- Testar com dois usuários diferentes e verificar isolamento de dados

---

## Critérios de Aceitação da Fase

A Fase 2 está concluída quando:

- [ ] Mensagem enviada pelo Telegram chega ao servidor e é processada
- [ ] Sara responde no Telegram com linguagem natural
- [ ] Cada usuário tem suas tarefas isoladas por `chat_id`
- [ ] Lembrete criado é enviado automaticamente no horário correto
- [ ] Campo `sent` é marcado como `true` após envio bem-sucedido
- [ ] Scheduler reinicia junto com a aplicação sem perder lembretes pendentes
- [ ] Briefing diário é enviado no horário configurado
- [ ] `cli.py` continua funcionando para debug
- [ ] Erros de envio são logados e não travam o scheduler

---

## Observações Técnicas

- O Telegram exige resposta HTTP 200 em menos de 60 segundos. Processar a mensagem de forma assíncrona evita timeout.
- O APScheduler deve ser configurado com `jobstore` no PostgreSQL em produção para sobreviver a reinicializações. Em desenvolvimento, o jobstore em memória é suficiente.
- O modo polling e o modo webhook não devem rodar simultaneamente — o Telegram aceita apenas um por vez.
- Mensagens com mais de 4096 caracteres precisam ser divididas — o Telegram rejeita mensagens maiores.
- O `bot_polling.py` não substitui o `main.py` — são duas formas de entrada para o mesmo agente.
