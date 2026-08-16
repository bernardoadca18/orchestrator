"""Configuração de logging estruturado (JSON) com contexto de workflow."""
import contextvars
import logging
import sys
from datetime import datetime, timezone

from pythonjsonlogger import jsonlogger

# Context variable para propagar workflow_id em toda a requisição/tarefa
workflow_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("workflow_id", default=None)


def set_workflow_id(workflow_id: str | None) -> None:
    """Define o workflow_id no contexto atual para ser incluído em todos os logs."""
    workflow_id_ctx.set(workflow_id)


def get_workflow_id() -> str | None:
    """Retorna o workflow_id do contexto atual."""
    return workflow_id_ctx.get()


class WorkflowJsonFormatter(jsonlogger.JsonFormatter):
    """Formatter JSON que inclui workflow_id do contexto em cada log."""

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)

        # Adicionar timestamp ISO 8601
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Adicionar workflow_id do contexto se disponível
        wf_id = get_workflow_id()
        if wf_id:
            log_record["workflow_id"] = wf_id

        # Adicionar campos padrão
        log_record["level"] = record.levelname
        log_record["logger"] = record.name


def setup_logging(json_format: bool = True) -> None:
    """Configura logging estruturado para a aplicação.

    Args:
        json_format: Se True, usa formato JSON. Se False, usa formato legível para desenvolvimento.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remover handlers existentes
    root_logger.handlers.clear()

    # Criar handler para stdout
    handler = logging.StreamHandler(sys.stdout)

    if json_format:
        formatter = WorkflowJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "name": "logger"},
        )
    else:
        # Formato legível para desenvolvimento
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Reduzir verbosidade de bibliotecas terceiras
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
