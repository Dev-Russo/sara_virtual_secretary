# Plano Geral de Divida Tecnica

## Objetivo

Reduzir a dependencia da IA nas regras criticas do sistema, diminuir bugs laterais e manter a Sara operavel mesmo que a camada conversacional mude bastante no futuro.

Este documento nao e um roadmap por semana. Ele e uma referencia geral do que precisa ser alterado e da ordem logica para atacar cada frente com `small-safe-patch`.

## Principios

- Regra de negocio critica nao pode depender de texto livre do modelo.
- Toda operacao que muda estado deve ter retorno estruturado e verificavel.
- Datas, periodos e estados devem ter contrato unico.
- A IA deve orquestrar intencao e resposta, nao carregar a regra final do sistema.
- Cada melhoria deve preservar a funcionalidade atual e reduzir risco antes de ampliar escopo.

## Trilha 1: Nucleo Deterministico de Operacoes

### Problema

Operacoes criticas ainda dependem demais do fluxo conversacional e de strings retornadas por tools.

### O que precisa mudar

- Criar um nucleo de comandos deterministico para:
  - salvar tarefa
  - concluir tarefa
  - concluir tarefas em lote
  - reagendar
  - deletar
- Cada comando deve ter:
  - entrada clara
  - validacao propria
  - execucao no banco
  - verificacao pos-operacao
  - retorno estruturado

### Resultado esperado

Mesmo sem IA, o sistema continua conseguindo executar os comandos principais com comportamento previsivel.

### Como quebrar em small patches

- Patch 1: padronizar contrato de retorno de uma operacao de escrita
- Patch 2: aplicar o contrato na conclusao individual
- Patch 3: aplicar o contrato na conclusao em lote
- Patch 4: aplicar o contrato em reagendamento e delecao

## Trilha 2: Contratos Compartilhados

### Problema

Estados de sessao, periodos, status e significados de sucesso/erro estao espalhados em strings literais.

### O que precisa mudar

- Centralizar constantes ou enums para:
  - estados da sessao
  - periodos reconhecidos
  - status de operacao
  - codigos de erro conhecidos
- Fazer runtime e testes usarem o mesmo contrato.

### Resultado esperado

Mudancas de estado ou periodo deixam de exigir sincronizacao manual em varios arquivos.

### Como quebrar em small patches

- Patch 1: criar modulo de contratos compartilhados
- Patch 2: migrar estados de sessao
- Patch 3: migrar periodos
- Patch 4: migrar status e erros de operacao

## Trilha 3: Modulo Unico de Datas

### Problema

Parsing e validacao temporal estao duplicados e sujeitos a drift entre agente, scheduler e tools.

### O que precisa mudar

- Extrair um modulo unico para:
  - interpretar `hoje`, `amanha`, `ontem` e datas explicitas
  - distinguir `dia inteiro` de `data com horario`
  - validar vencimento e atraso
  - resolver periodos de listagem
- Fazer todos os pontos criticos passarem pelo mesmo modulo.

### Resultado esperado

Bug corrigido uma vez em data deixa de reaparecer em outro fluxo.

### Como quebrar em small patches

- Patch 1: criar helpers centrais de parse e semantica temporal
- Patch 2: migrar `save_task`
- Patch 3: migrar listagens e filtros por periodo
- Patch 4: migrar scheduler e outros pontos duplicados

## Trilha 4: Desacoplamento do Agente Principal

### Problema

`sara_agent.py` concentra orquestracao, parsing, regras, guardrails, revisao, planejamento e tratamento de inconsistencias no mesmo fluxo.

### O que precisa mudar

- Tirar do agente principal:
  - parsing deterministico
  - execucao de comandos
  - pos-validacao
  - tratamento de inconsistencias operacionais
- Deixar no agente principal:
  - roteamento
  - escolha de fluxo
  - composicao da resposta ao usuario

### Resultado esperado

O arquivo principal passa a ser uma borda de orquestracao, mais simples de manter e de revisar.

### Como quebrar em small patches

- Patch 1: extrair funcoes auxiliares de pos-validacao
- Patch 2: extrair fluxo de conclusao
- Patch 3: extrair fluxo de planejamento
- Patch 4: extrair fluxo de revisao/diagnostico

## Trilha 5: Alinhamento entre Prompt e Contrato Real

### Problema

O prompt descreve capacidades e formatos que ja nao batem exatamente com o comportamento deterministico atual.

### O que precisa mudar

- Revisar prompts sempre que mudar:
  - periodos suportados
  - formato aceito para datas
  - regras de seguranca operacional
- Sempre que possivel, reduzir duplicacao de contrato em texto livre.

### Resultado esperado

Menos drift entre o que o modelo foi instruido a fazer e o que o sistema realmente aceita.

### Como quebrar em small patches

- Patch 1: revisar regras de datas
- Patch 2: revisar regras de operacoes em lote
- Patch 3: revisar regras de confirmacao e diagnostico

## Trilha 6: Testes de Contrato

### Problema

Os testes cobrem bastante coisa, mas ainda estao concentrados demais e dificultam manutencao e regressao localizada.

### O que precisa mudar

- Criar testes menores por dominio:
  - datas
  - conclusao
  - operacoes em lote
  - planejamento
  - diagnostico de inconsistencias
- Cobrir contratos estruturados, nao apenas texto final.

### Resultado esperado

Cada trilha acima passa a ter rede de seguranca propria e mais barata de manter.

### Como quebrar em small patches

- Patch 1: separar helpers comuns
- Patch 2: criar testes de contrato para conclusao
- Patch 3: criar testes de contrato para datas
- Patch 4: quebrar o runner monolitico gradualmente

## Ordem Recomendada de Ataque

1. Trilha 1: nucleo deterministico de operacoes
2. Trilha 2: contratos compartilhados
3. Trilha 3: modulo unico de datas
4. Trilha 6: testes de contrato dos pontos ja estabilizados
5. Trilha 4: desacoplamento do agente principal
6. Trilha 5: alinhamento final de prompt com contrato

## Regras para os proximos small patches

- Cada patch deve atacar um comportamento ou um contrato pequeno.
- Cada patch deve manter o sistema funcionando ao final.
- Cada patch deve incluir validacao minima do comportamento alterado.
- Se um patch exigir refatoracao larga demais, ele deve ser quebrado novamente.
- Nao misturar mudanca estrutural e mudanca de UX na mesma entrega.
- Sempre priorizar contratos deterministicos antes de embelezar respostas.

## Como usar este documento

Quando escolhermos uma trilha:

1. Recortar um item pequeno desta pagina.
2. Criar uma documentacao temporaria de patch queue para aquela trilha.
3. Aplicar um `small-safe-patch` por vez.
4. Validar, commitar e atualizar a patch queue local.
5. Voltar para esta pagina como referencia mestra.

## Criterio de sucesso geral

Consideraremos a divida tecnica principal sob controle quando:

- operacoes criticas nao dependerem mais de parsing de texto livre para sucesso/erro
- datas forem resolvidas por um unico contrato
- estados e periodos deixarem de ficar espalhados em strings soltas
- `sara_agent.py` perder responsabilidade de regra de negocio
- os testes principais protegerem os contratos criticos do sistema
