-- Top wash-trading candidate pairs on LooksRare / Ethereum / 2022.
--
-- Collapses directed (buyer, seller) trade counts into UNORDERED wallet pairs
-- {wallet_a, wallet_b} (a = lexicographically smaller address) so we can measure
-- back-and-forth SYMMETRY, which is the primary self-dealing tell.
--
--   a_buys  = trades where wallet_a was the buyer  (a bought from b)
--   a_sells = trades where wallet_a was the seller (b bought from a)
--   symmetry_ratio = min(a_buys, a_sells) / max(a_buys, a_sells)  -> ~1.0 is suspicious
--
-- Partition keys (blockchain, project, block_month) are filtered for performance.
-- amount_usd is summed only as WEAK secondary corroboration — LooksRare 2022 USD
-- values are inflated by LOOKS-token reward farming, so trust counts + symmetry.

WITH directed AS (
    SELECT
        buyer,
        seller,
        COUNT(*)         AS n,
        SUM(amount_usd)  AS usd
    FROM nft.trades
    WHERE blockchain = 'ethereum'
      AND project    = 'looksrare'
      AND block_month >= DATE '2022-01-01'
      AND block_month <  DATE '2023-01-01'
      AND buyer <> seller                       -- exclude self-address trades
    GROUP BY buyer, seller
)
SELECT
    LEAST(buyer, seller)    AS wallet_a,
    GREATEST(buyer, seller) AS wallet_b,
    SUM(CASE WHEN buyer = LEAST(buyer, seller) THEN n ELSE 0 END)   AS a_buys,
    SUM(CASE WHEN seller = LEAST(buyer, seller) THEN n ELSE 0 END)  AS a_sells,
    SUM(n)                                                          AS total_trades,
    SUM(usd)                                                        AS total_usd
FROM directed
GROUP BY 1, 2
ORDER BY total_trades DESC
LIMIT 200;
