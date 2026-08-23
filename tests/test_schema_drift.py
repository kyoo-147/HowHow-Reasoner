import json

from howhow.contracts import RECORD_TYPES


def test_checked_in_schema_snapshots_match_models() -> None:
    for model in RECORD_TYPES:
        path = __import__("pathlib").Path("schemas/v1") / f"{model.__name__}.json"
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == model.model_json_schema()
