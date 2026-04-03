# Sara — Fase 1: Core CLI

> **Objetivo:** Ter um agente de IA conversacional funcionando localmente via terminal, capaz de interpretar mensagens em linguagem natural, salvar tarefas e lembretes no PostgreSQL e manter histórico de conversa entre sessões.

---

## Visão Geral

A Fase 1 estabelece o núcleo do projeto. Toda a inteligência da Sara — o agente, as ferramentas, a memória — é construída aqui. As fases seguintes apenas adicionam canais e interfaces por cima dessa base.

O critério de sucesso é simples: você digita uma mensagem no terminal e a Sara responde de forma natural, salva as informações no banco e lembra do que foi dito anteriormente.

---

## Stack Tecnológico

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema rico para IA, tipagem moderna |
| Banco de dados | PostgreSQL 16 | Persistência confiável, suporte a UUID nativo |
| ORM | SQLAlchemy + Alembic | Modelos tipados e migrações versionadas |
| LLM | Groq (llama-3.3-70b-versatile) | Gratuito, rápido, suporte a tool calling |
| Containerização | Docker + Docker Compose | Banco isolado, reproduzível em qualquer máquina |
| Gerenciamento de env | python-dotenv | Separação de configuração e código |

---

## Requisitos Funcionais

### RF-01 — Interpretação de linguagem natural
O sistema deve receber mensagens em texto livre e identificar automaticamente a intenção do usuário sem que ele precise usar comandos específicos.

**Critério de aceitação:**
- Mensagens como "preciso fazer X amanhã", "me lembra às 15h de Y" e "o que tenho pra hoje?" devem ser interpretadas corretamente sem instrução adicional do usuário.
- O agente deve pedir esclarecimento quando a mensagem for ambígua (ex: "preciso ligar pro médico" sem data).

### RF-02 — Gerenciamento de tarefas
O sistema deve permitir criar, listar e concluir tarefas com suporte a data de vencimento e prioridade.

**Critério de aceitação:**
- Tarefa criada deve ser persistida no banco com os campos: título, data de vencimento (opcional), prioridade e status.
- Listagem deve retornar tarefas pendentes ordenadas por data.
- Conclusão deve atualizar o status no banco sem deletar o registro.

### RF-03 — Gerenciamento de lembretes
O sistema deve permitir criar lembretes com data e hora específicas.

**Critério de aceitação:**
- Lembrete criado deve ser persistido com mensagem e horário de disparo.
- Campo `sent` deve iniciar como `false` para controle futuro do scheduler.

### RF-04 — Histórico de conversa persistente
O sistema deve manter o contexto da conversa entre sessões distintas.

**Critério de aceitação:**
- Ao reiniciar o CLI, o agente deve ter acesso às últimas N mensagens anteriores.
- Referências a mensagens passadas ("aquela tarefa que você salvou", "o que falamos antes") devem ser compreendidas corretamente.
- O limite de mensagens carregadas deve ser configurável via variável de ambiente.

### RF-05 — Confirmação de ações
O sistema deve sempre confirmar ao usuário o que foi registrado.

**Critério de aceitação:**
- Após salvar uma tarefa ou lembrete, a Sara deve confirmar com linguagem natural o que foi feito.
- A confirmação deve incluir os detalhes relevantes (título, data, prioridade).

---

## Modelo de Dados

### Tabela `tasks`
Armazena todas as tarefas criadas pelo usuário.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária gerada automaticamente |
| user_id | VARCHAR(20) | Sim | Identificador do usuário |
| title | TEXT | Sim | Descrição da tarefa |
| due_date | TIMESTAMP | Não | Data e hora de vencimento |
| priority | VARCHAR(10) | Não | low, medium ou high |
| status | VARCHAR(15) | Sim | pending, done ou cancelled |
| created_at | TIMESTAMP | Sim | Gerado automaticamente |
| updated_at | TIMESTAMP | Sim | Atualizado automaticamente |

