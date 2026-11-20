from talyx_sdk.network_changes import NetworkChanges


def test_interface_parameter_refreshes_do_not_count_as_a_route_change():
    changes = NetworkChanges()
    try:
        before = changes.epoch
        # Windows refreshes interface parameters about once a minute without moving a route.
        changes._interface_changed(None, None, 0)
        changes._changed(None, None, 3)
        assert changes.epoch == before
        changes._interface_changed(None, None, 1)
        changes._changed(None, None, 0)
        assert changes.epoch == before + 2
    finally:
        changes.close()
