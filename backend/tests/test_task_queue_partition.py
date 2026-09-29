"""Audio recovery remains single-owned while short control tasks have a worker."""

from types import SimpleNamespace

from app.core.celery_app import celery_app, is_control_worker


def test_task_routes_keep_voice_serial_and_render_poll_independent() -> None:
    router = celery_app.amqp.router
    assert router.route({}, "app.tasks.production_tasks.processar_producao_workflow")[
        "queue"
    ].name == "audio_producao"
    assert router.route({}, "app.tasks.audio_tasks.gerar_audio_workflow")[
        "queue"
    ].name == "audio_producao"
    assert router.route({}, "app.tasks.production_tasks.poll_video_render")[
        "queue"
    ].name == "production_control"
    assert router.route({}, "app.tasks.qa_tasks.executar_qa_producao")[
        "queue"
    ].name == "production_control"


def test_recovery_worker_identity_is_unambiguous() -> None:
    assert is_control_worker(SimpleNamespace(hostname="control@host"))
    assert not is_control_worker(SimpleNamespace(hostname="audio@host"))
    assert not is_control_worker(SimpleNamespace(hostname="celery@legacy-host"))


def test_recovery_runs_only_on_owning_worker(monkeypatch) -> None:
    from app.tasks import production_tasks, qa_tasks

    calls: list[str] = []

    async def production() -> None:
        calls.append("production")

    async def qa() -> None:
        calls.append("qa")

    monkeypatch.setattr(production_tasks, "recover_productions", production)
    monkeypatch.setattr(qa_tasks, "recover_standard_qa", qa)
    audio_worker = SimpleNamespace(hostname="audio@host")
    control_worker = SimpleNamespace(hostname="control@host")
    production_tasks.setup_production_recovery(control_worker)
    qa_tasks.setup_qa_recovery(audio_worker)
    assert calls == []
    production_tasks.setup_production_recovery(audio_worker)
    qa_tasks.setup_qa_recovery(control_worker)
    assert calls == ["production", "qa"]
