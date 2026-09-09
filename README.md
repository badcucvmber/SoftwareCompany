---
title: MetaGPT
emoji: 🐼
colorFrom: green
colorTo: blue
sdk: docker
app_file: app.py
pinned: false
---

# MetaGPT Web UI

基于 MetaGPT 定制的多智能体软件协作生产系统（SoftwareCompany 版）。

- **AHP 分派**：通过层次分析法 + Tfidf 从 EngineerRD / UI / PK 三类工程师中选择任务承接角色
- **RTSM 绩效评估**：基于历史绩效的共生度模型校正分派权重
- **实时协作**：FastAPI + SSE 推送任务流转，产出放行至 workspace

## 快速开始

```bash
pip install -r requirements.txt
python backend/app.py   # 默认 uvicorn 33210
```

浏览器打开 http://localhost:33210 （主界面）。

## 多智能体协作 Demo

项目内置一份独立演示页，展示工程师团队、绩效趋势、任务分布与协作会话时间线，
数据来自 `performance.db`，无需配置 API Key 即可浏览：

打开 http://localhost:33210/demo.html

源码位于 `static/demo.html`，数据快照 `static/demo_data.json`（可由
`performance.db` 重新生成）。
