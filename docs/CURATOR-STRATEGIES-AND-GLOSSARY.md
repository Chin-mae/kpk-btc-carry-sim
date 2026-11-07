# Risk curators, their strategies, and the jargon — a learning note
Written 5 Nov 2026 for Chinmay, from the competitor deep dive (~/workspace/research_notes/kpk-fund-strategies-competitors-20261104-1801/report.md). Every strategy figure is dated in that report; this note teaches the concepts.

## The jargon bank (read this first, everything else uses these words)

- **Risk curator** — a firm that decides where other people's crypto gets lent inside lending apps, and under what safety rules. The curator does not hold the money; it sets the rules and moves allocations between approved markets.
- **Collateral** — the asset a borrower pledges as security. If the borrower does not repay, the collateral is sold to repay lenders.
- **LLTV (Liquidation Loan-To-Value)** — the maximum loan size, as a percentage of collateral value, at which the lender starts seizing collateral. 86% LLTV on $100 of BTC means seizure risk begins once the loan reaches $86 against it. It is a ceiling set by the protocol.
- **LTV (Loan-To-Value)** — the loan size a strategy actually chooses, below the LLTV ceiling. Choosing 70% when 86% is allowed is choosing a safety cushion.
- **Health factor (HF)** — one number for "how close to seizure": collateral value adjusted by the liquidation threshold, divided by the loan. HF 1.21 = a 21%-ish cushion in ratio terms. HF 1.0 = seizure starts.
- **Utilisation** — what percentage of a vault's money is currently lent out. 90% utilised = only 10% can be withdrawn instantly. 100% utilised = nobody can exit until borrowers repay, even if the vault is perfectly safe.
- **Bad debt** — a loan that was not repaid and could not be fully recovered from selling the collateral. The loss lands on lenders.
- **Cap** — a curator-set maximum: "no more than X% of this vault in that market." Caps stop one bad market from killing the whole vault.
- **Rebalancing** — moving money between approved markets as rates and risks change.
- **APY** — the yearly return including compounding (interest earning interest). Always ask what it was measured over: 7d and 30d APYs are snapshots, not promises.
- **TVL (Total Value Locked)** — total money currently inside a product. Size, not profit.
- **Looping / recursive leverage** — deposit, borrow, buy more of the same asset, deposit that, borrow again. Each round multiplies both yield and seizure risk. "No recursive leverage" means borrow once, never re-borrow against the borrowed money.
- **LST (Liquid Staking Token)** — a receipt token for staked ETH (wstETH, rETH, cbETH). It grows in value as staking rewards accrue, and can be traded or pledged instead of waiting to unstake.
- **LRT (Liquid Restaking Token)** — the same receipt idea, one layer riskier: the staked asset is re-pledged to secure additional networks for extra yield (weETH, rsETH, ezETH), adding slashing risk (see below).
- **Slashing** — a penalty that destroys part of staked assets when the operator misbehaves or fails. The core extra risk of staking/restaking products.
- **E-mode (Efficiency Mode)** — an Aave feature: when collateral and loan are closely related assets (ETH-family vs ETH), the protocol allows much higher loan limits (93–96.5% LLTV) because they tend to move together. Higher limits, thinner cushion if they stop moving together (a depeg).
- **Depeg** — when a token that should track another asset's price (a wrapped, staked, or stable token) trades below it. A 5% depeg can trigger seizures in positions the price chart says are safe.
- **Delta-neutral** — a position built so a price move in either direction roughly cancels out: own the asset, and simultaneously bet against it in equal size. What remains is a fee/funding/interest income, not price profit.
- **Perpetual future (perp)** — a futures contract with no expiry date. Traders pay or receive a periodic **funding rate** to keep its price glued to the spot price.
- **Funding rate** — the periodic payment between perp longs and shorts. When most traders are long, longs pay shorts. Harvesting funding = being paid to hold the unpopular side, hedged.
- **Basis** — the price gap between a futures contract and the spot asset. Futures usually trade slightly above spot (contango); the gap shrinks to zero at expiry, and a cash-and-carry trade (buy spot, sell the future) pockets it.
- **Market-neutral** — the same family as delta-neutral, used for fund labels: profit from spreads and fees, not from guessing price direction.
- **Arbitrage** — buying the same thing cheap in one venue and selling it dear in another, pocketing the difference. Real, but the gap usually closes as you trade it.
- **Rehypothecation** — lending out an asset that was itself pledged to you. It chains risk: if the final borrower fails, everyone in the chain is exposed. Central to the Stream Finance collapse.
- **Concentrated liquidity (CL)** — providing trading liquidity only inside a chosen price range on Uniswap v3/v4, instead of all prices. Earns much higher fees while price stays in range; earns nothing, and holds only the losing asset, once price leaves the range.
- **Impermanent loss (IL)** — the loss a liquidity provider suffers versus simply holding the two assets, caused by price movement. Called "impermanent" because it reverses if price returns; it becomes permanent the moment you withdraw or re-centre the range. The modern, sharper term is LVR (loss-versus-rebalancing): what arbitrage traders extract from your position.
- **RWA (Real-World Asset)** — a token representing an offchain asset: US Treasury bills (BUIDL, JTRSY), AAA corporate loan bundles (JAAA), property, invoices.
- **Private credit** — loans to real companies/borrowers, arranged by a manager rather than a bank. Maple is the onchain example.
- **Overcollateralised** — the borrower pledges more than they borrow (Maple's loans run at 148–174% collateral). Your safety comes from the excess pledge.
- **Take rate** — the curator's cut, expressed as a share of the yield the vault earned (not of assets). Sentora's ~11.7% of earnings is the highest among large curators; Gauntlet's ~1.2% the lowest (Alea, Q3 2026).
- **Performance fee** — a percentage of profits only (e.g. 25% of yield earned), as opposed to a management fee, which is a percentage of assets charged regardless.
- **Oracle** — the feed that tells a protocol what an asset's price is. Protocols liquidate using oracle prices, not market prices; if the oracle is stale or wrong, the protocol acts on a fiction.
- **Risk oracle** — Chaos Labs' product: a live system that automatically adjusts a lending protocol's limits as market conditions change, inside bounds governance approved.
- **CeFi** — centralised finance: exchanges and prime brokers (Kraken, Coinbase, August) as opposed to onchain DeFi protocols.
- **OTC (Over-The-Counter)** — a privately negotiated deal between two parties, not on a public market.
- **Synthetic dollar** — a dollar-pegged token that is not backed by dollars in a bank, but by a hedged crypto position (Ethena's USDe: crypto collateral plus an equal short perp position).
- **Tokenised certificate / note** — a token issued by a company (Midas) representing a claim on an offchain-managed strategy. The strategy runs offchain; the token is your receipt and your legal claim.
- **Monte Carlo / agent-based simulation** — Gauntlet's method: simulate thousands of fictional market futures and thousands of fictional borrower/liquidator behaviours, and count how often the vault would go insolvent. Parameters are set from that failure probability, not from gut feel.

## The curators and what each one actually does

### Steakhouse Financial — the credit-rating conservative
Strategy: lending only, and only against collateral it rates top-grade in its own published rating scale (Prime = A/AA-rated collateral only; a separate High Yield range accepts weaker collateral for more return; Smokehouse is its higher-risk range). On Morpho it runs Prime vaults at ~90% utilisation by design, meaning ~10% is always instantly withdrawable, with bots rebalancing continuously. Coinbase's USDC Prime vault (cbBTC collateral market at 86% LLTV) is Steakhouse-curated. Prime USDC held ~$437M and yielded 2.5–3.3% in Feb 2026 with zero reported bad debt through a 17% BTC fall. It also curates a ~$540M USDG vault on Robinhood Chain where the yield is partly subsidised by the issuer: distribution subsidy, not strategy genius.
Identity in one line: the Moody's of DeFi collateral, monetising trust and exchange distribution.

### Gauntlet — the simulation shop
Strategy: began as the firm protocols pay to set lending parameters using agent-based Monte Carlo simulation (see glossary). Its own products (via its Aera platform) go further than plain lending: **gtUSDa**, a vault-of-vaults that spreads USDC across Morpho markets on four chains scored by "probability of recovering capital plus interest"; a **levered Apollo ACRED** fund that loops a tokenised private-credit asset (supply it, borrow USDC, buy more of it, ~2.5× leverage turning an ~8% asset minus ~4% borrowing into ~10% net); **Osero Yield+**, the same looping trick on sUSDS (a yield-bearing dollar token), with leverage adjusted dynamically rather than fixed; and on Drift (a Solana perp exchange), **hJLP** (a hedged version of the exchange's liquidity-provider token, shorting crypto to cancel its price exposure) and a **SOL basis** fund.
Identity: if Steakhouse is a ratings agency, Gauntlet is an engineering firm that trusts simulations over ratings.

### Sentora — the strategy factory with distribution
Strategy: designs the strategy and the risk models (it claims 1,000+ risk models across 300+ strategies), then places the product inside someone else's front end. Flagship proof: Kraken's DeFi Earn. The BTC product is a **single-cycle supervised loan**: BTC is wrapped to kBTC on Kraken's chain, pledged in a vault, dollars are borrowed once against it (no recursive leverage), the dollars are deployed into lending, the profit is swapped back into BTC. $320M+, ~1.8% net in BTC, 25% performance fee, zero liquidations claimed. It also curates the main PYUSD (PayPal) and RLUSD (Ripple) vaults. Highest take rate among large curators (~11.7% of earnings).
Identity: KPK's closest rival, and the proof that distribution (an exchange's Earn button) beats cleverness.

### Re7 Labs — the yield hunter
Strategy: diversified, more aggressive DeFi yield, reaching for returns plain lending cannot give. Much of its fund-like product reaches investors through **Midas certificates** (mRe7YIELD; mBTC, a BTC-denominated market-neutral token). The cautionary tale: Re7 had ~$14.65M plus ~$12.75M of exposure to Stream Finance when it collapsed in Nov 2025 (an opaque offchain manager whose token was rehypothecated through the system). The strategy did not fail on its own maths; it failed on trusting another manager it could not see inside.
Identity: higher yield, wider hunting ground, and the scar tissue to show where the boundary is.

### MEV Capital — the arbitrageur
Strategy: market-neutral and arbitrage strategies (basis, spreads, relative value between venues), distributed partly through Midas as **mMEV** and **mBASIS** (a tokenised basis fund). Also took a ~$25.4M Stream exposure, same lesson as Re7.
Identity: a trading firm wearing a curator's clothes.

### Block Analitica — the hired risk department
Strategy: not really a fund manager. Its real job is being Sky's (the protocol behind DAI/USDS, formerly MakerDAO) risk team: it models Sky's enormous lending book and recommends parameters. Its own curator TVL is small (~$8.8M); its conservative Morpho flagship vaults accept blue-chip collateral only and allocate using an offchain model. 
Identity: proof that in this industry a risk mandate from a giant can matter more than your own product's size.

### Chaos Labs — the toolmaker, not a manager
Strategy: no funds or vaults at all. It sells risk infrastructure: simulation plus **risk oracles** that have automatically made 1,100+ parameter updates on Aave. It competes with curators for mandates and talent, not for deposits.
Identity: sells the shovels.

### Clearstar Labs — the liquidity-provision specialist
Strategy: automates **concentrated liquidity** management and related DeFi strategies (~$184M curated). Directly relevant to KPK's job ad, which asks for AMM basket modelling: Clearstar is what a firm looks like when CL management is the whole business.
Identity: the LP specialist.

### Upshift — the bridge to CeFi
Strategy: a vault platform (~$452M) whose differentiator is reaching centralised venues through August, a prime-brokerage stack: basis trades, OTC deals and institutional loans that pure onchain curators cannot touch. Publishes simple yield bands instead of promises: Cash 3–4%, Core 5–7%, Enhanced 8%+.
Identity: the only listed curator that natively straddles DeFi and CeFi.

### Veda — the rails, not the manager
Strategy: none of its own. Veda's BoringVault is infrastructure: a vault shell where a Manager can only make whitelisted calls, an Accountant prices the shares, a Teller handles deposits and withdrawals. Kraken Earn and ether.fi Liquid are built on it; the strategy belongs to the curator using the rails ($1.74B measured TVL, older claims of $3.5–4B).
Identity: the Shopify of vaults.

### ether.fi Liquid — the aggressive multi-strategy
Strategy: multi-strategy vaults on Veda's rails, run by a manager (Nonce Capital): Liquid ETH loops a restaking token in Aave E-mode at a health factor of about 1.03, a hair above seizure, dramatically more aggressive than KPK's published posture. Its older marketing quoted ~24.6% APYs that clean listings put nearer 4% — the standard example of why backfilled APYs must never enter a backtest.
Identity: the yield maximiser, useful mainly as the contrast that makes KPK's discipline legible.

### Ethena — the delta-neutral giant
Strategy: USDe, the synthetic dollar. Hold staked ETH and BTC, short an equal value of perpetual futures, and the price risk cancels; what remains is staking yield plus funding payments from perp longs. That income funds sUSDe's yield. Its disclosed BTC funding income fell from 11.0% (2024) to 4.9% (2025) to 2.2% (2026 YTD): the cleanest public proof that carry income compresses.
Identity: not a curator, but the largest living example of the basis/funding strategy class, and the reason every carry pitch in 2026 must answer "what happens when funding dies?"

### Maple / Syrup — the private-credit lender
Strategy: real institutional borrowers, real loans, at 148–174% overcollateralisation, 6–7.8% loan rates; syrupUSDC (~$2.78B, ~4.8%) is the ERC-4626 wrapper retail deposits into. Maple lost $50M+ in its earlier uncollateralised era (2022) and rebuilt entirely on secured lending, claiming zero losses since.
Identity: the closest thing onchain to an actual credit fund.

### K3 Capital — the measurement lesson
Claims >$570M of delta-neutral portfolios managed; measured curator TVL was ~$219M on 4 Nov 2026 and falling. Both numbers circulate. The gap between self-reported and independently measured size is itself a due-diligence skill.

### KPK, placed on the same map
Framework-first curator (150+ checks, worst-dimension rating, tiers per market) now building tokenised multi-strategy Funds (Carry funds, Alpha funds, AMM funds on Robinhood Chain) on its own Safe/Zodiac infrastructure with deterministic agents. Smaller than Sentora/Steakhouse/Gauntlet today; its opening is funds and denominations the giants have not occupied yet, BTC above all.

## The one-paragraph summary to carry in your head
Every curator sells the same underlying thing, safety rules for other people's money, and differentiates on one axis: Steakhouse on ratings, Gauntlet on simulation, Sentora on strategy-plus-distribution, Re7/MEV on hunting yield and arbitrage (with Stream scars), Block Analitica and Chaos on being hired risk brains, Clearstar on LP automation, Upshift on CeFi access, Maple on real credit, Ethena on owning one giant delta-neutral trade. KPK's axis is framework plus its new funds layer. Any strategy you design for the PoW should be answerable in one sentence against this map: "who already does this, and what is different about mine?"
