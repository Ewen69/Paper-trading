# Operations Center (station 1)

The default screen: a dense, dark telemetry dashboard fed live by `/ws/telemetry`. The 3D
"City" view was removed, along with three.js and react-three-fiber.

| Panel | What it shows | Source |
|---|---|---|
| Status strip | stream, optimizer, paper runner, risk guard, Auditor, Graduation Gate | heartbeats, risk state, Auditor |
| Agent terminal | every daemon log line and every trial as it happens, filterable | `daemon_log`, `agent_learning_log` |
| Learning agent | current target, generation, latest trial, budget used, trial counts, the Active Best and why | optimizer heartbeat, learning log |
| Active Best equity curve | in-sample and out-of-sample backtest curves vs the benchmark, after costs | backtest replay of runs already in the log |
| Paper runner | equity, cash, today's P&L, positions, risk-limit utilization bars, recent orders with quote, fill and slippage | runner heartbeat, paper log |
| Net worth | history chart | your entries |
| Portfolio allocation | value by asset class | your holdings |
| Auditor | flags and Graduation Gate progress | Auditor, Graduation Gate |
| Manual controls | kill switch (reason required), data check, manual in-sample sweeps, one paper cycle (dry run by default) | REST API |

**Missing data is labeled, never invented:**

- A daemon that isn't running shows "not running" or "no heartbeat".
- A panel without data says "no data" and why.
- Invalid stream messages are counted and ignored.
- If the stream drops, the page says "offline" and reconnects.