### Tabela `reminders`
Armazena lembretes agendados para disparo futuro.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária |
| task_id | UUID FK | Não | Tarefa associada (opcional) |
| user_id | VARCHAR(20) | Sim | Identificador do usuário |
| message | TEXT | Sim | Texto do lembrete |
| remind_at | TIMESTAMP | Sim | Quando disparar |
| sent | BOOLEAN | Sim | Se já foi enviado |
| created_at | TIMESTAMP | Sim | Gerado automaticamente |

### Tabela `conversation_history`
Armazena o histórico de mensagens para manutenção de contexto.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| id | UUID | Sim | Chave primária |
| user_id | VARCHAR(20) | Sim | Identificador do usuário |
| role | VARCHAR(15) | Sim | user ou assistant |
| content | TEXT | Sim | Conteúdo da mensagem |
| created_at | TIMESTAMP | Sim | Gerado automaticamente |

---

## Estrutura de Diretórios

```
sara-virtual-secretary/
├── app/
│   ├── __init__.py
│   ├── config.py                 # Todas as variáveis de ambiente centralizadas
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── prompts.py            # System prompt da Sara
│   │   ├── sara_agent.py         # Ciclo principal do agente
│   │   └── tools.py              # Funções de banco + schema JSON para o LLM
│   ├── db/
│   │   ├── __init__.py
│   │   └── database.py           # Engine, SessionLocal, Base, get_db
│   └── models/
│       ├── __init__.py
│       ├── conversation.py
│       ├── reminder.py
│       └── task.py
├── alembic/                      # Migrações versionadas
├── alembic.ini
├── cli.py                        # Interface de linha de comando
├── docker-compose.yaml           # PostgreSQL
├── .env                          # Variáveis locais (não versionar)
├── .env.example                  # Template público das variáveis
├── .gitignore
└── requirements.txt
```

---

## Variáveis de Ambiente

| Variável | Obrigatório | Descrição |
|---|---|---|
| DB_USER | Sim | Usuário do PostgreSQL |
| DB_PASSWORD | Sim | Senha do PostgreSQL |
| DB_NAME | Sim | Nome do banco da aplicação |
| DATABASE_URL | Sim | String de conexão completa |
| GROQ_API_KEY | Sim | Chave da API do Groq |
| GROQ_MODEL | Não | Modelo a usar (default: llama-3.3-70b-versatile) |
| GROQ_TEMPERATURE | Não | Temperatura do modelo (default: 0.3) |
| GROQ_MAX_TOKENS | Não | Máximo de tokens por resposta (default: 1024) |
| USER_ID | Sim | Identificador fixo do usuário no MVP |
| TIMEZONE | Não | Fuso horário (default: America/Sao_Paulo) |
| HISTORICO_LIMITE | Não | Mensagens do histórico por chamada (default: 10) |

---

## Arquitetura do Agente

O agente opera em um ciclo de duas chamadas ao LLM:

```
Mensagem do usuário
        ↓
Monta contexto: [system_prompt + histórico + mensagem atual]
        ↓
Primeira chamada ao Groq (tool_choice=auto)
        ↓
    ┌───────────────────────────────┐
    │ Modelo quer usar uma tool?    │
    └───────────────────────────────┘
         Sim ↓              Não ↓
    Executa tool       Resposta direta
    no banco           ao usuário
         ↓
    Segunda chamada
    ao Groq com resultado
         ↓
    Resposta final ao usuário
         ↓
Salva [user + assistant] no histórico
```

### Tools disponíveis

| Tool | Quando usar | Ação no banco |
|---|---|---|
| save_task | Usuário menciona algo a fazer ou não pode esquecer | INSERT em tasks |
| create_reminder | Usuário pede para ser lembrado em horário específico | INSERT em reminders |
| list_tasks | Usuário pergunta o que tem pra fazer | SELECT em tasks |
| complete_task | Usuário diz que já fez algo | UPDATE tasks SET status=done |

