# 测试记录与验证边界

本文件记录本包生成时的**实际**测试命令、结果和已知未覆盖项。测试只使用临时数据目录，不读写 `data/` 中的演示数据库；自动测试不发起真实 Qwen 请求，不产生模型费用。

## 1. 环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows（本机开发环境） |
| Python | 3.12.7 |
| 虚拟环境 | `D:\sgg\contract-review-runtime\venv` |
| Node.js / npm | 20.19.4 / 10.8.2 |
| pytest | 9.2.0 |
| 依赖锁定 | `backend/requirements.lock.txt`、`frontend/package-lock.json` |

## 2. 后端完整测试

在项目根目录执行：

```powershell
D:\sgg\contract-review-runtime\venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider
```

最近一次完整运行结果：

```text
58 passed, 1 skipped
```

共收集 59 个用例，其中 58 个通过、1 个跳过；完整运行约 10–15 秒（受 OCR 模型加载和机器性能影响）。跳过项是 `backend/tests/test_parser.py::test_image_without_ocr_dependency_gives_actionable_error`，原因是本机已安装 `rapidocr_onnxruntime`，该用例只验证“缺少 OCR 依赖时的可操作报错”，安装依赖后按设计跳过；同一个文件中的 OCR 端到端用例实际执行并通过。

`-p no:cacheprovider` 只是本机 DSH Windows 沙箱下 `.pytest_cache` 不可写时的规避参数，不影响测试结果。普通 Windows 环境按 README 执行即可：

```powershell
..\contract-review-runtime\venv\Scripts\python.exe -m pytest -q -rs
```

### 各测试文件

| 文件 | 用例数 | 覆盖内容 |
| --- | ---: | --- |
| `backend/tests/test_api.py` | 3 | 上传、审查、SSE、人工复核、模拟审批、报告、重新审查、400/404/409/415、lifespan 中断恢复 |
| `backend/tests/test_parser.py` | 23 | TXT/MD 编码与换行、超限截断、DOCX 段落与表格、文本层 PDF、空白页、页数上限、OCR 缺依赖报错、图片像素/大小限制、OCR 端到端、样例可解析 |
| `backend/tests/test_repository.py` | 26 | 建表、默认字段、跨实例持久化、列表顺序、JSON 往返、状态枚举、更新校验、内存库、入参模型 |
| `backend/tests/test_rules.py` | 2 | 付款规则误报防护、括号主体识别 |
| `backend/tests/test_workflow.py` | 5 | 离线图与两轮工具、TF-IDF 检索、脚本模型 Agent 循环与伪证据过滤、PDF 引文对齐、法律断言过滤、JSON 修复分支、工具轮数上限 |

## 3. 数据隔离与两个关键修复的验证

1. **`DATA_DIR` 临时目录**：`backend/tests/conftest.py` 在 pytest 收集前设置 `DATA_DIR`，并显式用 `os.mkdir(..., 0o777)` 创建临时目录。Windows 受限沙箱中 `tempfile.TemporaryDirectory` / pytest `tmp_path` 的 0o700 目录会在后续建子目录时被拒绝，本轮已改为同样的 0o777 方式，避免测试因文件权限而无法收集。
2. **`main.py` 恢复任务在 lifespan**：模块导入只创建应用对象和仓库，不再扫描并改写运行数据；`reviewing` 合同的中断恢复在 FastAPI lifespan 启动时执行。新增 `test_lifespan_marks_interrupted_review_failed` 验证：预置一条 `reviewing` 合同，用 `TestClient` 触发 lifespan 后状态变为 `failed`，错误信息包含“中断”。
3. **演示数据库未被触碰**：测试运行前后 `data/contracts.sqlite3` 的修改时间保持不变；`test_api.py` 还会把 `main.repo` 和上传目录替换为临时路径。

## 4. 本轮新增/调整的测试

