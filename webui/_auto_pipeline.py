"""Auto-pipeline WebUI adapter: bridges core.pipeline.run_auto_pipeline to the job system."""

from browser import backend_driver
from core import jobs, pipeline
from core.schema import AutoPipelineResult, PipelineItem


def _run_auto_pipeline(
    job, cfg: dict, built: list[PipelineItem], *, note_expiry=None
) -> AutoPipelineResult:
    """Run draft→verify→publish for all *built* packages inside an existing job.

    Thin adapter: translates WebUI job callbacks to core.pipeline.run_auto_pipeline's
    on_progress / on_status / on_session_expired interface.
    """
    return pipeline.run_auto_pipeline(
        built,
        cfg,
        timeout_ms=backend_driver.DEFAULT_TIMEOUT_MS,
        on_progress=lambda msg: jobs.report(job, msg),
        on_status=lambda msg: jobs.set_current(job, msg),
        on_session_expired=note_expiry,
    )
