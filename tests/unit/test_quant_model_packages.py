import shutil
import sys
from types import ModuleType, SimpleNamespace

from modules.quant.native.model_state import archive_recorder
from modules.quant.native.publish import Publisher


def test_fixed_recorder_retains_same_filename_in_two_directories(tmp_path, monkeypatch):
    source = tmp_path / "recorder"
    for name, content in (("params.pkl", b"model"), ("first/report.pkl", b"first report"), ("second/report.pkl", b"second report")):
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    class Client:
        def list_artifacts(self, identity, directory):
            return [SimpleNamespace(path=file.relative_to(source).as_posix(), is_dir=file.is_dir()) for file in (source / directory).iterdir()]
    tracking = ModuleType("mlflow.tracking")
    tracking.MlflowClient = lambda **kwargs: Client()
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    class Recorder:
        id, experiment_id, uri = "recorder", "original", source.as_uri()
        def download_artifact(self, name, dst_path):
            destination = tmp_path / dst_path / (source / name).name
            shutil.copyfile(source / name, destination)
            return str(destination)
    publisher = Publisher({"runId": "archive", "output": str(tmp_path / "output")})
    artifacts = archive_recorder(Recorder(), publisher, "window-2")
    assert len(artifacts) == 3
    for item in artifacts:
        assert (publisher.root / item["name"]).read_bytes() == (source / item["nativePath"]).read_bytes()
    (source / "first/report.pkl").write_bytes(b"later report")
    assert (publisher.root / "window-2/native/first/report.pkl").read_bytes() == b"first report"
    assert (publisher.root / "window-2/native/second/report.pkl").read_bytes() == b"second report"
