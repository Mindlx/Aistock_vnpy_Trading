## 1. 安装依赖

- [ ] 1.1 `pip install alphasift` 安装选股引擎
- [ ] 1.2 验证 `from alphasift import screen` 可正常导入

## 2. API 层

- [ ] 2.1 新建 `api/__init__.py` 包结构
- [ ] 2.2 新建 `api/v1/endpoints/alphasift.py` — 选股 API（策略列表/执行/状态）
- [ ] 2.3 新建 `api/v1/endpoints/hotspots.py` — 热点题材 API

## 3. 前端页面

- [ ] 3.1 新建 `frontend/src/pages/StockScreeningPage.tsx` — 选股页面
- [ ] 3.2 新建 `frontend/src/pages/HotspotsPage.tsx` — 热点看板
- [ ] 3.3 注册路由到 `App.tsx`

## 4. 验证

- [ ] 4.1 测试选股 API 返回正常
- [ ] 4.2 确认前端页面可正常加载
