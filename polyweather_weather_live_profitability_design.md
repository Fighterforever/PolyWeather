# PolyWeather Weather Market 实盘准备与盈利化设计说明

## 核心结论

当前系统不能实盘。项目值得继续，但当前最大瓶颈不是 readiness 分数低，而是还没有形成“官方结算规则结构化、站点级天气概率分布、真实可成交盘口、已结算验证、执行证据”这一条完整盈利闭环。现在的系统更像 paper evidence orchestration，而不是已经证明有 alpha 的 trading bot。

## 真正的 alpha 来源

1. **station-level calibration**：不是预测城市天气，而是预测市场官方结算站点、官方日期、官方指标、官方 rounding rule 下的最终结算值。
2. **threshold CDF mispricing**：`le/ge` bucket 应该用校准后的分布函数比较市场隐含 CDF，而不是用 raw point forecast。
3. **forecast update latency**：GFS、HRRR、NWS、METAR、Open-Meteo 等数据更新后，盘口可能滞后。
4. **near-lock**：临近结算时，概率问题逐渐变成观测与结算规则问题。
5. **maker spread capture**：只有在能证明成交后 markout 不被 adverse selection 吞掉时才有价值。

## 当前系统主要问题

### 数据层

缺少 canonical settlement spec。每个市场必须被解析为结构化对象，至少包含 platform、market id、token id、city、station、source、local date、timezone、metric、unit、bucket type、threshold、rounding rule、resolution rule hash、end time。没有这个对象，不能进入策略层。

### 模型层

当前模型更像把已有 PolyWeather 分析结果包装成 trading rows，不是独立的 station-calibrated probability distribution。`eq` bucket 直接依赖离散分布的精确点质量，风险很高。

### 信号层

当前 `edge_percent = model_probability - market_price` 过于粗糙。信号必须改成：

```text
ev_safe = p_lcb - q_effective - cost
```

其中 `p_lcb` 是校准概率的置信下界，`q_effective` 是真实可成交价格，`cost` 包括 spread、slippage、fees、latency、settlement risk。

### 执行层

taker paper markout 已经显示 broad current-signal taker 为负，这是不能实盘的强证据。maker inferred fill 目前只是 diagnostic，不足以证明真实成交，因为没有 queue position、touch-through path、order lifecycle。

### 风控层

readiness 不应该是 0-100 分凑分制，而应该是 hard gates + evidence ledger，并按 strategy、station、bucket type、execution style 分开判断。

## 最终系统形态

1. **Market Truth**：解析 Polymarket/Kalshi 市场为 `SettlementSpec` 和 `MarketBucket`。
2. **Weather Truth**：存 forecast snapshots、observation snapshots、official settlement values，所有数据带 `available_at`，禁止 lookahead。
3. **Calibrated Distribution**：对 station/date 输出温度最终结算值的概率分布，支持 `P(Y<=T)`、`P(Y>=T)`、`P(round(Y)=T)`。
4. **Market-Implied Distribution**：对互斥 bucket 做 de-vig，对 threshold surface 做 monotonic CDF fit。
5. **Strategy Layer**：拆为 `tail_threshold`、`near_lock`、`maker_passive`、`taker_update`。`eq_exact` 只保留 shadow calibration。
6. **Execution Layer**：taker 用 orderbook depth walk 计算有效成交价；maker 记录 quote lifecycle、touch-through、missed fill、adverse selection。
7. **Risk Layer**：fractional Kelly + 强 haircut + per-market/city/station/date/platform/strategy limits + kill switch。
8. **Evaluation Layer**：按 strategy/station/bucket/horizon 输出 resolved PnL、markout、Brier score、log loss、calibration、drawdown、capacity。

## 最高优先级任务链

### P0：继续强制 paper-only

涉及：`scripts/weather_market_paper_cycle.py`、`src/trading/weather_live_readiness.py`

