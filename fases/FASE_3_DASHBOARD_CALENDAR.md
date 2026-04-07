# Sara — Fase 3: Dashboard Web + Google Calendar

> **Objetivo:** Criar uma interface web onde o usuário pode visualizar tarefas e lembretes, editar registros, configurar preferências e conectar o Google Calendar para sincronização bidirecional.

---

## Visão Geral

A Fase 3 adiciona visibilidade ao sistema. O Telegram continua sendo o canal principal de interação, mas o dashboard oferece uma visão consolidada de tudo que foi registrado — com edição direta, filtros e configurações que seriam difíceis de fazer por mensagem de texto.

A integração com Google Calendar torna a Sara parte do ecossistema existente do usuário — eventos criados pela Sara aparecem na agenda, e eventos da agenda podem ser importados pela Sara.

---

## Stack Tecnológico

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Frontend | React + Next.js | Build moderno, ecossistema maduro |
| Estilização | Tailwind CSS | Utilitário, consistente, rápido de prototipar |
| Requisições | Axios | Simples, interceptors para autenticação |
| Estado | Zustand ou Context API | Leve, sem overhead do Redux |
| Autenticação | JWT + Google OAuth2 | Padrão da indústria, integra com Google Calendar |
| Calendário | Google Calendar API v3 | Sincronização bidirecional |
| Backend | FastAPI (extensão do main.py) | Novos endpoints REST no mesmo servidor |

---

## Requisitos Funcionais

### RF-01 — Autenticação via Google
O usuário deve conseguir fazer login com sua conta Google e ter acesso ao dashboard.

**Critério de aceitação:**
- Botão "Entrar com Google" na tela de login
- Após autenticação, o sistema armazena o `google_id` vinculado ao `chat_id` do Telegram
- Token JWT é gerado e armazenado no browser para autenticar as próximas requisições
- Sessão expira após 24 horas (configurável)

### RF-02 — Visualização de tarefas
O dashboard deve exibir todas as tarefas do usuário com filtros e ordenação.

**Critério de aceitação:**
- Lista de tarefas com título, data de vencimento, prioridade e status
- Filtros por status (pendente, concluída, cancelada) e por data
- Ordenação por data de vencimento ou prioridade
- Indicação visual de tarefas atrasadas (vencidas e ainda pendentes)
- Paginação ou scroll infinito para listas longas

### RF-03 — Edição de tarefas
O usuário deve conseguir editar tarefas diretamente no dashboard.

**Critério de aceitação:**
- Clicar em uma tarefa abre modal ou painel lateral com campos editáveis
- Campos editáveis: título, data de vencimento, prioridade e status
- Alterações são persistidas no banco imediatamente
- Interface otimista — atualiza a UI antes de confirmar com o servidor

### RF-04 — Visualização de lembretes
O dashboard deve exibir todos os lembretes com status de envio.

**Critério de aceitação:**
- Lista de lembretes com mensagem, horário e status (pendente/enviado)
- Lembretes enviados exibidos com indicação visual distinta
- Possibilidade de excluir lembretes pendentes

### RF-05 — Configurações de preferências
O usuário deve poder configurar comportamentos da Sara pelo dashboard.

**Critério de aceitação:**
- Configurar horário do briefing diário
- Ativar ou desativar o briefing diário
- Configurar fuso horário
- Preferências são salvas no banco e refletem imediatamente no scheduler

### RF-06 — Sincronização com Google Calendar (exportar)
Tarefas com data de vencimento criadas pela Sara devem poder ser exportadas para o Google Calendar.

**Critério de aceitação:**
- Botão "Sincronizar com Google Calendar" na interface
- Ao sincronizar, cria eventos no Google Calendar para cada tarefa com data
- Eventos criados têm o prefixo "Sara:" no título para identificação
- Sincronização não duplica eventos já exportados anteriormente

### RF-07 — Sincronização com Google Calendar (importar)
Eventos do Google Calendar do usuário podem ser importados como tarefas na Sara.

**Critério de aceitação:**
- Botão "Importar do Google Calendar" com seletor de período
- Eventos importados viram tarefas com status `pending`
- Usuário pode revisar antes de confirmar a importação
- Eventos já importados não são duplicados

---

