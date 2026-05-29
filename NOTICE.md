# NOTICE

This project incorporates three subsystems that are **heavily customized private forks** of the following open-source projects. Each retains its original license.

## 1. lynx_vnpy
- **Upstream**: [vnpy/vnpy](https://github.com/vnpy/vnpy) (MIT License)
- **Copyright**: Copyright (c) 2015-present, Xiaoyou Chen
- **Customizations**: Signal engine replaced with RandomForest model; stripped UI layer; custom A-share data pipeline via Sina Finance API.
- **Our fork location**: `systems/lynx_vnpy/`

## 2. MindLynx-Aistock
- **Upstream**: [MindLynx-Aistock](https://github.com/Mindlx/MindLynx-Aistock) (MIT License)
- **Copyright**: Copyright (c) 2025 ZhuLinsen, Copyright (c) 2026 blue-aistock contributors
- **Customizations**: Stock pool, LLM provider configuration, strategy parameters tailored to personal watchlist.
- **Our fork location**: `systems/MindLynx-Aistock/`

## 3. mind_TradingAgent
- **Upstream**: [TradingAgents-CN](https://github.com/TradingAgents-CN) (Apache License 2.0)
- **Copyright**: Original project contributors
- **Customizations**: Adapted for Chinese A-share data via yfinance (.SS/.SZ suffixes); configured for DeepSeek LLM provider; stock watchlist synchronized with MindLynx.
- **Our fork location**: `systems/mind_TradingAgent/`

## Fusion Layer
The ensemble engine (`src/`, `scripts/`, `config/`) is original work.  
**License**: MIT License (see LICENSE file).

---

*This project is for educational and research purposes only.  
Trading stocks involves substantial risk. No investment advice is implied.*
