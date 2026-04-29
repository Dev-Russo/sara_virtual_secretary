from __future__ import annotations

from datetime import datetime


def formatar_data_legivel(target_date: str) -> str:
    return datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def mensagem_abertura_planejamento(target_date: str) -> str:
    return (
        f"Beleza, vamos olhar {formatar_data_legivel(target_date)}. "
        "O que precisa acontecer nesse dia pra ele render?"
    )


def mensagem_pergunta_data_planejamento() -> str:
    return "Quer organizar hoje ou amanhã? Se for outro dia, me fala qual."


def mensagem_cancelamento() -> str:
    return "Fechei por aqui. Se quiser retomar depois, eu sigo de onde parou."


def mensagem_revisao_planejamento(tarefas: list[dict]) -> str:
    if not tarefas:
        return "Me fala o que rolou que eu já separo o que foi feito e o que ficou pendente."
    itens = "\n".join(f"• {t['title']}" for t in tarefas)
    return (
        "Separei o que ficou pra hoje:\n\n"
        f"{itens}\n\n"
        "Me fala o que rolou que eu organizo aqui. Se preferir, pode usar os botões."
    )


def mensagem_revisao_check(tarefas: list[dict]) -> str:
    if not tarefas:
        return "Hoje não achei tarefa aberta pra revisar."
    itens = "\n".join(f"• {t['title']}" for t in tarefas)
    return (
        "Peguei suas tarefas de hoje:\n\n"
        f"{itens}\n\n"
        "Me fala o que você conseguiu fazer que eu já marco tudo aqui."
    )


def mensagem_revisao_sem_match() -> str:
    return (
        "Não consegui bater isso com as tarefas ainda. Me fala mais direto tipo: 'fiz treino, não estudei'."
    )


def mensagem_briefing(tarefas: list[str]) -> str:
    if not tarefas:
        return "Bom dia. Hoje tá mais livre. Se quiser, me chama que eu te ajudo a organizar."
    itens = "\n".join(f"• {t}" for t in tarefas)
    return f"Bom dia. Hoje você tem isso aqui:\n\n{itens}\n\nSe mudar alguma coisa, me fala."


def mensagem_revisao_aplicada(done_titles: list[str], pending_titles: list[str], moved_to: str | None) -> str:
    partes: list[str] = []
    if done_titles:
        partes.append("Marquei como feitas: " + ", ".join(done_titles) + ".")
    if pending_titles and moved_to:
        partes.append(
            "Joguei pra "
            f"{formatar_data_legivel(moved_to)}: "
            + ", ".join(pending_titles)
            + "."
        )
    elif pending_titles:
        partes.append("Deixei pendente por enquanto: " + ", ".join(pending_titles) + ".")
    if not partes:
        return "Fechei a revisão por aqui."
    return " ".join(partes)


def mensagem_confirmacao_revisao(done_titles: list[str], pending_titles: list[str], pending_action: str, pending_date: str | None) -> str:
    partes: list[str] = []
    if done_titles:
        partes.append("feitas: " + ", ".join(done_titles))
    if pending_titles:
        if pending_action == "move" and pending_date:
            partes.append(
                "pendentes pra mover pra "
                f"{formatar_data_legivel(pending_date)}: "
                + ", ".join(pending_titles)
            )
        else:
            partes.append("pendentes: " + ", ".join(pending_titles))
    if not partes:
        return "Não marquei nada ainda. Se quiser, me diz o que rolou hoje."
    return "Então ficou assim: " + " | ".join(partes) + ". Se estiver certo, me manda um ok. Se quiser ajustar algo, me fala."

