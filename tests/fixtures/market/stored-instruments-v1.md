# stored-instruments-v1.json

Eight crypto instrument records in the earlier stored shape (vocabulary 1.0.0, with `sourceId`), copied exactly as they were stored. `tests/unit/test_market_backbone.py` checks that their instrument ids do not change under vocabulary 2.0.0: each record's id must be reproduced from its fields and survive `MarketInstrument.from_wire`.
