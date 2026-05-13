# Hotfix Reliability Plan

## Objetivo

Corrigir os problemas de confiabilidade operacional da Sara com foco em:

1. tool safety
2. deterministic routing
3. grounding pos-tool
4. state consistency

Este plano esta quebrado em small patches para aplicacao incremental, validacao rapida e rollback simples.

## Regras do hotfix

1. Cada patch deve alterar uma superficie pequena.
2. Nenhum patch destrutivo entra sem teste ou reproducao minima.
3. Antes de melhorar UX, travar riscos de integridade.
4. Resposta da IA nao vale como sucesso; o banco e a fonte da verdade.

## Status geral

- [x] Patch 01 - Bloquear delete livre no caminho geral do LLM
- [x] Patch 02 - Criar gate deterministico para acoes destrutivas
- [x] Patch 03 - Dar precedencia a intents operacionais seguras
- [ ] Patch 04 - Garantir read-after-write nas confirmacoes
- [ ] Patch 05 - Corrigir confirmacao falsa no fluxo de revisao
- [ ] Patch 06 - Fortalecer resolucao de entidade para mutacoes
- [x] Patch 07 - Cobrir regressao critica com testes
- [ ] Patch 08 - Revisar consistencia backlog/categorias
- [ ] Patch 09 - Auditar logs e observabilidade de incidentes
- [x] Patch 10 - Permitir conclusão em massa do backlog sem exigir data
- [x] Patch 11 - Permitir seleção parcial determinística ao concluir backlog

---

## Patch 01 - Bloquear delete livre no caminho geral do LLM

### Prioridade

P0

### Problema

`delete_task` e `delete_all_tasks` estao expostas ao modelo no schema geral. Hoje a protecao e so prompt.

### Mudanca

1. Remover `delete_task` e `delete_all_tasks` do `TOOLS_SCHEMA` geral.
2. Manter implementacoes internas disponiveis apenas para fluxos deterministcos futuros.

### Arquivos alvo

1. `app/agent/tools.py`
2. `app/agent/prompts.py`

### Validacao

1. Pedido comum de adicionar tarefa nao pode mais terminar em delete por tool routing livre.
2. Nenhuma tool destrutiva deve aparecer no schema normal enviado ao LLM.

### Risco

Baixo. Pode reduzir capacidade de delecao conversacional temporariamente, o que e aceitavel.

---

## Patch 02 - Criar gate deterministico para acoes destrutivas

### Prioridade

P0

### Problema

Acoes destrutivas dependem de obediencia do modelo, sem confirmacao vinculada a alvos concretos.

### Mudanca

1. Criar estado explicito de confirmacao de delete.
2. Mostrar preview dos alvos.
3. Vincular confirmacao a IDs ou conjunto congelado de tarefas.
4. Executar delete apenas a partir desse estado.

### Arquivos alvo

1. `app/agent/sara_agent.py`
2. `app/agent/session.py`
3. `app/agent/tools.py`

### Validacao

1. Sem estado de confirmacao, delete nao executa.
2. `sim` fora do contexto nao pode deletar nada.
3. O retorno final deve listar exatamente os itens removidos.

### Risco

Medio. Introduz novo estado e nova transicao.

---

## Patch 03 - Dar precedencia a intents operacionais seguras

### Prioridade

P0

### Problema

Perguntas como "quais minhas tarefas pendentes?" ficam sequestradas por `planning` ou `reviewing_tasks`.

### Mudanca

1. Criar uma camada de preempcao antes da FSM para intents seguras.
2. Cobrir pelo menos:
   - listar tarefas
   - ver hoje
   - ver backlog
   - cancelar fluxo atual
3. Definir se `add task` tambem deve interromper fluxos ativos.

### Arquivos alvo

1. `app/agent/sara_agent.py`

### Validacao

1. Durante review, pedir lista de pendencias deve responder com listagem real.
2. Durante planning, comandos de escape devem funcionar sem depender do LLM.

### Risco

Medio. Pode alterar expectativa atual da FSM, mas melhora confiabilidade.

---

## Patch 04 - Garantir read-after-write nas confirmacoes

### Prioridade

P0

### Problema

A Sara confirma sucesso baseada em intencao, contexto ou texto, nao no estado persistido.

### Mudanca

1. Padronizar retorno estruturado ou readback apos operacoes de escrita.
2. Montar resposta final a partir do resultado persistido.
3. Bloquear confirmacao otimista sem verificacao.

### Arquivos alvo

1. `app/agent/sara_agent.py`
2. `app/agent/tools.py`

### Validacao

1. Se write falhar, resposta nao pode ser "feito".
2. Se write suceder, resposta deve refletir exatamente o que foi salvo/alterado.

### Risco

Medio. Pode exigir ajuste no contrato de retorno das tools.

---

## Patch 05 - Corrigir confirmacao falsa no fluxo de revisao

### Prioridade

P0

### Problema

`_aplicar_revisao` ignora retorno real de `complete_task_by_id` e `reschedule_task`, depois responde com base no contexto em memoria.

### Mudanca

1. Coletar retorno de cada mutacao.
2. Reconsultar estado final das tarefas afetadas.
3. Responder com base no banco, nao no `review_task_status_map`.

### Arquivos alvo

1. `app/agent/sara_agent.py`

### Validacao

1. Tarefa concluida nao pode continuar aparecendo na lista pendente.
2. Tarefa reagendada deve refletir a nova data no retorno e na listagem seguinte.