---

## Passo a Passo de Implementação

### 1. Infraestrutura
- Configurar o `docker-compose.yaml` com o serviço PostgreSQL
- Mapear uma porta externa diferente de 5432 se já houver PostgreSQL local
- Subir o container e verificar que está healthy

### 2. Ambiente Python
- Criar e ativar o ambiente virtual
- Instalar as dependências do `requirements.txt`
- Criar o arquivo `.env` a partir do `.env.example` e preencher os valores

### 3. Banco de dados
- Implementar `database.py` com engine, SessionLocal e Base
- Implementar os três models com SQLAlchemy
- Inicializar o Alembic e configurar o `env.py` para ler a `DATABASE_URL` do `.env`
- Gerar e aplicar a migration inicial

### 4. Configuração central
- Implementar `config.py` lendo todas as variáveis via `os.getenv`
- Garantir que nenhum outro arquivo use `os.getenv` diretamente

### 5. System prompt
- Implementar `prompts.py` com a personalidade e regras da Sara
- Injetar data e hora atual e o user_id no prompt dinamicamente

### 6. Tools
- Implementar cada função Python como função pura (sem decorators de framework)
- Cada função deve: abrir sessão, executar no banco, fechar sessão, retornar string
- Criar o `TOOLS_MAP` relacionando nome da tool com a função
- Criar o `TOOLS_SCHEMA` no formato JSON Schema que o Groq entende

### 7. Agente
- Implementar `carregar_historico` buscando as últimas N mensagens do banco
- Implementar `salvar_historico` persistindo user e assistant após cada troca
- Implementar `executar_tool` buscando no TOOLS_MAP e chamando a função
- Implementar a função `chat` com o ciclo de duas chamadas ao Groq

### 8. CLI
- Implementar loop simples com `input()` e `print()`
- Tratar `KeyboardInterrupt` e comando "sair"
- Exibir mensagem de boas-vindas ao iniciar

### 9. Validação
- Testar criação de tarefa com data relativa ("amanhã", "sexta")
- Testar listagem de tarefas pendentes
- Testar conclusão de tarefa
- Testar criação de lembrete com horário
- Reiniciar o CLI e testar se o contexto foi mantido

---

## Critérios de Aceitação da Fase

A Fase 1 está concluída quando:

- [ ] `python cli.py` inicia sem erros
- [ ] Mensagem "preciso fazer X amanhã" cria um registro na tabela `tasks`
- [ ] Mensagem "me lembra às 15h de Y" cria um registro na tabela `reminders`
- [ ] Mensagem "o que tenho pra hoje?" retorna as tarefas do dia
- [ ] Mensagem "já fiz X" marca a tarefa como `done`
- [ ] Ao reiniciar o CLI, a Sara lembra de mensagens anteriores
- [ ] Nenhuma credencial está hardcoded no código
- [ ] Todas as variáveis de ambiente vêm exclusivamente do `config.py`
- [ ] Imports seguem o padrão PEP8 (stdlib → terceiros → projeto)
- [ ] Todos os arquivos têm docstrings e comentários em português

---

## Dependências

```
fastapi
uvicorn
groq
sqlalchemy
alembic
psycopg2-binary
python-dotenv
pytz
```

---

## Observações Técnicas

- O `user_id` é fixo no MVP para simplificação. A partir da Fase 2 virá do identificador do canal de mensagens (chat_id do Telegram).
- O campo `sent` da tabela `reminders` é reservado para o scheduler da Fase 2 — não é usado na Fase 1.
- O histórico carrega apenas mensagens com `role` igual a `user` ou `assistant` — mensagens internas de tool não devem ser salvas para evitar poluição do contexto.
- Datas relativas ("amanhã", "sexta-feira", "semana que vem") devem ser resolvidas pelo LLM com base na data atual injetada no system prompt.
