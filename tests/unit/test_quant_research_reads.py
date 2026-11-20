"""UTF-8-safe log segments keep byte offsets of the original file."""

import json

from modules.quant.backend.research_reads import log_segment


def test_log_offsets_use_original_bytes_with_invalid_encoding_and_partial_utf8(tmp_path):
    path = tmp_path / "stderr.log"
    raw = b"pip\xff\xfe\n" + "中文".encode("gbk") + b"\n" + "正常日志".encode() + b"\xe4"
    path.write_bytes(raw)
    position, text = 0, ""
    while position < len(raw):
        segment = log_segment(path, position, 4)
        assert segment["nextOffset"] > position
        position = segment["nextOffset"]
        text += segment["text"]
    assert position == len(raw) and "正常日志" in text and "\ufffd" in text
    path.write_bytes("中".encode()[:2])
    assert log_segment(path, 0, final=False)["nextOffset"] == 0
    path.write_bytes("中文".encode())
    assert log_segment(path, 0, final=False)["text"] == "中文"
    path.write_bytes(b"\x00\xff" * 12_000)
    result = log_segment(path, 0)
    assert len(json.dumps(result["text"], ensure_ascii=False).encode()) <= 15_000
    assert 0 < result["nextOffset"] <= 12_000
