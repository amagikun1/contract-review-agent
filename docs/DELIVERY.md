# 交付说明

## 1. 交付包

- 路径：`D:\sgg\contract-review-delivery.zip`
- 生成命令（项目根目录）：

  ```powershell
  D:\sgg\contract-review-runtime\venv\Scripts\python.exe scripts/package_project.py
  ```

- 打包方式：`scripts/package_project.py` 使用**白名单**，只加入代码、依赖清单、样例、测试与文档。
- 明确排除：`.env`、`data/`、虚拟环境（`venv`/`.venv`）、`node_modules`、`dist`、`__pycache__`、`.pytest_cache`、`.git`、`*.pyc`、`*.log`、`*.tsbuildinfo`。
- 脚本在写完后会重新打开压缩包，断言归档路径中不包含上述目录或 `.env`。

本包包含：

```text
README.md、ARCHITECTURE.md、pytest.ini、.env.example、.gitignore
backend/（app、tests、requirements.txt、requirements.lock.txt）
frontend/（src、package.json、package-lock.json、tsconfig、vite 配置等；不含 node_modules/dist）
scripts/（setup、启动、样例生成、打包）
samples/（3 份 TXT 原稿 + 15 个衍生格式）
docs/（parser-smoke.json、TESTING.md、DELIVERY.md，以及打包时已存在的 demo-report.md、live-validation.json、screenshots/*.png）
```

`.env.example` 只有空 `DASHSCOPE_API_KEY=` 占位，不含真实密钥；真实 `.env` 不进包。

## 2. 本轮修改文件

| 文件 | 修改内容 |
| --- | --- |
| `backend/tests/conftest.py` | 用显式 `os.mkdir(..., 0o777)` 在临时目录设置 `DATA_DIR`，修复受限 Windows 沙箱下 0o700 临时目录导致的收集失败；新增 `tmp_dir` 夹具 |
| `backend/tests/test_api.py` | 改用 `tmp_dir`；新增 lifespan 中断恢复测试 |
| `backend/tests/test_workflow.py` | 改用 `tmp_dir`；新增模型 JSON 修复分支与工具轮数上限测试 |
| `scripts/package_project.py` | 将 `.env` 明确加入白名单排除集合（此前仅由打包后断言兜底） |
| `README.md` | 补充当前测试计数、文档入口、代码归属说明 |
| `ARCHITECTURE.md` | 更新本轮代码归属与“验证与交付”说明 |
| `docs/TESTING.md` | 新增：环境、命令、计数、隔离、真实联调证据、未覆盖边界 |
| `docs/DELIVERY.md` | 新增：交付包、修改文件、验证项、剩余父代理事项 |

普通后端代码 `backend/app/main.py`、`models.py`、`repository.py`、`parser.py` 本轮做了兼容性审查，**未发现必须改动的契约问题，因此没有修改**；契约字段、HTTP 路径和返回结构保持不变。`workflow.py`、`rag.py`、`rules.py`、`knowledge.json`、`frontend/` 未修改。

## 3. 已验证项

- 后端完整 pytest：**58 passed, 1 skipped**；跳过项为已安装 RapidOCR 时的“缺 OCR 依赖报错”用例，属预期。
- 测试数据隔离：`DATA_DIR` 指向临时目录，`data/contracts.sqlite3` 修改时间在测试前后未变化。
- lifespan 中断恢复：`reviewing` 合同在应用启动时被标记为 `failed` 并写入“中断”提示。
- 模型 JSON 修复分支：非法 JSON 经一次格式整理后可通过证据校验；工具循环第 4 轮触发上限异常。测试使用脚本模型，不调用真实 Qwen。
- 入参长度：`note` 10000 字返回 200，10001 字返回 422；非法 `decision` 返回 422；`comment` 10000 字返回 200。
- 打包脚本白名单断言通过；压缩包不含 `.env`、`data/`、`node_modules`、`dist`、`__pycache__`、`.pytest_cache`、虚拟环境或 `.pyc`。
- 解析与真实联调证据见 `docs/TESTING.md`。

## 4. 最终验收

Codex 已完成关键测试代码审查、前端生产构建、真实 Qwen 服务合同审查、原文高亮、人工确认、模拟审批、报告预览及浏览器下载验证。提交包包含 3 张实际运行截图和真实审查报告，无需补充编号为 01 的截图。

采购 PDF 最后一次模型请求超时，已明确记录为失败，没有再次发起收费请求。服务合同完整链路成功；PDF 文本解析和扫描 PDF OCR 均已独立验证。网络超时后可在界面重新审查。

本机密钥已配置；提交包不含密钥。换机器时按 README 安装依赖、配置自己的百炼 Key 并启动前后端。修改代码或更换模型后，应重新执行对应验证。

## 5. 未完成能力（不要写成已完成）

- PDF 版审查报告导出未实现；当前只有 Markdown 查看/下载。
- 未接入 MongoDB、MinIO、Milvus 等云数据库/对象存储。
- 未接入 BGE-M3 语义嵌入；检索是中文 n-gram TF-IDF 稀疏向量。
- 未实现用户权限、多租户、外部企业审批或多角色流转；人工复核与审批是本地 API 状态转换和模拟回写。
- DOCX 未用 Word/LibreOffice 渲染校验；PDF 样例 9 页由父代理人工渲染检查，但自动测试只覆盖文本提取与解析结构。
