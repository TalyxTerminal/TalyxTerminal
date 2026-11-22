"""Closed product configuration; original native descriptors keep native validation."""

def native_ns(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(label + " needs exact integer nanoseconds or decimal text.")
    try:
        stamp = int(value)
    except ValueError as error:
        raise ValueError(label + " needs exact integer nanoseconds or decimal text.") from error
    if not 0 <= stamp < 2**64:
        raise ValueError(label + " is outside native nanosecond bounds.")
    return stamp


def latency_ns(value, label):
    stamp = native_ns(value, label)
    if stamp is None:
        raise ValueError(label + " needs exact integer nanoseconds or decimal text.")
    return stamp


def fields(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object.")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(label + " has unsupported fields: " + ", ".join(sorted(unknown)))
    if missing:
        raise ValueError(label + " needs: " + ", ".join(sorted(missing)))


def validate(config):
    fields(config, {"engine", "name", "dataMeaning", "task", "market_data", "dataType", "timezone", "priceBasis", "instruments", "venues", "strategy", "riskEngine", "referencePrices", "tradeFrom"}, {"instruments", "venues", "strategy"}, "Strategy definition")
    if config.get('engine', 'nautilus') != 'nautilus' or config.get('task', 'event') != 'event' or config.get('dataType') != 'bars':
        raise ValueError('Strategy objects run Nautilus on their frozen Market bars.')
    bases = config.get('referencePrices', [])
    if not isinstance(bases, list) or any(value not in {'mark', 'index'} for value in bases) or len(bases) != len(set(bases)):
        raise ValueError('referencePrices contains mark and/or index without duplicates.')
    if not config["venues"] or not config["instruments"]:
        raise ValueError("Define at least one native venue and instrument.")
    venue_names = [item["name"] for item in config["venues"]]
    ids = [item["id"] for item in config["instruments"]]
    if len(set(venue_names)) != len(venue_names) or len(set(ids)) != len(ids):
        raise ValueError("Native venues and instrument identities must be unique.")
    for item in config["instruments"]:
        fields(item, {"id", "type", "symbol", "currency", "baseCurrency", "pricePrecision", "sizePrecision", "priceIncrement", "sizeIncrement", "settlementCurrency", "inverse", "marginInit", "marginMaint", "makerFee", "takerFee", "stock", "stockCommission", "executionRaw", "executionState", "rebuildOrders", "rebuildSeed"}, {"id", "type", "currency", "pricePrecision", "priceIncrement"}, "Native instrument")
        if item["id"].rsplit(".", 1)[-1] not in venue_names:
            raise ValueError("Each exact instrument needs its declared venue.")
    for item in config["venues"]:
        fields(item, {"name", "omsType", "accountType", "balances", "baseCurrency", "fees", "latency", "bookType", "leverage", "marginModel", "liquidityConsumption", "queuePosition", "liquidation", "liquidationTriggerRatio"}, {"name", "balances"}, "Native venue")
        for flag in ("queuePosition", "liquidityConsumption", "liquidation"):
            if flag in item and type(item[flag]) is not bool:
                raise ValueError("Native " + flag + " needs a boolean value.")
        fields(item.get("latency", {}), {"base", "insert", "update", "cancel"}, set(), "Native latency model")
        for key, value in item.get("latency", {}).items():
            latency_ns(value, "Native " + key + " latency")
        fields(item.get("fees", {}), {"type", "amount", "maker", "taker"}, set(), "Native fee model")
        if item.get("fees", {}).get("type", "fixed") not in {"fixed", "maker_taker", "per_instrument", "instrument_conditions"}:
            raise ValueError("Choose fixed, maker_taker, per_instrument or frozen instrument conditions for commission.")
        if item.get("fees", {}).get("type") == "per_instrument" and any(not {"makerFee", "takerFee"} <= set(instrument) for instrument in config["instruments"] if instrument["id"].rsplit(".", 1)[-1] == item["name"]):
            raise ValueError("Per-instrument commission needs makerFee and takerFee on every venue instrument.")
        if item.get('fees', {}).get('type') == 'instrument_conditions' and any('stock' not in instrument for instrument in config['instruments'] if instrument['id'].rsplit('.', 1)[-1] == item['name']):
            raise ValueError('Instrument-condition commission requires frozen stock terms for every venue instrument.')
        if item.get("marginModel", "standard") not in {"standard", "leveraged"}:
            raise ValueError("Choose the standard or leveraged native margin model.")
    strategy = config['strategy']
    fields(strategy, {'kind', 'module', 'nextRebalance'}, {'kind', 'module'}, 'Strategy')
    if strategy['kind'] != 'object':
        raise ValueError("Use the strategy object's authored Python class.")
    if 'nextRebalance' in strategy and type(strategy['nextRebalance']) is not bool:
        raise ValueError('Strategy nextRebalance must be boolean.')