## Modelo de Dados (novas tabelas)

### Tabela `users`
Armazena dados dos usuários autenticados via Google.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária |
| google_id | VARCHAR(50) | Sim | ID único do Google |
| telegram_chat_id | VARCHAR(20) | Não | Vinculado ao chat_id do Telegram |
| email | VARCHAR(255) | Sim | Email da conta Google |
| name | VARCHAR(255) | Sim | Nome de exibição |
| google_access_token | TEXT | Não | Token de acesso ao Google Calendar |
| google_refresh_token | TEXT | Não | Token de renovação |
| briefing_enabled | BOOLEAN | Sim | Se o briefing está ativo |
| briefing_hora | VARCHAR(5) | Sim | Horário do briefing (HH:MM) |
| timezone | VARCHAR(50) | Sim | Fuso horário |
| created_at | TIMESTAMP | Sim | Data de criação |

### Tabela `calendar_sync`
Controla quais tarefas já foram exportadas para o Google Calendar.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária |
| task_id | UUID FK | Sim | Referência à tarefa |
| google_event_id | VARCHAR(255) | Sim | ID do evento no Google Calendar |
| synced_at | TIMESTAMP | Sim | Data da sincronização |

---

## Estrutura de Diretórios

```
sara-virtual-secretary/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── auth.py               # Endpoints de autenticação Google OAuth2
│   │   ├── tasks.py              # CRUD de tarefas via REST
│   │   ├── reminders.py          # CRUD de lembretes via REST
│   │   ├── preferences.py        # Preferências do usuário
│   │   └── calendar.py           # Endpoints de sincronização com Google Calendar
│   ├── services/
│   │   ├── telegram.py           # Fase 2
│   │   ├── google_calendar.py    # Integração com Google Calendar API
│   │   └── auth.py               # Geração e validação de JWT
│   └── main.py                   # Inclui os novos routers
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── TaskList.jsx
│   │   │   ├── TaskCard.jsx
│   │   │   ├── TaskModal.jsx
│   │   │   ├── ReminderList.jsx
│   │   │   └── PreferencesForm.jsx
│   │   ├── pages/
│   │   │   ├── Login.jsx
│   │   │   ├── Dashboard.jsx
│   │   │   ├── Tasks.jsx
│   │   │   ├── Reminders.jsx
│   │   │   └── Settings.jsx
│   │   ├── services/
│   │   │   └── api.js            # Chamadas HTTP com Axios
│   │   └── App.jsx
│   ├── package.json
│   └── vite.config.js
└── ...
```

---

## Endpoints REST (novos)

### Autenticação
| Método | Rota | Descrição |
|---|---|---|
| GET | /auth/google | Inicia fluxo OAuth2 com Google |
| GET | /auth/google/callback | Callback do Google, gera JWT |
| POST | /auth/logout | Invalida a sessão |

### Tarefas
| Método | Rota | Descrição |
|---|---|---|
| GET | /api/tasks | Lista tarefas com filtros e paginação |
| GET | /api/tasks/{id} | Busca tarefa por ID |
| PUT | /api/tasks/{id} | Atualiza campos de uma tarefa |
| DELETE | /api/tasks/{id} | Cancela uma tarefa |

### Lembretes
| Método | Rota | Descrição |
|---|---|---|
| GET | /api/reminders | Lista lembretes |
| DELETE | /api/reminders/{id} | Remove lembrete pendente |

### Preferências
| Método | Rota | Descrição |
|---|---|---|
| GET | /api/preferences | Retorna preferências do usuário |
| PUT | /api/preferences | Atualiza preferências |

### Google Calendar
| Método | Rota | Descrição |
|---|---|---|
| POST | /api/calendar/export | Exporta tarefas para o Google Calendar |
| POST | /api/calendar/import | Importa eventos do Google Calendar |
| GET | /api/calendar/status | Retorna status da sincronização |

---

## Variáveis de Ambiente (novas)

| Variável | Obrigatório | Descrição |
|---|---|---|
| GOOGLE_CLIENT_ID | Sim | Client ID do Google OAuth2 |
| GOOGLE_CLIENT_SECRET | Sim | Client Secret do Google OAuth2 |
| GOOGLE_REDIRECT_URI | Sim | URL de callback após autenticação |
| JWT_SECRET | Sim | Chave secreta para assinar tokens JWT |
| JWT_EXPIRATION_HOURS | Não | Horas de validade do JWT (default: 24) |
| FRONTEND_URL | Sim | URL do frontend (para CORS e redirects) |

