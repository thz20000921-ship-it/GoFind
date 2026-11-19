# 本地图文引擎客户端

`scripts/image_engine.py` 使用 Python 标准库调用独立引擎的现有 HTTP API，不调用供应商 SDK，不改变运营规则。

先运行引擎自己的 `启动图文引擎.cmd`。默认连接本机8787端口，自动从引擎个人数据目录读取 `agent-token.txt`；也支持引擎已有的 `ILAB_CONJURE_DATA_DIR`、`IMAGE_ENGINE_TOKEN` 环境变量。供应商 Key 仍由引擎持有，GoFind 不需要它。

非默认位置可在被忽略的 `private/image-engine.json` 写 `base_url`、`data_dir`、可选 `timeout_seconds`。不把配置或业务任务放进公共提交。

```powershell
python scripts/image_engine.py --config private/image-engine.json status
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/reference import-link "真实笔记分享链接"
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/materials upload workspaces/demo/material.png
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/preflight prepare --job workspaces/demo/tasks/current/job.json
```

`import-link` 复用 `/api/company-workflow/link`，下载其返回的图片，再上传到 `/api/engine/assets`。`import-response.json` 保留真实原始返回，`assets-response.json` 和 `assets.json` 保留素材 ID 与本地文件对应关系。引擎的浏览器登录与 ego 独立，遇到登录/验证限制会原样报告并停止此路径；已有正常取得的本地素材仍可用 `upload` 导入。

按引擎真实 `Job/Page` 契约准备 `job.json`，用返回的 ID 填写 `references` 和相应页面的 `pages[].asset_ids`。创作者决定选题、文字、素材权利和模式。`prepare` 只调用真实 `status/plan/estimate`，图片授权固定为零。准确输入和检查结果保存在指定目录。续接先查同一份输入是否已有预检回执。输入、素材、页数或接口变了，已安排提交，或需要定位具体错误时，才补相应检查。

只有用户已经批准具体请求额度时，才执行提交命令。下面的3次只是调用示例，实际值取本次获准额度：

```powershell
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/run submit --job workspaces/demo/tasks/current/job.json --approve-image-requests 3
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/run query stable-task-id --wait-seconds 30
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/results download stable-task-id
```

提交前保存准确请求和任务 ID，超时或服务端写盘失败记录为未知，不自动重发。继续时先 `query` 同一 ID；正常重发同一准确请求沿用引擎的幂等机制。任务或模型配置更改不能静默复用旧 ID。额度由引擎执行；未配置单价时不能把次数估算当货币报价，也不推定余额足够。

`query` 保存真实 `progress.json`。`download` 逐页核对 PNG 签名及接口返回的哈希，保存返回的图片，全部完成才下载并检验整套 ZIP。部分失败时保留已成功页和原始进度，付费重试仍需已有授权覆盖。报告生成完成要对应到任务、回执和实际图片。逐页看图和事实验收另依据实际查看结果。

## 母版结构迁移和指定页修改

引擎status包含`reference_policies: ["inspiration", "layout"]`时，Job可用`mode: inspired`及`reference_policy: layout`。每页在`asset_ids`中明确对应一张`purpose: reference`的真实母版，素材权利仍如实标记；不同页可使用不同图，产品素材不代替母版。客户端只传准确内容、替换方向及映射，不做视觉拆解。引擎在图片请求中读取母版并保持视觉骨架，不默认填新色板或独立排版。

只有封面参考时，可将它明确映射到其他页作为设计体系依据，注明其他页没有原母版；不能把空`asset_ids`描述成已逐页参考。结构效果必须用实际图片对比，不能只看完成状态。

需要独立视觉分析时，复用引擎现有分析模型，不新建解析服务。该操作需要单独获准一次分析请求；只给图片额度时不调用。返回的`analyzed-job.json`只接受引擎视觉要求，保留原业务文字、事实和素材映射，并恢复为零图片授权供审阅：

```powershell
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/analysis analyze --job workspaces/demo/tasks/current/job.json --analysis-id stable-analysis-id --approve-analysis-requests 1
```

需要修正某页时，在私有`revision.json`给出稳定`revision_id`和`pages`，只包含要改的页及字段。现有母版映射、结构策略和其他页沿用，不重新生成整套。只有已获准的剩余图片额度才可传入：

```powershell
python scripts/image_engine.py --config private/image-engine.json --output workspaces/demo/tasks/current/revision revise stable-task-id --revision workspaces/demo/tasks/current/revision.json --approve-image-requests 1
```

`revision-state.json`保存明确返回或未知状态；没有自动重试。整套原始及修订请求总量要留在当前任务记录，不能把每次传入的独立上限误认为重新获得了用户额度。

客户端失败时返回非零退出码，并在任务目录保存错误及 HTTP 状态。工程单测验证费用边界、未知状态与下载校验；隔离模型测试和真实供应商图片质量需要分别记录。
