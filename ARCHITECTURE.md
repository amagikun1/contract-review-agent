# 合同审查 MVP 接口与分工

技术：FastAPI、SQLite、本地上传目录、LangGraph、LangChain Tool、Qwen OpenAI-compatible API、中文字符 n-gram TF-IDF 向量检索、React/TypeScript/Vite。

本轮代码归属：主代理负责 `frontend/`、`backend/app/workflow.py`、`rag.py`、`rules.py`、`knowledge.json`、最终截图与演示报告；实现子代理负责 `backend/app/main.py`、`models.py`、`repository.py`、`parser.py` 的兼容性审查，`backend/tests/`、`scripts/`、`README.md`、`ARCHITECTURE.md`、`docs/TESTING.md`、`docs/DELIVERY.md`。`.env`、`data/`、虚拟环境与依赖目录不进入交付包；Agent 核心本轮只读，必要问题交回主代理。

## Python 契约
- parser.parse_document(path: Path) -> dict：text: str、pages: list[{page:int,text:str}]、method: str、warnings: list[str]。支持 txt/md/docx/pdf/png/jpg/jpeg，扫描件 RapidOCR（可选，失败提供明确异常）。限制空文本，异常 ValueError。
- repository.Repository(db_path: str|Path)：init 自动建表。create_contract(filename: str, stored_path: str) -> dict；list_contracts() -> list[dict]；get_contract(id: str) -> dict|None；update_contract(id: str, **fields) -> dict（不存在抛 KeyError）。SQLite，单次独立连接，JSON 字段保存。
- contract 固定字段：id,filename,stored_path,created_at,updated_at,status（uploaded/reviewing/pending_review/confirmed/rejected/approved/failed）,text（默认空字符串）,pages（[]）,analysis（{}）,review_note（空字符串）,approval（{}）,error（空字符串）。update 只接受以上可变字段，禁止 id 和 created_at。
- models.py 提供 ReviewInput(BaseModel)：decision Literal[confirm,reject], note str；ApprovalInput：comment str；其他自选但不改变契约。

## HTTP 契约（FastAPI 实现）
GET /api/health；GET /api/contracts；POST /api/contracts multipart file；GET /api/contracts/{id}；POST /api/contracts/{id}/analyze；GET /api/contracts/{id}/events (SSE)；POST /api/contracts/{id}/review {decision,note}；POST /api/contracts/{id}/approve {comment}；GET /api/contracts/{id}/report 下载 Markdown；GET /api/knowledge。
分析后台线程执行，前端 SSE 获取进展，完成后重新读取详情。风险包括 id,title,level(high/medium/low),quote,reason,suggestion,rule_ids；quote 必须是正文子串，依据只允许检索到的知识库 ID。缺失条款单独标记无原文。人工确认后才允许模拟审批；重新分析清除旧人工意见及审批。

## 工作流
parse → identify → tools（知识检索与规则检查）→ 条件路由（有 Key: Qwen agent / 无 Key: 明示离线规则）→ validate → report。Qwen 使用绑定工具、真实 ToolNode 循环及最大轮数控制；不可信文档不能更改系统指令。报告展示工具调用、依据链接、人工意见、运行模式。

## 验证与交付
后端测试在隔离的临时 `DATA_DIR` 中运行，不读写演示数据库；`main.py` 的中断任务恢复在 FastAPI lifespan 中执行，模块导入不产生运行数据。测试覆盖、真实联调记录与未覆盖边界见 `docs/TESTING.md`；白名单打包、排除项与仍需主代理收尾的事项见 `docs/DELIVERY.md`。
