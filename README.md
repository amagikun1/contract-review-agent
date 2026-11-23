# 契明 · 基于 LangGraph + RAG 的智能合同审批审查系统

学生实战 MVP：上传合同 → 解析/OCR → 向量检索与规则工具 → Qwen 审查 → 原文证据校验 → 人工复核 → 模拟审批 → Markdown 报告。

## 快速启动（Windows / PowerShell）

要求 Python 3.11+、Node.js 20.19+。在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

脚本将 Python 虚拟环境放在项目同级的 `contract-review-runtime/venv`，安装前后端依赖，并在不存在时从 `.env.example` 创建 `.env`。

分别在两个终端启动：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-frontend.ps1
```

前端：http://127.0.0.1:5173 ；后端文档：http://127.0.0.1:8000/docs 。后端必须单进程启动，不添加 `--workers`，因为任务队列和 SSE 事件保存在当前进程。

其他系统手动安装：

```bash
python -m venv ../contract-review-runtime/venv
source ../contract-review-runtime/venv/bin/activate
pip install -r backend/requirements.lock.txt
cp .env.example .env
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
# 另一个终端
cd frontend
npm ci
npm run dev
```

## Qwen 配置

本机开发过程中已经通过安全输入配置了百炼 Key。提交包不包含该 Key；换机器后在根目录 `.env` 填写：

```dotenv
DASHSCOPE_API_KEY=填写你自己的百炼APIKey
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