---

## Fluxo de Autenticação

```
Usuário clica em "Entrar com Google"
        ↓
Backend redireciona para Google OAuth2
        ↓
Usuário autoriza o app
        ↓
Google redireciona para /auth/google/callback com code
        ↓
Backend troca code por access_token + refresh_token
        ↓
Backend cria ou atualiza registro na tabela users
        ↓
Backend gera JWT com user_id
        ↓
Frontend armazena JWT e redireciona para dashboard
```

---

## Passo a Passo de Implementação

### 1. Configurar Google Cloud Project
- Criar projeto no Google Cloud Console
- Ativar a Google Calendar API
- Criar credenciais OAuth2 (Web Application)
- Adicionar URLs autorizadas de redirect
- Copiar Client ID e Client Secret para o `.env`

### 2. Autenticação backend
- Instalar `google-auth`, `google-auth-oauthlib`, `python-jose`
- Criar tabela `users` e `calendar_sync` via Alembic
- Implementar endpoints `/auth/google` e `/auth/google/callback`
- Implementar geração e validação de JWT
- Criar middleware de autenticação para proteger os endpoints da API

### 3. Endpoints REST
- Criar routers para tasks, reminders, preferences e calendar
- Proteger todos com o middleware de autenticação
- Implementar filtros e paginação nas listagens

### 4. Serviço Google Calendar
- Implementar `google_calendar.py` com funções de criação e leitura de eventos
- Gerenciar renovação automática do `access_token` usando o `refresh_token`
- Implementar lógica de deduplicação usando a tabela `calendar_sync`

### 5. Frontend
- Criar projeto React com Vite
- Configurar Tailwind CSS
- Implementar tela de login com botão Google
- Implementar dashboard com lista de tarefas e lembretes
- Implementar modais de edição
- Implementar tela de configurações
- Conectar todos os componentes aos endpoints REST via Axios

### 6. Vincular Telegram ao Google
- Na tela de configurações, permitir que o usuário insira seu `chat_id` do Telegram
- Alternativa: implementar comando `/vincular` no bot que gera um código temporário
- Armazenar a vinculação na tabela `users`

### 7. Adaptar scheduler
- Fazer o briefing diário usar as preferências da tabela `users` por usuário
- Horário e ativação vêm do banco, não mais do `.env`

### 8. Validação
- Fazer login com Google e verificar criação do usuário no banco
- Criar tarefa pelo Telegram e verificar que aparece no dashboard
- Editar tarefa pelo dashboard e verificar atualização no banco
- Exportar tarefa para Google Calendar e verificar criação do evento
- Importar evento do Google Calendar e verificar criação da tarefa

---

## Critérios de Aceitação da Fase

A Fase 3 está concluída quando:

- [ ] Login com Google funciona e gera JWT válido
- [ ] Dashboard exibe tarefas e lembretes do usuário autenticado
- [ ] Edições feitas no dashboard são persistidas no banco
- [ ] Tarefa criada pelo Telegram aparece no dashboard
- [ ] Exportação cria eventos no Google Calendar do usuário
- [ ] Importação cria tarefas a partir de eventos do Google Calendar
- [ ] Preferências salvas no dashboard afetam o comportamento do scheduler
- [ ] Todos os endpoints retornam 401 para requisições sem JWT válido
- [ ] Frontend é servido em rota separada do backend

---

## Observações Técnicas

- O `access_token` do Google expira em 1 hora. O `refresh_token` deve ser armazenado de forma segura e usado para renovação automática.
- O JWT deve conter apenas o `user_id` — dados sensíveis não devem estar no token.
- CORS deve ser configurado para aceitar apenas a origem do frontend.
- Em desenvolvimento, o frontend (porta 5173) e o backend (porta 8000) rodam em portas separadas. Em produção, o backend pode servir o build estático do frontend.
- A vinculação Telegram ↔ Google não é obrigatória para o dashboard funcionar — usuários podem usar só o dashboard ou só o Telegram.