做法：保留 `live_order_path=false`；live permission 与 evidence gate 分开；任何 live adapter 未完成前不允许实盘。

验收：单测证明任何 CLI 参数都不能触发真实下单。

### P1：新增 canonical settlement spec

涉及：新增 `src/trading/weather_market_catalog.py`，修改 `weather_market_enrichment.py`、`polymarket_readonly.py`

做法：解析 temperature markets 为 `SettlementSpec`；解析失败时输出明确 unsupported reason。

验收：没有 station/source/date/rule 的市场不能成为 candidate。

### P1：补 resolved market truth

涉及：`weather_closed_backfill.py`、`weather_resolved_audit.py`

做法：closed weather markets 保存 outcome、winning token、rule text、resolution source、official final value。

验收：已结算市场能按 token_id 重算 payout 与 PnL。

### P1：采集 orderbook 历史

涉及：新增 `src/trading/polymarket_orderbook_archive.py`

做法：按 token 保存 best bid/ask、depth ladder、spread、timestamp、latency。

验收：任一 paper fill 能回放入场前后盘口并计算 taker effective price、maker touch、missed fill。

### P1：建立 station-level weather store

涉及：新增 `src/weather/station_registry.py`、`weather_sources.py`、`weather_observations.py`

做法：绑定城市、机场站、官方源、时区；接 Open-Meteo、NWS、METAR、HRRR。

验收：数据全部带 `available_at`，回放测试无未来数据泄漏。

### P2：替换 raw edge

涉及：新增 `weather_probability_model.py`，修改 signal/enrichment

做法：输出 calibrated distribution、`p_model`、`p_lcb`、`q_effective`、`cost`、`ev_safe`。

验收：strict candidate 必须 `ev_safe > 0`，没有校准分布不能进入 strict candidate。

### P2：market-implied distribution / de-vig

涉及：新增 `weather_market_implied.py`

做法：同一 event 的互斥 bucket 归一化；threshold bucket 做 monotonic CDF。

验收：报告 overround、underround、monotonic violation。

### P2：拆策略层

涉及：新增 `src/trading/weather_strategies/`

做法：实现 `tail_threshold`、`near_lock`、`maker_passive`、`taker_update`；每个 candidate 有唯一 `strategy_id`。

验收：readiness 可按 strategy 单独输出。

### P2：no-lookahead replay

涉及：新增 `weather_replay.py`、`weather_execution_sim.py`

做法：用历史 forecast、orderbook、closed market、observation 重建当时信号并模拟成交。

验收：报告 resolved PnL、markout、missed fill、fill rate、drawdown；单测覆盖 no-lookahead。

### P3：重写 readiness

涉及：`weather_live_readiness.py`

做法：改成 hard gates + evidence ledger，不再用单一 0-100 分。

验收：输出哪些策略可 tiny-live，哪些只能 paper，以及缺什么证据。

## 删除、合并、降级建议

1. `eq_exact` 降级为 shadow calibration，不能进入 live candidate。
2. `final_score = edge_percent * 10` 降级为展示字段，不可做交易依据。
3. quarantine、targeted shadow、quality surface、maker focus 合并为统一 `EvidenceStratifier`。
4. 小样本 markout risk rules 降级为 diagnostics，不能自动封杀或放行。
5. maker inferred fill 降级为 diagnostic-only，直到有连续盘口和 quote lifecycle。
6. 非 temperature weather family 暂时只做 catalog scan，不进入 live readiness。

## 实盘前硬门槛

1. settlement spec 完整。
2. resolved audit 能重建 payout/PnL。
3. no-lookahead replay 扣成本后为正。
4. forward paper 按 strategy 为正。
5. resolved PnL 为正，而不只是 markout 为正。
6. station-level calibration 指标过关，包括 Brier score、log loss、calibration curve。
7. execution simulator 与真实盘口一致。
8. 风控、kill switch、数据异常检测完成。
9. 平台权限、地区限制、费用参数、API policy 显式检查。
