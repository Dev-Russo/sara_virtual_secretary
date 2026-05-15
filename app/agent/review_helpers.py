from app.agent.copy import mensagem_revisao_aplicada
from app.agent.contracts import WRITE_STATUS_SUCCESS


def summarize_review_outcome(
    *,
    task_titles_by_id: dict[str, str],
    done_ids: list[str],
    done_status_by_id: dict[str, str],
    pending_ids: list[str],
    pending_action: str,
    pending_date: str | None,
    move_status_by_id: dict[str, str] | None = None,
) -> str:
    done_confirmadas: list[str] = []
    moved_confirmadas: list[str] = []
    pending_confirmadas: list[str] = []
    falhas: list[str] = []

    for task_id in done_ids:
        title = task_titles_by_id.get(task_id)
        if not title:
            continue
        if done_status_by_id.get(task_id) == WRITE_STATUS_SUCCESS:
            done_confirmadas.append(title)
        else:
            falhas.append(title)

    if pending_action == "move" and pending_date:
        for task_id in pending_ids:
            title = task_titles_by_id.get(task_id)
            if not title:
                continue
            if (move_status_by_id or {}).get(task_id) == WRITE_STATUS_SUCCESS:
                moved_confirmadas.append(title)
            else:
                falhas.append(title)
        pending_output = moved_confirmadas
        pending_output_date = pending_date
    else:
        pending_confirmadas = [
            task_titles_by_id[task_id]
            for task_id in pending_ids
            if task_id in task_titles_by_id
        ]
        pending_output = pending_confirmadas
        pending_output_date = None

    resumo = mensagem_revisao_aplicada(
        done_confirmadas,
        pending_output,
        pending_output_date,
    )
    if falhas:
        resumo += " Não consegui aplicar tudo em: " + ", ".join(falhas) + "."
    return resumo
