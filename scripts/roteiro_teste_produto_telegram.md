# Roteiro de Teste no Produto Final

Use este roteiro direto no Telegram com a Sara já rodando em produção. A ideia é validar a Home, a captura rápida, as categorias e os fluxos principais sem depender de CLI.

## Preparação

1. Abra a conversa com a Sara.
2. Se a Home ainda não estiver visível, envie `/home` ou qualquer mensagem curta como `oi`.
3. Confirme que o teclado fixo aparece com:
   - `Hoje`
   - `Planejar`
   - `Revisar dia`
   - `Backlog`
   - `Adicionar tarefa`
   - `Lembretes`

## 1. Captura rápida para backlog

1. Toque em `Adicionar tarefa`.
2. Esperado:
   - a Sara responde algo como “manda a tarefa do jeito que vier”
3. Envie:
   - `estudar Docker`
4. Esperado:
   - a Sara confirma que anotou em backlog
5. Toque em `Adicionar tarefa` de novo.
6. Envie:
   - `revisar arquitetura da Sara`
7. Esperado:
   - a Sara confirma backlog de novo

## 2. Backlog

1. Toque em `Backlog`.
2. Esperado:
   - aparecem pelo menos:
     - `estudar Docker`
     - `revisar arquitetura da Sara`
   - a seção deve estar claramente como backlog

## 3. Tarefa com data

1. Envie naturalmente:
   - `amanhã preciso treinar`
2. Esperado:
   - a Sara salva a tarefa sem exigir horário
   - a tarefa não deve cair no backlog
3. Toque em `Hoje`.
4. Se a tarefa ficou para amanhã, esperado:
   - ela aparece em `Próximas`

## 4. Hoje, atrasadas e próximas

1. Envie:
   - `preciso pagar boleto hoje`
2. Esperado:
   - a tarefa entra como `Hoje`
3. Toque em `Hoje`.
4. Esperado:
   - a resposta vem agrupada por categorias
   - deve existir pelo menos:
     - `Hoje`
     - `Backlog`
   - se houver tarefa antiga não concluída, ela deve aparecer em `Atrasadas`

## 5. Lembretes

1. Toque em `Lembretes`.
2. Se não houver lembrete:
   - esperado: a Sara informa que não existe lembrete pendente
3. Envie:
   - `me lembra amanhã às 09:00 de tomar água`
4. Toque em `Lembretes` de novo.
5. Esperado:
   - o lembrete aparece listado com data e hora

## 6. Planejamento

1. Toque em `Planejar`.
2. Se houver tarefas de hoje:
   - esperado: a Sara pode abrir revisão antes do planejamento
3. Feche a revisão se aparecer.
4. Quando entrar no planejamento, envie:
   - `quero trabalhar, revisar PRs e treinar`
5. Confirme o plano até ele ser salvo.
6. Esperado:
   - a Sara não entra em loop de confirmação
   - o plano fecha e as tarefas ficam salvas

## 7. Revisão do dia

1. Toque em `Revisar dia`.
2. Se houver tarefas de hoje:
   - esperado: a Sara mostra revisão por texto e/ou botões
3. Responda algo como:
   - `fiz treinar, não revisei PRs`
4. Confirme com `ok`.
5. Esperado:
   - uma tarefa fica concluída
   - outra continua pendente ou é movida conforme a resposta da Sara

## 8. Conferência final das categorias

1. Toque em `Hoje`.
2. Esperado:
   - tarefas concluídas não aparecem mais nas listas pendentes
   - tarefas sem data continuam em `Backlog`
   - tarefas futuras aparecem em `Próximas`
   - tarefas antigas não concluídas aparecem em `Atrasadas`

## 9. Briefing diário

1. Aguarde o briefing normal ou force via ambiente de admin se você tiver esse caminho.
2. Esperado:
   - o briefing vem organizado por blocos de categoria
   - se houver atrasadas, a Sara dá uma sugestão curta do que fazer com elas

## 10. Teste de robustez da navegação

1. No meio de um fluxo, toque em outro botão da Home, por exemplo:
   - durante backlog, toque em `Hoje`
   - durante hoje, toque em `Lembretes`
2. Esperado:
   - a Sara troca de contexto sem travar
   - o teclado da Home continua disponível

## Resultado esperado geral

- Você consegue usar a Sara sem decorar comandos.
- Tarefa sem data vira backlog sem atrito.
- O estado do produto fica visível em `Hoje`, `Backlog`, `Atrasadas` e `Próximas`.
- `Planejar`, `Revisar dia` e `Lembretes` ficam acessíveis por navegação.
