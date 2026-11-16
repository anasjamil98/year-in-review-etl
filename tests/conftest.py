import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

import generate  # noqa: E402
from review import build  # noqa: E402

YEAR = 2025


@pytest.fixture(scope="session")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "zaffre.db"
    generate.build(path, n_customers=700, seed=7)
    return path


@pytest.fixture(scope="session")
def con(source, tmp_path_factory):
    output = tmp_path_factory.mktemp("out") / "review.db"
    connection = build.build(YEAR, source, output)
    yield connection
    connection.close()
