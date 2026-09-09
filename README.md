---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: a9514ee67bbdac48c730854af5d9bbfe_fb52809aac1411f18874525400287e28
    ReservedCode1: sKVjinW33pyuf86exMOZdofZBR67Vg7jJzG2zycdgWwUJ/LsLSViIJ2IeTd5rjMxJTqTMwRl20wg1NwKeSxrDou1smzyQ4lwYnC+UbNat2c9dUe7+KulLDgrxZn44tLV0w+xwHAiUrA4tHvJB0txyFwa94clxeGtdPhrLtdMwhWRHyQkJ4TsLjw9xT0=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: a9514ee67bbdac48c730854af5d9bbfe_fb52809aac1411f18874525400287e28
    ReservedCode2: sKVjinW33pyuf86exMOZdofZBR67Vg7jJzG2zycdgWwUJ/LsLSViIJ2IeTd5rjMxJTqTMwRl20wg1NwKeSxrDou1smzyQ4lwYnC+UbNat2c9dUe7+KulLDgrxZn44tLV0w+xwHAiUrA4tHvJB0txyFwa94clxeGtdPhrLtdMwhWRHyQkJ4TsLjw9xT0=
---

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
*（内容由AI生成，仅供参考）*


## 零配置演示工作台

无需任何 API Key、无需 MetaGPT，即可体验完整的多智能体协作交互：

```bash
pip install -r requirements.txt          # 仅需 fastapi / uvicorn
cd 项目根目录                             # 关键：必须从根目录运行
python backend/mock_app.py               # 启动 mock 后端,端口 8000
```

打开 http://localhost:8000/ 即为可交互工作台：

- **工程师调度**：选择工程师生成代码/图表/习题，实时展示分派决策与 SSE 进度流
- **并行任务演示**：输入含"需求 1"（2 任务并行）或"需求 3"（3 任务并行）的句子，可看到多智能体并行拆解-执行-汇总全过程
- **绩效看板**：每次任务完成实时更新 avg_score / total_tasks 曲线

> 前端依赖（Vue3 / marked / highlight.js）已全部本地化至 `static/vendor/`，无外网也能完整渲染。
> 若 8000 端口被占用：`python -m uvicorn backend.mock_app:app --port 8001 --host 0.0.0.0`
>
> 纯静态脱机版（无需 Python）：`python -m http.server 8080`（在根目录执行）后访问 `/demo.html`。
