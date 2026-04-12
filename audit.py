"""
Script de inspeção de audit logs.

Uso:
    python audit.py                  # Últimas 20 tool calls
    python audit.py --today           # Tool calls de hoje
    python audit.py --tool list_tasks # Tool calls de uma tool específica
    python audit.py --errors          # Apenas tool calls com erro de validação
    python audit.py --user 123456     # Tool calls de um usuário específico
"""

import argparse
import json
from datetime import datetime, timedelta

import pytz

from app.db.database import SessionLocal
from app.models.tool_call_log import ToolCallLog

TIMEZONE = pytz.timezone("America/Sao_Paulo")


def fmt_log(log: ToolCallLog) -> str:
    dt = log.created_at
    if dt and dt.tzinfo is None:
        dt = TIMEZONE.localize(dt)
    dt_str = dt.strftime("%d/%m %H:%M:%S") if dt else "?"

    status = "✅" if not log.validation_error else "❌"
    line = f"[{dt_str}] {status} {log.tool_name} (user={log.user_id})"

    if log.arguments:
        args_str = json.dumps(log.arguments, ensure_ascii=False, default=str)
        line += f"\n    args: {args_str}"

    if log.validation_error:
        line += f"\n    erro: {log.validation_error}"

    if log.result:
        result_preview = log.result[:120]
        line += f"\n    result: {result_preview}"

    return line


def main():
    parser = argparse.ArgumentParser(description="Inspeção de audit logs da Sara")
    parser.add_argument("--today", action="store_true", help="Mostrar apenas logs de hoje")
    parser.add_argument("--tool", type=str, help="Filtrar por nome da tool")
    parser.add_argument("--errors", action="store_true", help="Mostrar apenas logs com erro")
    parser.add_argument("--user", type=str, help="Filtrar por user_id")
    parser.add_argument("--limit", type=int, default=20, help="Número de logs (default: 20)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(ToolCallLog)

        if args.today:
            hoje = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(ToolCallLog.created_at >= hoje)

        if args.tool:
            query = query.filter(ToolCallLog.tool_name == args.tool)

        if args.errors:
            query = query.filter(ToolCallLog.validation_error.isnot(None))

        if args.user:
            query = query.filter(ToolCallLog.user_id == args.user)

        logs = query.order_by(ToolCallLog.created_at.desc()).limit(args.limit).all()

        if not logs:
            print("Nenhum log encontrado.")
            return

        print(f"\n{'='*70}")
        print(f"Audit Logs — {len(logs)} registro(s)")
        print(f"{'='*70}\n")

        for log in logs:
            print(fmt_log(log))
            print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
