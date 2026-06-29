# 2026-06-29: ml accuracy-calibrated sentiment mapping (v4.0)

> 论证方法: c1skill 8 阶段闭环（3 学科 5 著作跨学科证据）
> 所有阶段输出: `docs/decisions/accuracy-calibrated-mapping.md`
> c1skill 轨迹: `data/traces/2026-06-29_003.json`

---

## 变更概述

将 ml (MindLynx) 的 `normalize_mindlynx_score` 从 3 值对称映射升级为基于 598 样本回测精度的 7 值非对称映射。

## 背景

回测数据显示 ML 的 sentiment_score 方向准确率存在极端不对称：

| 区间 | 方向 | 准确率 | 样本(N) |
|------|------|:-----:|:-------:|
| 0-19 | 看空 | 100.0% | 9 |
| 20-30 | 看空 | 89.0% | 93 |
| 31-40 | 看空 | 92.8% | bulk |
| 41-48 | 看空 | 92.8% | 351 |
| 49-51 | 中性 | 0.0% | 10 |
| 52-59 | 看多 | 56.2% | 76 |
| 60-79 | 看多 | 38.2% | 59 |
| ≥80 | 看多 | extrap | — |

原始映射用对称的 ±1.5 处理所有信号，系统性高估低精度看多信号、低估高精度看空信号。

## 映射变更

| Sentiment | 旧 L7 | 新 L7 | 说明 |
|:---------:|:----:|:----:|------|
| 0-19 | -1.5 | **-3.0** | S7, 100% acc |
| 20-30 | -1.5 | **-2.5** | S6↑, 89% acc |
| 31-40 | -1.5 | **-2.0** | S6, 92.8% acc |
| 41-48 | -1.5 | -1.5 | S5, **保留最强信号段** |
| 49-51 | 0.0 | 0.0 | 严格中性 |
| 52-59 | +1.5 | **+0.8** | S4+, **保守降低** |
| 60-79 | +1.5 | **+1.0** | S3, **阻尼** |
| ≥80 | +1.5 | +1.5 | S2-, extrapolated |

## 跨学科理论支撑

| 学科 | 著作 | 核心论点 |
|------|------|---------|
| 量化组合管理 | Grinold & Kahn *APM* | 精度(IC) > 粒度(BR), IR=IC×√BR |
| 量化组合管理 | Carver *Systematic Trading* | 连续信号 > 离散, 统一标准化 |
| 量化 ML | López de Prado *AFML* | 元标签: 不对称精度→不对称映射 |
| 行为/认知科学 | Kahneman *TFS* | WYSIATI: 分类跳跃丢失梯度信息 |
| 行为/认知科学 | Taleb *Black Swan* | 降维=丢失尾部信息 |
| 多智能体 | Wooldridge *MAS* | Arrow定理: 不同粒度偏好聚合必有缺陷 |

## 涉及文件

| 文件 | 改动 |
|------|------|
| `src/normalizer.py:260-284` | `normalize_mindlynx_score` 替换 |
| `tests/test_normalizer.py:99-105` | 测试标签更新 |
| `docs/decisions/accuracy-calibrated-mapping.md` | 新建: 完整论证记录 |

## 验证

- 68/68 pytest 通过 (非我的改动引起的 3 个预存故障除外)
- 无样本翻转方向
- 最大融合位移 ±0.20 L7 (well within noise)
- 风险等级: LOW