### Risco

Baixo a medio.

---

## Patch 06 - Fortalecer resolucao de entidade para mutacoes

### Prioridade

P1

### Problema

`complete_task` e `delete_task` usam matching textual frouxo.

### Mudanca

1. Manter texto livre apenas para buscar candidatos.
2. Exigir selecao deterministica quando houver ambiguidade.
3. Migrar mutacoes sensiveis para `task_id`.

### Arquivos alvo

1. `app/agent/tools.py`
2. `app/agent/sara_agent.py`

### Validacao

1. Com duas tarefas parecidas, o sistema nao escolhe arbitrariamente.
2. Operacao ambigua pede desambiguacao curta.

### Risco

Medio. Pode adicionar atrito em alguns casos, mas com ganho de seguranca.

---

## Patch 07 - Cobrir regressao critica com testes

### Prioridade

P0

### Problema

Os cenarios de confiabilidade mais perigosos nao estao protegidos por teste.

### Mudanca

Adicionar testes para:

1. nao deletar sem confirmacao deterministica
2. listagem interrompendo review/planning
3. nao confirmar sucesso quando write falha
4. review aplicando estado real
5. backlog e concluidas nao divergirem em listagem seguinte

### Arquivos alvo

1. `test_deploy.py`
2. `tests/harness/*`

### Validacao

1. Os cenarios acima devem falhar antes do patch e passar depois.

### Risco

Baixo.

---

## Patch 08 - Revisar consistencia backlog/categorias

### Prioridade

P1

### Problema

Ainda nao esta provado se `category` persistida e derivada estao sempre coerentes.

### Mudanca

1. Auditar onde `category` e recalculada, persistida e lida.
2. Decidir se a categoria continua persistida ou passa a ser derivada em leitura.
3. Uniformizar queries de backlog, today, overdue e upcoming.

### Arquivos alvo

1. `app/agent/tools.py`
2. `app/scheduler/jobs.py`
3. migracoes se necessario

### Validacao

1. Tarefas concluidas nao aparecem em backlog.
2. Tarefas sem data aparecem apenas em backlog.
3. Reagendamento atualiza categoria de forma consistente.

### Risco

Medio.

---

## Patch 09 - Auditar logs e observabilidade de incidentes

### Prioridade

P1

### Problema

Sem trilha completa por turno fica dificil fechar RCA rapido.

### Mudanca

1. Garantir log do estado anterior e posterior.
2. Registrar tool chamada, argumentos, resultado e resposta enviada.
3. Facilitar consulta por incidente real.

### Arquivos alvo

1. `app/agent/sara_agent.py`
2. `app/models/tool_call_log.py`
3. scripts utilitarios se necessario

### Validacao

1. Para um incidente, deve ser possivel reconstruir turno, estado, tool e efeito.

### Risco

Baixo.

---

## Patch 10 - Permitir conclusão em massa do backlog sem exigir data

### Prioridade

P1

### Problema

Ao pedir para concluir tarefas em massa no backlog, o fluxo caia na regra de período explícito e pedia data, mesmo quando o alvo já era claramente o backlog.

### Mudanca

1. Tratar `backlog` como alvo explícito de conclusão em massa.
2. Abrir confirmação determinística com preview das tarefas sem data.
3. Concluir apenas tarefas do backlog após confirmação.

### Arquivos alvo

1. `app/agent/sara_agent.py`
2. `app/agent/tools.py`
3. `test_deploy.py`

### Validacao

1. Pedido de concluir backlog não pede data.
2. O preview mostra apenas tarefas sem `due_date`.
3. A confirmação conclui só backlog, preservando tarefas datadas.

### Risco

Baixo.

---

## Patch 11 - Permitir seleção parcial determinística ao concluir backlog

### Prioridade

P1

### Problema

O fluxo do backlog passou a aceitar concluir tudo sem data, mas ainda faltava concluir apenas parte dele com segurança.

### Mudanca

1. Quando o pedido menciona backlog sem "todas", a Sara lista os itens candidatos.
2. O usuário escolhe por número, nome ou "todas".
3. A Sara mostra preview da seleção.
4. A conclusão só acontece após confirmação explícita.

### Arquivos alvo

1. `app/agent/sara_agent.py`
2. `app/agent/tools.py`
3. `test_deploy.py`

### Validacao

1. Pedido parcial do backlog não conclui nada de imediato.
2. A seleção gera preview determinístico.
3. Apenas os itens selecionados são concluídos.

### Risco

Baixo.

---

## Ordem recomendada de execucao

1. Patch 01
2. Patch 07
3. Patch 02
4. Patch 03
5. Patch 04
6. Patch 05
7. Patch 06
8. Patch 08
9. Patch 09

## Criterio de aceite do hotfix P0

1. Nenhuma delecao ocorre fora de fluxo deterministico de confirmacao.
2. Intents de listagem e escape funcionam mesmo com sessao ativa.
3. A Sara nao confirma sucesso sem evidencia persistida.
4. Os casos relatados viram testes de regressao.

## Observacoes

1. Enquanto o Patch 01 nao entrar, o sistema continua com risco alto de integridade.
2. O Patch 07 deve acompanhar os P0 desde o inicio, nao no fim.
3. Depois do P0 estabilizado, faz sentido refinar UX e conversa. Antes disso, nao.
