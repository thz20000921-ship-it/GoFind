# 浏览器交接与取证

## 何时必须用浏览器

- HTTP 只返回空壳、加载提示或极短正文，需要等待 JavaScript 渲染才能读取目标内容。
- 资料或入口依赖已授权账号的登录状态。
- 需要观察原生界面上的搜索联想、排序与筛选、评论区、私信入口或商品入口。

## 入口与降级顺序

从第一项开始，选择能在当前权限内取得所需证据的入口。若不可用，按顺序继续，记录实际尝试、结果和证据缺口。

1. **宿主的 `ego-browser` Skill**。从当前会话的 Skill 列表加载并遵循说明。常见宿主的入口如下。

   | 宿主 | 加载与调用 |
   | --- | --- |
   | DSH（DeepSeek Harness） | 在已启用 `@ego-lite/dsh-plugin` 的 profile 中启动 DSH，调用 `/ego-browser`。例如插件装在 `web` profile 时，用 `dsh --profile web` 启动。[ego 插件说明](https://upd.dev/citrolabs/ego-lite/src/branch/main/plugins/ego/README.md#deepseek-harness) |
   | Claude Code | 用户级 `~/.claude/skills/ego-browser/SKILL.md` 或项目级 `.claude/skills/ego-browser/SKILL.md` 中的独立 Skill 可用 `/ego-browser` 调用。通过 ego 插件加载时用 `/ego:ego-browser`。[Claude Code Skill 说明](https://code.claude.com/docs/en/skills) · [ego 插件说明](https://upd.dev/citrolabs/ego-lite/src/branch/main/plugins/ego/README.md#claude-code) |
   | Codex | 从用户级 `~/.agents/skills/ego-browser/SKILL.md`、项目级 `.agents/skills/ego-browser/SKILL.md` 或已启用的 ego 插件发现 Skill。在支持的界面可显式选择 `$ego-browser`，CLI 可用 `/skills` 查找。[Codex Skill 说明](https://learn.chatgpt.com/docs/build-skills) |

2. **直接调用 `ego-browser` 命令**。先运行 `ego-browser skill`，按当前安装版本返回的指南操作。接口和调用参数以该指南为准，GoFind 不保存 ego 的 API 参考或调用实现。
3. **宿主自带浏览器**。使用当前会话可用且有权限的渲染型浏览器，核对它实际能访问的页面、账号和地区。
4. **用户截图或导出**。说明需要的页面、查询与筛选，要求附观察时间。按用户提供的资料登记可见范围和缺失字段。
5. **纯 HTTP**。仅用于发现公开链接、标题或摘要等线索。缺少渲染或登录状态时，明确正文与界面证据未取得。

## 确认可用性

1. 在当前 shell 确认命令能否解析，PowerShell 可用 `Get-Command ego-browser`。找不到命令时，先检查安装目录和本会话的 `PATH`。Windows 默认安装目录可用 `%ProgramFiles%\Citro Labs\ego lite` 定位，在默认 C 盘系统上即 `Program Files` 下的对应目录。检查进程 `PATH` 是否为空或缺少实际可执行文件所在目录，必要时对照用户级和系统级 `PATH`，再判断是否与沙箱过滤或会话未刷新有关。命令查找失败只说明当前会话无法解析入口。确认已安装时，可在权限允许的会话中用现场定位的可执行文件运行，或在新终端重新检查 `PATH`。
2. 执行 `ego-browser skill`，确认命令成功返回且指南完整，末尾应有 `[ego-browser:skill] end of guide`。再按指南打开 `about:blank` 并截取空白页，核实浏览器连接、渲染和截图能力。若截图提示窗口已最小化，恢复 ego 窗口后再试。
3. 记录实际依据，包括命令解析与安装检查结果、`PATH` 是否缺项、指南是否完整、空白页截图是否成功和实际错误。指南成功只验证命令入口，账号和目标页面的可读性在真实任务中另行确认。

## Windows PowerShell 5.1

Windows PowerShell 5.1 调用原生程序时可能丢掉 `-e` 参数内部的双引号。多行脚本或含中文的脚本一律用宿主文件工具写成 UTF-8 `.mjs` 文件，再按 `ego-browser skill` 返回的文件执行方式运行。不要用 `Set-Content` 或 `Out-File` 写脚本。

## 一次最小真实读取

1. 打开目标页面，核对当前账号、地区和登录状态。无法确认的条件记为“未知”。
2. 等目标内容渲染后，用 ego 的 `snapshot()` 读取可见正文和控件。使用其他浏览器时采用对应的页面读取能力。
3. 截取同一页面状态，记录观察时间和时区。若只看到局部内容，注明截图和正文覆盖的范围。

没拿到正文就记录“未取得正文”，并注明卡在加载、登录、访问限制或读取工具的哪一环。没有观察到的内容不能写成“页面无内容”。

## 取证字段

沿用任务内的证据 ID，记录实际使用的入口、原样查询和观察范围。[证据登记](evidence.md#实际登记不做数据仓库)

- **来源与原件**。页面 URL、标题和截图相对路径。截图放在 `workspaces/<business>/tasks/<task>/`，路径相对于项目根目录。
- **观察条件**。登录状态、地区、视口尺寸、排序与筛选。条件未确认时标记“未知”。
- **观察时间与结果**。时间及时区、实际取得的正文或可见元素、截取范围和未取得的字段。用户提供的截图或导出注明来源。

## 授权与隐私

- 对平台或账号执行任何写操作，包括发帖、评论、私信、下单和改设置，都必须有针对该动作的明确授权。
- 需要登录或处理验证码时，用 ego 的 `handOff()` 把控制权交回用户，用户完成后再继续。降级到其他入口时也由用户处理登录和验证码。不得绕过访问限制或导出 Cookie。
- 含身份信息的截图只存放在 `workspaces/`。公开示例只能使用经脱敏的副本。
