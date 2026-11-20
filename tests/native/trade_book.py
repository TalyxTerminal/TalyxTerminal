"""A small test exchange ledger supplying authoritative account frames to real nodes."""
from copy import deepcopy
from decimal import Decimal


class Book:
    def __init__(self, funds, target, sim, margin):
        self.funds, self.margin = funds, margin
        self.shares = {p['instrumentId']: {'quantity': Decimal(p['quantity']) * (1 if p['side'] == 'LONG' else -1),
                       'average': Decimal(p['avgPx'])} for p in (sim or {'positions': []})['positions']}
        self.fees = Decimal((sim or {}).get('feeAdjustment', '0'))
        self.funding = sum((Decimal(p['amount']) for p in target['fundingTotals']), Decimal(0))
        cost = sum((p['quantity'] * p['average'] for p in self.shares.values()), Decimal(0)) if not margin else Decimal(0)
        self.realized = Decimal((sim or {}).get('quote', funds['amount'])) - Decimal(funds['amount']) + cost + self.fees - self.funding
        self.sequence, self.reductions, self.seen = 0, [], set()
        self.cursor = target['cursor']

    def snapshot(self):
        rows = [{'instrumentId': instrument, 'quantity': str(p['quantity']), 'averagePrice': str(p['average']) if p['quantity'] else None,
                 'realizedPnl': {'amount': '0', 'currency': self.funds['currency']}} for instrument, p in self.shares.items()]
        if not rows:
            rows = [{'instrumentId': 'unused', 'quantity': '0', 'averagePrice': None,
                     'realizedPnl': {'amount': '0', 'currency': self.funds['currency']}}]
        rows[0]['realizedPnl']['amount'] = str(self.realized)
        return {'shares': rows, 'fees': str(self.fees), 'funding': str(self.funding), 'sequence': self.sequence,
                'cursor': self.cursor, 'fillCursor': self.cursor,
                'fillIds': sorted(self.seen), 'cycleId': 'book-cycle', 'reductions': deepcopy(self.reductions)}

    def fill(self, instrument, side, frame):
        if frame['tradeId'] in self.seen:
            return self.snapshot()
        self.seen.add(frame['tradeId'])
        quantity, price = Decimal(frame['quantity']), Decimal(frame['price'])
        delta = quantity * (1 if side.lower() == 'buy' else -1)
        if not self.margin and 'feeValuation' in frame:
            delta -= Decimal(frame['feeValuation']['baseAmount'])
            quantity = abs(delta)
        held = self.shares.setdefault(instrument, {'quantity': Decimal(0), 'average': price})
        if held['quantity'] * delta < 0:
            self.realized += min(abs(held['quantity']), quantity) * (price - held['average']) * (1 if held['quantity'] > 0 else -1)
        left = held['quantity'] + delta
        if held['quantity'] == 0 or held['quantity'] * left < 0:
            held['average'] = price
        elif held['quantity'] * delta > 0:
            held['average'] = (abs(held['quantity']) * held['average'] + quantity * price) / abs(left)
        held['quantity'] = left
        self.fees += Decimal(frame['feeValuation']['settlementAmount']) if 'feeValuation' in frame else Decimal(frame['commission'])
        self.sequence += 1
        self.cursor = frame['fillCursor'] if frame['fillCursor'] is not None else self.cursor
        return self.snapshot()
