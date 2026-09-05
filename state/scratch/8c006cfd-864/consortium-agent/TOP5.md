# Top-5 Anchors — rationale tied to the consortium reserve agent

The five categories, five picks each. Picked **not** for hype but for **fit to a
stablecoin reserve shared by 140+ partners across Base/Ethereum/Solana/Tempo,
settled by an agent that must be auditable, budgeted, and quantum-safe.**

## Blockchains (settlement + observation rails)
| # | Chain | Why it's in |
|---|---|---|
| 1 | **Base** | OUSD/Yearn-Drop primary settlement domain; cheap, fast, stablecoin-dense. |
| 2 | **Ethereum** | Root of trust; most liquid yield legs (Aave/Lido) and deepest CCTP. |
| 3 | **Solana** | High-throughput settlement; USDC-native, cheap partner payouts. |
| 4 | **Arbitrum** | L2 depth for reserve allocation + low-cost cross-leg accounting. |
| 5 | **Tempo** | The consortium's stated expansion chain — included as the frontier rail. |

## Stablecoins (the reserve + payout asset)
| # | Asset | Why it's in |
|---|---|---|
| 1 | **USDC** | Regulatory-compliant, CCTP-native, the disclosure-grade reserve asset. |
| 2 | **USDT** | Highest volume; must be tracked/attributed even if not the base reserve. |
| 3 | **DAI** | On-chain, transparent supply; cleanest for programmatic attribution. |
| 4 | **PYUSD** | Compliance-first, payment-oriented — fits a payout-to-partners flow. |
| 5 | **OUSD** | The consortium's own yield-bearing reserve token; the whole point. |

## Cryptos (native-value / collateral legs)
| # | Asset | Why it's in |
|---|---|---|
| 1 | **ETH** | The reserve's risk/yield anchor (staked via Lido). |
| 2 | **BTC** | The standard "store-of-value" collateral leg for a diversified reserve. |
| 3 | **SOL** | High-yield collateral on Solana yield legs. |
| 4 | **WETH** | The settlement/collateral instrument across DEX + lending legs. |
| 5 | **cbBTC** | Coinbase's custody-backed BTC — compliance-grade, auditable supply. |

## Protocols (yield + movement + settlement)
| # | Protocol | Role in the agent |
|---|---|---|
| 1 | **OUSD / Yearn-Drop** | The reserve itself; where yield is generated. |
| 2 | **Circle CCTP** | Native cross-chain token movement (the "bridge" the use-case wants). |
| 3 | **Aave / Lido** | The yield legs the reserve is allocated to. |
| 4 | **Circle Paymaster / x402** | Metered, agentic settlement (this is `metered-web-broker`). |
| 5 | **OpenPaymaster** | Open metered-settlement standard — the open-source anchor. |

## Standards (the "standards" the agent conforms to — the real moat)
| # | Standard | Why it's load-bearing |
|---|---|---|
| 1 | **FIPS 203 / 204 / 205** (ML-KEM / ML-DSA / ML-EdDSA) | **Quantum-safe.** The PQC anchor for agent identity + delegation; drives the "harvest-now-decrypt-later" reserve-key ranking. |
| 2 | **ERC-4626** | Uniform vault accounting — the correct way to attribute yield *per share/partner*. |
| 3 | **ERC-1643** | RWA on-chain registry — the disclosure/audit surface for reserve assets. |
| 4 | **CycloneDX + RSL** | Sentinel's CBOM + metered's license/obligation format — the **audit-grade disclosure** shape. |
| 5 | **x402 / AP2** | The metered-settlement wire (HTTP 402 + payment signature) — the settlement contract the broker already implements. |

### What this selection means
- **No single "best chain/asset."** The agent is **protocol-agnostic by design**
  (metered's `PaymentRail ×5`), so the Top-5 is a *configuration*, not a bet.
  Swap Tempo→a new chain and only the rail registry changes.
- **PQC is a standard, not a feature.** FIPS 203/204/205 sits *inside* the
  standards list because it's the constraint the whole trust layer must migrate
  toward — it's the difference between an agent that looks quantum-safe and one
  that is auditably quantum-safe.
- **Standards are the moat.** Chains and assets are commodities; **conforming to
  FIPS + ERC-4626/1643 + CycloneDX + x402** is what makes the agent's output
  consumable by a regulator, a partner, and another chain — toy vs. shippable.