- `test_lifespan_marks_interrupted_review_failed`：覆盖恢复任务的启动时机，防止把恢复逻辑重新放回模块导入。
- `test_model_json_repair_and_tool_round_cap`：用一个只在本进程内运行的脚本模型，先返回非法 JSON，断言 `validate` 进入 `format` 修复分支并只保留合法风险；再用一个每轮都返回工具调用的模型，断言第 4 轮触发“工具调用轮数”上限异常。两次调用都不访问真实模型。
- `backend/tests/test_api.py` 和 `test_workflow.py` 改用 conftest 的 `tmp_dir`，不再依赖 pytest `tmp_path`。

## 5. 入参长度等接口校验（手工探针，未计入 pytest 数）

使用隔离临时 `DATA_DIR`、空 `DASHSCOPE_API_KEY` 和 FastAPI `TestClient` 做过一次不收费的探针：

```text
离线审查成功：pending_review，5 项风险
note 长度 10000：200
note 长度 10001：422
非法 decision：422
comment 长度 10000：200
```

因此 `models.py` 的 `max_length=10000` 与 `main.py` 的复核入参校验行为一致，未改变既有字段名或返回结构。

## 6. 真实联调记录（主代理完成，非自动测试）

以下为父代理在本机真实环境中的验证事实，自动测试不重复发起收费请求：

- Qwen 真实服务合同 DOCX 审查：识别 13 项风险、6 次工具调用；浏览器原文高亮通过；人工确认后模拟审批成功，编号 `SIM-929B04DC01`；浏览器已完成 Markdown 报告下载（12040 字节），并验证报告预览。
- PDF 首次审查曾出现模型自行编造法规、换行导致引文匹配失败的问题，由父代理在 Agent 核心修复（`align_quote` 容忍 PDF 换行、法律断言过滤）；修复后服务合同审查成功。`workflow.py` 本轮只读，未在本轮改动。
- 采购 PDF 的后续任务曾被测试模块导入错误标记为中断；该隔离问题已修复（见第 3 节）。父代理的 `docs/live-validation.json` 记录：隔离修复后最后一次采购 PDF 运行遇到 `OpenAITimeoutError`，界面正确显示失败，为避免额外模型费用未重试；最终是否重试由父代理决定。
- 浏览器端前端 `tsc + vite build` 已通过（父代理执行，`live-validation.json` 记为 `frontend_build: passed`）；本后端角色未重复构建 `frontend/`。

## 7. 解析器实测（证据）

`docs/parser-smoke.json` 保存了部分实测结果：

| 文件 | method | 页数 | 字符数 | 累计耗时（同一脚本） |
| --- | --- | ---: | ---: | ---: |
| `purchase_contract.docx` | `docx` | 1 | 2314 | 0.21 s |
| `purchase_contract.pdf` | `pdf-text` | 3 | 2378 | 0.34 s |
| `purchase_contract_scan.pdf` | `pdf-ocr` | 3 | 2232 | 30.08 s |
| `purchase_contract_scan_page1.jpg` | `ocr` | 1 | 817 | 39.0 s |

父代理另外实测：真实采购 PNG 提取 788 字、约 11.71 秒（未写入 `parser-smoke.json`）；DOCX 仅做解析测试，未用 Word/LibreOffice 渲染；PDF 样例共 9 页已由父代理渲染检查。样例共 3 份 TXT 原稿 + 15 个衍生格式文件。

## 8. 已知未覆盖/未完成

- 自动测试不调用真实 Qwen，不验证真实模型质量、计费或网络故障恢复。
- 未实现 PDF 版审查报告导出；当前仅 Markdown 查看/下载。
- 未连接云数据库、对象存储或 Milvus，也未接入 BGE-M3 语义嵌入；检索为中文 n-gram TF-IDF。
- DOCX 不解析页眉页脚、文本框、图片，页码不是 Word 真实分页；PDF 使用解析文本高亮，不做原版坐标定位。
- OCR 为 CPU 模式，复杂表格、印章、低清照片的准确率不在测试保证范围内。
- 浏览器 UI 的完整点击链路由父代理人工验收；本角色只验证了后端接口与数据。
