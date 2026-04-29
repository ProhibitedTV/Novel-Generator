from __future__ import annotations

import shutil
from pathlib import Path


def delete_run_artifacts_dir(artifacts_dir: Path, run_id: str) -> None:
    target = artifacts_dir / run_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def delete_run_artifacts_dirs(artifacts_dir: Path, run_ids: list[str]) -> None:
    seen: set[str] = set()
    for run_id in run_ids:
        if run_id in seen:
            continue
        seen.add(run_id)
        delete_run_artifacts_dir(artifacts_dir, run_id)
