"""Native instrument fixtures for backtests."""
from decimal import Decimal

PERP = {"id": "BTCUSDT-PERP.OKX", "type": "perpetual", "symbol": "BTCUSDT-PERP", "currency": "USDT", "baseCurrency": "BTC", "settlementCurrency": "USDT", "pricePrecision": 1,
        "sizePrecision": 3, "priceIncrement": "0.1", "sizeIncrement": "0.001", "takerFee": "0.0005"}
SPOT = {"id": "BTCUSDT.OKX", "type": "spot", "symbol": "BTCUSDT", "currency": "USDT", "baseCurrency": "BTC", "pricePrecision": 1, "sizePrecision": 3,
        "priceIncrement": "0.1", "sizeIncrement": "0.001", "takerFee": "0.001"}
PERP_VENUE = {"name": "OKX", "accountType": "MARGIN", "omsType": "NETTING", "balances": ["100000 USDT"], "baseCurrency": "USDT",
              "fees": {"type": "maker_taker", "maker": 0.0002, "taker": 0.0005}, "leverage": 1}
SPOT_VENUE = {"name": "OKX", "accountType": "CASH", "omsType": "NETTING", "balances": ["100000 USDT"], "fees": {"type": "maker_taker", "maker": 0.001, "taker": 0.001}, "leverage": 1}

BASE = 1704153600000
IID = "STOCK.SIM"


def stock_item(*, trigger=False):
    sessions = []
    for index, day in enumerate(['2024-01-02', '2024-01-03', '2024-01-04']):
        raw = ['100', '101', '99', '100'] if index < 2 else ['66', '67', '62' if trigger else '65', '66']
        adjusted = [str((Decimal(value) - 1) / Decimal('1.5')) for value in raw] if index < 2 else raw
        # Market's independently frozen source observations have a finite quote precision.
        adjusted = [format(Decimal(value).quantize(Decimal('.00000001')), 'f') for value in adjusted]
        sessions.append({'sessionDate': day, 'openAt': BASE + index * 86400000,
            'closeAt': BASE + index * 86400000 + 3600000,
            'rawOHLC': raw, 'adjustedOHLC': adjusted, 'rawVolume': '100000', 'adjustedVolume': '100000',
            'rawLower': '90' if index < 2 else '59.40', 'rawUpper': '110' if index < 2 else '72.60',
            'lower': '59.33333333' if index < 2 else '59.40', 'upper': '72.66666667' if index < 2 else '72.60',
            'rawPriceIncrement': '.01', 'priceIncrement': '.006666666666666667' if index < 2 else '.01',
            'priceMap': {'scale': str(Decimal(1) / Decimal('1.5')) if index < 2 else '1',
                'offset': str(-Decimal(1) / Decimal('1.5')) if index < 2 else '0', 'maxError': '.00000001'}})
    commission = {'rate': '.0003', 'perShare': '0', 'minimum': '5', 'maximumRate': None}
    return {'id': IID, 'type': 'equity', 'symbol': 'STOCK', 'currency': 'USD', 'pricePrecision': 8,
        'sizePrecision': 0, 'priceIncrement': '.00000001', 'sizeIncrement': '1', 'stockCommission': commission,
        'stock': {'orderSize': {'minimum': 100, 'increment': 100, 'oddLotSellAll': True},
            'settlement': {'sellAfterTradingDays': 1}, 'commission': commission, 'sessions': sessions,
            'taxes': [{'effectiveFrom': '2023-08-28', 'effectiveUntil': None, 'buyRate': '0', 'sellRate': '.0005'}],
            'corporateActions': [{'factId': 'cash', 'rowVersion': 'cash-v1', 'exDate': '2024-01-04', 'actionType': 'dividend', 'amount': '1'},
                                 {'factId': 'bonus', 'rowVersion': 'bonus-v1', 'exDate': '2024-01-04', 'actionType': 'split', 'ratio': '1.5'}],
            'materialVersion': 'verified-test-material', 'limitations': []}}

