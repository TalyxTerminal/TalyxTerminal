"""Accepted-observation provider opt-in for the approved PyneCore wheel."""
# ruff: noqa: E501 -- native source lines are preserved verbatim for wheel identity

import hashlib


def patched_security_dispatch(raw: bytes) -> bytes:
    if (
        hashlib.sha256(raw).hexdigest()
        != "e70060ba84dc55d1562cccae7dc8eabd8b86d77616617a396d40cce504c60d35"
    ):
        raise ValueError("The approved base security.py identity differs")
    source = raw.decode().replace("\r\n", "\n")
    replacements = [
        (
            "    is_live: bool = False\n",
            "    is_live: bool = False\n\n    # Captured providers receive one round for each accepted chart observation.\n    authoritative_observations: bool = False\n",
        ),
        (
            "            gaps_on=is_gaps_on,\n",
            "            gaps_on=is_gaps_on,\n            authoritative_observations=bool(ctx.get('authoritative_observations', False)),\n",
        ),
        (
            "        if target_time > state.last_confirmed:\n            state.last_confirmed = target_time\n            state.new_period = True\n            # Make sure no stale developing/override flag leaks across modes.\n",
            "        if (target_time > state.last_confirmed\n                or (state.authoritative_observations and state.is_live)):\n            state.new_period = target_time > state.last_confirmed\n            state.last_confirmed = max(state.last_confirmed, target_time)\n            # Make sure no stale developing/override flag leaks across modes.\n",
        ),
    ]
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError("Captured security dispatch patch location differs")
        source = source.replace(before, after)
    return source.replace("\n", "\r\n").encode()


def patched_live_runner(raw: bytes) -> bytes:
    if (
        hashlib.sha256(raw).hexdigest()
        != "0abf255ec24662b994c3c06850f4c5f3ae642bf4c3be9dc01bbb22beee924c61"
    ):
        raise ValueError("The upstream live_runner.py identity differs")
    lines = raw.decode().replace("\r\n", "\n").splitlines(keepends=True)
    lines[1311:1311] = """\
                    if authoritative_observations:
                        # Absence of an accepted close is not a close or a new bar.
                        continue
""".splitlines(keepends=True)
    lines[1189:1190] = (
        "                if last_closed_bar is not None and not authoritative_observations:\n".splitlines(
            keepends=True
        )
    )
    lines[997:998] = (
        "                elapsed_ms = _observation_now_ms() - last_historical_timestamp\n".splitlines(
            keepends=True
        )
    )
    lines[774:775] = (
        "        local_dt = datetime.fromtimestamp(_observation_now_ms() / 1000.0, tz=_sym_tz)\n".splitlines(
            keepends=True
        )
    )
    lines[741:741] = """\
    authoritative_observations = bool(getattr(provider, "authoritative_observations", False))

    def _observation_now_ms() -> float:
        if authoritative_observations:
            return float(provider.observation_time_ms())
        return time.time() * 1000.0

""".splitlines(keepends=True)
    return "".join(lines).replace("\n", "\r\n").encode()


