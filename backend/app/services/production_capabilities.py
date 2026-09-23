"""Resolve immutable workflow production decisions.

Null snapshots belong to workflows created before the capabilities migration.
"""

from app.models.workflow import WorkflowExecucao


def capabilities_for(workflow: WorkflowExecucao) -> dict:
    snapshot = getattr(workflow, "production_capabilities", None)
    if snapshot is not None:
        return snapshot
    # Legacy fallback only. New workflows always store the Ideas Hub contract.
    return {
        "delivery_mode": "rendered_video",
        "visual_mode": "semantic_match",
        "requires_media_scope": True,
        "caption_policy": "none"
        if getattr(workflow, "perfil_producao", None) == "faceless_pop_culture_lists"
        else "burned_in",
        "render_profile": None,
        "version": "legacy_workflow_v1",
    }


def burned_in_captions_for(workflow: WorkflowExecucao) -> bool:
    return capabilities_for(workflow)["caption_policy"] == "burned_in"
