# Stock Tracking & Automated Trading System

A personal stock-tracking and portfolio-management system for monitoring manually selected stocks, executing a predefined pyramiding strategy, managing risk, and analysing performance over time.

## Strategy

For each selected stock:

* Start with an initial position.
* Add predefined tranches when the stock moves upward by fixed percentage intervals.
* If the stock declines, deploy additional tranches at predefined downside levels.
* Limit the maximum loss per stock with a hard stop-loss.
* Once the trade reaches a predefined profit threshold, activate a trailing stop-loss based on the peak price.
* Automatically place and manage orders based on the strategy state.

The strategy parameters should be configurable rather than hardcoded.

## Broker & Market Data

Initially targeting **Zerodha**, with **FYERS** as a potential alternative for market data.

The system will explore broker APIs/MCP capabilities for:

* Live market data
* Historical data
* Order placement
* Order tracking
* Positions and holdings
* P&L and trade history

Market data will be implemented as a **separate microservice**, independent of the trading application, allowing different data providers to be swapped without changing the strategy engine.

## Architecture

```text
Trading App
     │
     ▼
Strategy & Risk Engine
     │
     ├── Market Data Service
     │        ├── Zerodha
     │        └── FYERS
     │
     └── Broker Integration
              └── Order Execution
```

## Portfolio Analytics

Store historical orders, trades, positions, P&L, and portfolio performance to analyse:

* Strategy performance
* Drawdowns
* Winning/losing trades
* Performance by stock and tranche
* Portfolio performance over time

## LLM Integration

Use historical portfolio and trading data to help an LLM analyse performance and identify potential improvements to the strategy.

Any strategy changes should first go through **backtesting and/or paper trading** before being considered for live execution.

## Goals

* [ ] Portfolio tracking
* [ ] Market data microservice
* [ ] Configurable strategy engine
* [ ] Risk management
* [ ] Broker integration
* [ ] Automated order management
* [ ] Historical performance analytics
* [ ] LLM-assisted strategy analysis
* [ ] Backtesting and paper trading