def patched_security_process(raw: bytes) -> bytes:
    if (
        hashlib.sha256(raw).hexdigest()
        != "b7c30daa97d524faf2ad2d3c5890b0d7d195b3a8238f4c588b2dcedb8fef0c7e"
    ):
        raise ValueError("The upstream security_process.py identity differs")
    lines = raw.decode().replace("\r\n", "\n").splitlines(keepends=True)
    lines[1755:1765] = """\
                        if dev_values is None:
                            (dev_open, dev_high, dev_low, dev_close, dev_volume,
                             dev_time_ms) = sync_block.get_developing_bar(sec_id)
                        else:
                            (dev_open, dev_high, dev_low, dev_close, dev_volume,
                             dev_time_ms) = dev_values
                        ohlcv = OHLCV(timestamp=dev_time_ms,
                            open=dev_open, high=dev_high, low=dev_low,
                            close=dev_close, volume=dev_volume)
""".splitlines(keepends=True)
    lines[1751:1754] = """\
                    if isinstance(dev_values, OHLCV):
                        ohlcv = dev_values
                        dev_time_ms = ohlcv.timestamp
""".splitlines(keepends=True)
    lines[1667:1677] = """\
                        if dev_values is None:
                            (dev_open, dev_high, dev_low, dev_close, dev_volume,
                             dev_time_ms) = sync_block.get_developing_bar(sec_id)
                        else:
                            (dev_open, dev_high, dev_low, dev_close, dev_volume,
                             dev_time_ms) = dev_values
                        ohlcv = OHLCV(timestamp=dev_time_ms,
                            open=dev_open, high=dev_high, low=dev_low,
                            close=dev_close, volume=dev_volume)
""".splitlines(keepends=True)
    lines[1663:1666] = """\
                    if isinstance(dev_values, OHLCV):
                        ohlcv = dev_values
                        dev_time_ms = ohlcv.timestamp
""".splitlines(keepends=True)
    lines[1476:1478] = (
        "        yield (target_time, slot_flags, bool(slot_flags & FLAG_MORE_STEPS), None)\n".splitlines(
            keepends=True
        )
    )
    lines[1475:1475] = """\
        target_time = sync_block.get_target_time(sec_id)
        if authoritative_observations:
            closed, developing, historical = live_streamer.refresh(target_time, sec_ctx.round_tick)
            if closed and not historical and not is_ltf:
                for index, bar in enumerate(live_streamer.pop_new_closed_bars()):
                    _ingest_streamer_bar(bar)
                    following = index + 1 < len(closed) or developing is not None
                    round_is_last_step[0] = not following
                    flags = (slot_flags | FLAG_CLOSED_OVERRIDE) & ~(FLAG_IS_DEVELOPING | FLAG_DEV_HISTORICAL)
                    if following:
                        flags |= FLAG_MORE_STEPS
                    yield (bar.timestamp, flags, following or bool(slot_flags & FLAG_MORE_STEPS), bar)
                if developing is None:
                    return
            if developing is not None and not is_ltf:
                round_is_last_step[0] = True
                dev_flags = (slot_flags | FLAG_IS_DEVELOPING) & ~FLAG_CLOSED_OVERRIDE
                if historical:
                    dev_flags |= FLAG_DEV_HISTORICAL
                else:
                    dev_flags &= ~FLAG_DEV_HISTORICAL
                yield (developing.timestamp, dev_flags, bool(slot_flags & FLAG_MORE_STEPS), developing)
                return
""".splitlines(keepends=True)
    lines[673:673] = (
        "        if authoritative_observations:\n            streamer.prime(bar_buffer)\n".splitlines(
            keepends=True
        )
    )
    lines[665:670] = """\
        if authoritative_observations:
            class AcceptedObservationStream:
                # A captured snapshot is already accepted. No concurrent queue
                # or wall-clock producer can add observations behind its back.
                def __init__(self):
                    self.seen = {}
                    self.pending = []
                    self.developing = None
                    self.historical = True

                @staticmethod
                def identity(bar):
                    import json
                    return json.dumps([bar.timestamp, bar.open, bar.high, bar.low,
                        bar.close, bar.volume, bar.extra_fields, bar.is_closed], sort_keys=True)

                def prime(self, bars):
                    self.seen = {bar.timestamp: self.identity(bar) for bar in bars}

                def start(self):
                    pass

                def stop(self):
                    pass

                def refresh(self, target_time, round_tick):
                    closed, self.developing, self.historical = live_provider.read_security_observation(
                        target_time, round_tick)
                    for bar in closed:
                        prior = self.seen.get(bar.timestamp)
                        identity = self.identity(bar)
                        if prior is not None:
                            if prior != identity:
                                raise RuntimeError("Accepted closed secondary history changed; native replay required")
                            continue
                        if self.seen and bar.timestamp <= max(self.seen):
                            raise RuntimeError("Accepted secondary history inserted behind the native cursor")
                        self.seen[bar.timestamp] = identity
                        self.pending.append(bar)
                    return list(self.pending), self.developing, self.historical

                def pop_new_closed_bars(self):
                    result, self.pending = self.pending, []
                    return result

                def wait_for_bars(self, timeout):
                    return self.pop_new_closed_bars()

                def peek_developing_bar(self):
                    return self.developing

            live_streamer = streamer = AcceptedObservationStream()
        else:
            live_streamer = streamer = live_runner.LiveBarStreamer(
                live_provider, data_source.symbol, data_source.timeframe,
                syminfo=syminfo,
                last_historical_timestamp=None,
            )
""".splitlines(keepends=True)
    lines[651:652] = """\
        time_to = (datetime.fromtimestamp(live_provider.observation_time_ms() / 1000.0, UTC)
                   if authoritative_observations else datetime.now(UTC))
        if authoritative_observations:
            live_bar_grace_seconds = 0.0
""".splitlines(keepends=True)
    lines[638:638] = """\
        authoritative_observations = bool(getattr(live_provider, "authoritative_observations", False))
""".splitlines(keepends=True)
    lines[594:594] = "    authoritative_observations = False\n".splitlines(keepends=True)
    source = "".join(lines)
    before = "                yield (developing.timestamp, dev_flags, bool(slot_flags & FLAG_MORE_STEPS), developing)\n                return\n"
    after = (
        before
        + "            if not historical and not is_ltf:\n                # No qualifying observation may commit a forming baseline.\n                sec_ctx.released.clear()\n                round_is_last_step[0] = True\n                for member in sec_ids:\n                    with result_locks[member]:\n                        write_na(result_blocks[member], sync_block)\n                _release_round()\n                _end_round()\n                return\n"
    )
    if source.count(before) != 1:
        raise ValueError("Captured absent-observation patch location differs")
    return source.replace(before, after).replace("\n", "\r\n").encode()


