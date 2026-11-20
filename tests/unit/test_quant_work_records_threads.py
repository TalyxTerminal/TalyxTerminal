"""The run reads moved off the event loop read the records from several worker threads at once while the loop writes."""
import threading

from modules.quant.backend.work_records import WorkRecords


def test_many_threads_find_and_list_while_records_are_written(tmp_path):
    records = WorkRecords(tmp_path)
    for index in range(50):
        records.put("qrun", {"id": f"r{index}", "state": "completed", "createdAt": index, "payload": "x" * 2000})
    errors, stop = [], threading.Event()

    def reader():
        try:
            while not stop.is_set():
                assert records.find("qrun", "r7")["id"] == "r7"
                assert len(records.list("qrun")) >= 50
                assert records.db.execute("SELECT COUNT(*) FROM records").fetchone()[0] >= 50
        except Exception as error:  # noqa: BLE001 - collected and failed on below
            errors.append(error)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(8)]
    for thread in threads:
        thread.start()
    for index in range(50, 250):
        records.put("qrun", {"id": f"r{index}", "state": "completed", "createdAt": index})
    stop.set()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a reader hung on the records connection"
    records.close()
    assert errors == []
