import json

import pytest

from msa.config import ROOT, Settings


@pytest.fixture
def case():
    return json.loads((ROOT / "demo/case.json").read_text(encoding="utf-8"))


@pytest.fixture
def config(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'runs.sqlite'}",
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
    )