这是百炼中国内地 OpenAI 兼容接口；使用其他地域或套餐时，将 `QWEN_BASE_URL` 改为账户对应地址。更改后重启后端。参考[百炼官方兼容接口文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。

验证：`Invoke-RestMethod http://127.0.0.1:8000/api/health` 中 `llm_configured=true` 仅代表已填写；上传样例并完成审查，结果显示 Qwen 模式和实际工具调用记录，才代表真实调用成功。未填写时执行明确标识的离线规则模式；填写了无效 Key 则显示审查失败，不自动伪装成功。

## 演示流程（约 5 分钟）

1. 打开合同工作台，上传 `samples/generated/purchase_contract.pdf`。
2. 点击“开始审查”，观察解析、检索、规则工具、Qwen、证据校验的进度。通常需要几十秒至数分钟，取决于模型网络和返回长度。
3. 查看左右分栏。点击风险原文，左侧解析文本定位并高亮；缺失条款会提示人工核实。
4. 打开“条款与依据”，查看主体、金额及法律/内部规则的区别，点击官方来源核验。
5. 打开“执行记录”，展示真实 `search_knowledge`、`check_contract_rules` 调用。Qwen 会至少发起一次检索工具请求，最多 3 轮工具决策。
6. 填写人工意见，例如“建议改为分期付款，完善质保及解除结算；本次仅做教学模拟审批”，点击“确认并保存意见”。
7. 点击“提交模拟审批”，查看 `SIM-...` 审批编号和本地回写结果。审批后不允许覆盖原审查；需变更时上传新版本。
8. 点击“查看报告”和“下载报告”。报告包含风险、原文、依据、人工意见、审批状态及工具记录。
9. 可上传另一份合同，演示“驳回 → 重新审查”。重审清除旧的意见及审批数据。
10. 上传 `purchase_contract_scan.pdf` 或 `purchase_contract_scan_page1.png` 演示 OCR。

全部样例均为虚构数据。采购、销售、服务三份 TXT 原稿及 15 个衍生格式位于 `samples/`。重新生成：

```powershell
..\contract-review-runtime\venv\Scripts\python.exe scripts/generate_samples.py --strict
```

## 结构与学习要点

```text
backend/app/
  main.py          HTTP 接口、任务执行、SSE、人工状态转换
  workflow.py      StateGraph、ToolNode、Qwen 循环、证据验证、报告
  rag.py           文档切分、TF-IDF 向量化、余弦相似度检索
  knowledge.json   可审阅的法律摘要和内部规则
  rules.py         条款字段识别和确定性风险规则
  parser.py        PDF/DOCX/TXT/图片及 RapidOCR
  repository.py    SQLite 持久化
  models.py        Pydantic 入参模型
frontend/src/      React + TypeScript 页面与样式
backend/tests/     数据层、解析、工作流与接口测试
samples/           虚构合同与多格式样例
docs/              测试记录、交付说明、截图、演示报告
scripts/           安装、启动、样例生成、打包
```

**LangGraph**：`parse → identify → retrieve → tools → 条件路由`。已配置 Key 则进入 `agent ↔ tools` 循环，否则进入 `offline`；最后 `validate → report → END`。消息通过 `add_messages` 管理，`ToolNode` 执行真实 LangChain 工具。参考[LangGraph 官方工作流与 Agent 文档](https://docs.langchain.com/oss/python/langgraph/workflows-agents)。

**RAG**：内置小知识库按 300 字窗口、240 字步长切分，中文字符 1–3 gram TF-IDF 转为稀疏向量，余弦相似度返回相关片段。它是轻量向量检索，不是 BGE-M3 语义嵌入，也没有接 Milvus。语义召回能力有限，但完整展示了切分、向量化、检索和增强生成链路。

**证据校验**：模型引文必须对应原文子串，仅容忍 PDF 换行等空白差异；引用编号必须来自工具结果。自由文本中未经验证的法律引文/效力断言被过滤，法律依据统一由已核对的知识卡片展示。规则发现不会因模型遗漏消失。缺失条款没有伪造引文，须人工确认。仍不能保证模型推理或“缺失”判断绝对正确。

**持久化与复核**：SQLite 保存合同、结果、最终人工意见和模拟审批；本地目录存文件。人工复核是 API 状态转换，没有实现 LangGraph interrupt/resume 或多角色权限。ThreadPoolExecutor 仅用于本地后台任务，不需要 Redis/Celery。

法律摘要核对来源：[工信部天津市通信管理局民法典全文](https://tjca.miit.gov.cn/zwgk/zcwj/flfg/art/2020/art_20cf1a2e1b854924b5caa744c8045d1f.html1)，第470条和第585条，2026-11-22 核对。内部规则明确标记为教学规则，不冒充法律要求。

## 测试与提交

```powershell
..\contract-review-runtime\venv\Scripts\python.exe -m pytest -q -rs
npm run build --prefix frontend
..\contract-review-runtime\venv\Scripts\python.exe scripts/package_project.py
```

测试使用临时数据库和明确标记的脚本模型验证工具循环；真实 Qwen、OCR 和浏览器交互的验证结果另见 `docs/TESTING.md`。当前完整后端测试为 **58 passed, 1 skipped**（跳过的是“缺少 OCR 依赖时的错误提示”用例，因为本机已安装 RapidOCR），逐项覆盖见 `docs/TESTING.md`；交付内容与边界见 `docs/DELIVERY.md`。`backend/requirements.lock.txt` 与 `frontend/package-lock.json` 固定本次验证的依赖版本。

打包脚本使用白名单，仅包含代码、依赖清单、样例与测试资料；排除 `.env`、`data`、虚拟环境、`node_modules`、构建目录与缓存。输出到项目同级 `contract-review-delivery.zip`。

## 明确边界

- 尚未实现 PDF 审查报告导出；已支持 Markdown 查看/下载。
- 使用 SQLite/本地文件与 TF-IDF，没有连接 MongoDB、MinIO、Milvus 或 BGE-M3。
- 上传最大 20 MB；审查正文最大 60000 字符；PDF 最多解析前 50 页并给出警告。OCR 为 CPU 模式，复杂表格、印章、低清照片可能识别不准。
- DOCX 提取正文段落和表格，不解析页眉页脚、文本框、图片；其页码不是 Word 的真实分页。PDF 原版坐标定位未实现，使用解析文本高亮。
- 条款基础字段先用规则提取，金额列表可能含单价/总价/违约金，需人工核实。
- 不含用户权限、多租户和外部企业审批；仅适合本地教学演示。
- 当前目录未提供老师的《项目实战.md》，按本次用户给出的 2.4 需求实现。
- 前端、架构、LangGraph/RAG 与最终集成由主代理完成；解析器、存储、模型、样例、脚本及后端测试由实现子代理完成，主代理已审查和联调。本轮仅对普通后端契约做兼容性审查，未改动 `workflow.py`、`rag.py`、`rules.py`、`knowledge.json` 或前端源码。
