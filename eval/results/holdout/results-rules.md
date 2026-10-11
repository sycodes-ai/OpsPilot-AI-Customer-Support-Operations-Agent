# Evaluation results (rules mode)

Date: 2026-10-10 · Cases: 12 · **Passed: 9/12 (75%)**

| Check | Accuracy |
|---|---|
| Answer content | 80% |
| Cited correct source | 100% |
| Guardrail decision | 50% |
| Intent detection | 90% |
| Tool outcome | 75% |
| Tool selection | 75% |

| Category | Passed |
|---|---|
| escalation | 0/1 |
| guardrails | 1/1 |
| inventory | 1/2 |
| order_status | 2/2 |
| policy_rag | 4/4 |
| returns | 1/2 |

Latency per turn: p50 0.3 ms, p95 3.2 ms.

## Failures

- **H06** (inventory): failed intent, tool_selection, content. Reply: "Headlamp 300 (NB-LMP-01) gives 300 lumens and is USB rechargeable. Thermal Gloves (NB-GLV-01), Sun Trek Hat (NB-HAT-01) and Trekking Poles (NB-PLE-01) complete "
- **H09** (returns): failed tool_selection, tool_result, content. Reply: "What is the reason for returning order OP-1010? (For example: too small, defective, changed my mind.)"
- **H11** (escalation): failed tool_selection, guardrail. Reply: "Human support is available Monday to Friday, 9:00 to 18:00 KST. The support assistant answers questions 24 hours a day and hands over to a human when needed. (S"
