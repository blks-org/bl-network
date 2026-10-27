GENESIS_SUPPLY = 1_000_000.0   # начальная эмиссия в treasury
NANO = 1_000_000_000

# Экономика
BLOCK_REWARD   = 10.0
MIN_STAKE      = 100.0
UNSTAKE_DELAY  = 3600
FAUCET_AMOUNT  = 100.0
FAUCET_ONCE    = True

# Блоки
BLOCK_INTERVAL    = 5.0
MAX_TXS_PER_BLOCK = 500
MAX_GAS_PER_BLOCK = 10_000_000
MIN_FEE           = 0.001

# DNS
DNS_PRICE        = 0.1
DOMAIN_TTL       = 365 * 24 * 3600
AUCTION_DURATION = 180
EXTEND_WINDOW    = 30
BID_STEP         = 1.05
MIN_BID_SHORT    = 100.0
MIN_BID_MEDIUM   = 10.0
MIN_BID_LONG     = 1.0
NAME_RE          = r"^[a-z0-9][a-z0-9-]{0,62}$"

def min_bid_for(name):
    n = len(name)
    if n <= 4: return MIN_BID_SHORT
    if n <= 8: return MIN_BID_MEDIUM
    return MIN_BID_LONG