def patched_own_symbol_feed(security_raw: bytes, script_runner_raw: bytes) -> tuple[bytes, bytes]:
    """A context whose bars a captured provider hands over reads them for the chart's own
    symbol too: no chart-side HTF aggregation, so a higher timeframe of the own symbol
    behaves like any other symbol's (developing values live, TradingView's real-time
    semantics), one rule for every request.security."""
    replacements = [
        (security_raw, (
            "                is_same_symbol = (\n"
            "                    chart_symbols is None\n",
            "                is_same_symbol = not ctx.get('authoritative_observations', False) and (\n"
            "                    chart_symbols is None\n",
        )),
        (script_runner_raw, (
            "                    is_same_symbol = (chart_ticker is None\n"
            "                                      or str(base_symbol) in own_symbols)\n",
            "                    is_same_symbol = not sec_state.authoritative_observations and (\n"
            "                        chart_ticker is None or str(base_symbol) in own_symbols)\n",
        )),
    ]
    patched = []
    for raw, (before, after) in replacements:
        source = raw.decode().replace("\r\n", "\n")
        if source.count(before) != 1:
            raise ValueError("Own-symbol feed patch location differs")
        patched.append(source.replace(before, after).replace("\n", "\r\n").encode())
    return patched[0], patched[1]


def patched_last_history_bar(script_runner_raw: bytes) -> bytes:
    """TradingView's barstate.islast: the dataset's last bar. In a live run with no real-time
    bar after the history (a closed market) that is the last historical bar; the chart provider
    says so (``history_ends_dataset``). Real-time bars keep islast as they are."""
    source = script_runner_raw.decode().replace("\r\n", "\n")
    before = (
        "                if is_live:\n"
        "                    barstate.islast = False\n"
        "                    barstate.islastconfirmedhistory = (next_item is LIVE_TRANSITION)\n"
    )
    after = (
        "                if is_live:\n"
        "                    barstate.islast = (next_item is LIVE_TRANSITION\n"
        "                                       and getattr(self._chart_provider_instance,\n"
        "                                                   'history_ends_dataset', False))\n"
        "                    barstate.islastconfirmedhistory = (next_item is LIVE_TRANSITION)\n"
    )
    if source.count(before) != 1:
        raise ValueError("Last history bar patch location differs")
    return source.replace(before, after).replace("\n", "\r\n").encode()
