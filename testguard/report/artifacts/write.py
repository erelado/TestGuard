from __future__ import annotations

from testguard.analysis import DiffSummary, RunMetrics
from testguard.report.artifacts.json import write_run_report_json
from testguard.report.artifacts.markdown import write_run_report_markdown
from testguard.store.store import RunRecord
from testguard.util import runs_dir


def write_run_report_artifacts(
    *,
    run_record: RunRecord,
    current_metrics: RunMetrics,
    diff_summary: DiffSummary,
) -> None:
    run_directory = runs_dir() / run_record.run_id
    run_directory.mkdir(parents=True, exist_ok=True)

    write_run_report_json(
        run_directory=run_directory,
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )
    write_run_report_markdown(
        run_directory=run_directory,
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )