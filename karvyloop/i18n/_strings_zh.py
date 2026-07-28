"""_strings_zh — 中文表(从 _strings.py 机械拆出;键序/内容零改动)。"""
ZH = {
    "console.lan_warning": (
        "[karvyloop] 绑 0.0.0.0 = 局域网可达。本机(localhost)免密;从别的设备访问需要带 token 的链接 —— "
        "在这台机器上跑 `karvyloop url` 获取。"
    ),
    "console.remote_url": "[karvyloop console] 跨设备访问(带 token 链接): {url}",
    "console.url_hint": "[karvyloop console] 以后再取这条链接: `{cmd} url`",
    "console.token_ledger_failed": "[karvyloop console] token 账本接线失败(不影响启动): {error}",
    "console.karvy_wired_on": "[karvyloop console] 小卡意图分析已接线(LLM on)",
    "console.karvy_wired_off": "[karvyloop console] 小卡意图分析已接线(LLM off — 暂不主动建议)",
    "console.karvy_wire_failed": "[karvyloop console] 小卡意图分析接线失败(console 照常起): {error}",
    "console.conv_ready": "[karvyloop console] 对话已就绪(续上 {n} 轮)",
    "console.conv_wire_failed": "[karvyloop console] 对话编排器接线失败(console 照常起): {error}",
    "console.domain_registry_failed": "[karvyloop console] domain_registry 构造失败(仅私聊): {error}",
    "console.opening": "[karvyloop console] 正在打开 {url}",
    "cli.init.launching_console": "配置好了 —— 正在打开你的控制台…",
    "console.uvicorn_missing": "[karvyloop] uvicorn 未安装({error});`pip install 'uvicorn[standard]>=0.30'`",
    "console.bind_failed": "[karvyloop console] 绑定失败: {error}",
    "console.port_fallback": "[karvyloop console] 端口 {orig} 被占用,已自动改用 {port}",
    "console.already_running": "[karvyloop console] 已有实例在 {url} 运行(v{ver})—— 直接打开它,别再开第二个",
    "console.old_running": "[karvyloop console] 端口上还有旧版 KarvyLoop(v{old})在 {url} 运行;请先停掉它再启动 v{new} —— 旧版占着端口时升级不会生效",
    "cli.config_missing": "[karvyloop] config.yaml 不存在({path}) — 只读视图",
    "cli.lang_set": "[karvyloop] 语言已设为 {lang}",
    "cli.unknown_cmd": "未知子命令:{cmd}",
    "cli.no_key_setup": "还没有可用模型/API Key —— 没有它 KarvyLoop 跑不起来。正在进入配置(或运行 `karvyloop init`;或设置对应 provider 的 API key 环境变量)。",
    "cli.help.update": "检查有没有新版本(只检测+提示,绝不自动升级)",
    "cli.help.url": "打印当前运行中 console 的访问链接(本机免密 + 跨设备带 token 链接)",
    "cli.url.no_runtime": "没有正在运行的 console(未记录 runtime)。先 `karvyloop console` 起服务。",
    "cli.url.not_running": (
        "console 现在没有在运行(端口 {port} 连不上——找到的只是上次运行留下的记录,"
        "链接打出来也打不开)。先 `karvyloop console` 起服务,再跑 `karvyloop url`。"
    ),
    "cli.url.local": "本机访问(免 token):    {url}",
    "cli.url.remote": "跨设备访问(带 token):  {url}",
    "cli.url.remote_none": "跨设备:当前 console 只绑了本机(localhost)。要从别的设备访问,用 `--host 0.0.0.0` 重启。",
    "update.disabled": "[karvyloop] 更新检测已关闭(设了 KARVYLOOP_NO_UPDATE_CHECK)。当前:{current}",
    "update.unreachable": "[karvyloop] 连不上发布源(离线 / 被限流)。当前:{current}",
    "update.uptodate": "[karvyloop] 已是最新({current})。",
    "update.available": "[karvyloop] 有新版本:{current} → {latest}",
    "update.command": "  升级:  {command}",
    "update.notes": "  更新内容:  {url}",
    # doctor / status(确定性自检)
    "cli.help.verify_web": "用无头浏览器真加载网页产物,抓控制台/运行时报错(网页类的运行时验收门)",
    "cli.help.verify_web.path": "网页产物目录或它的 index.html 路径",
    "cli.help.verify_web.entry": "入口文件(默认 index.html)",
    "verifyweb.unavailable": "[verify-web] 没装 Playwright —— 没法验浏览器运行时(只能验语法)。装:pip install playwright && playwright install chromium",
    "verifyweb.ok": "[verify-web] ✓ 加载无控制台/运行时报错({url})",
    "verifyweb.inconclusive": "[verify-web] ⚠ 浏览器验证器没跑成 —— 运行时没验到(既不算通过、也不算失败):",
    "verifyweb.failed": "[verify-web] ✗ 加载时有 {n} 条运行时报错:",
    "cli.help.doctor": "体检安装环境,用人话告诉你哪坏了、怎么修(不需要模型)",
    "cli.help.doctor.fix": "顺便自动修可逆的安全问题(如备份并重置坏掉的数据文件);有风险的留给你拍",
    "cli.help.doctor.online": "顺便跑活性检查:模型端点连不连得上、磁盘可不可写、沙箱能不能起(一次网络探测——绝不发送你的 key)",
    "cli.help.status": "快速状态:版本、模型就绪、更新",
    "doctor.header": "KarvyLoop 自检:",
    "status.header": "KarvyLoop 状态:",
    "doctor.overall.ok": "一切正常。✓",
    "doctor.overall.warn": "能用,但有上面的警告。⚠",
    "doctor.overall.fail": "还不能用 —— 先修上面带 ✗ 的项。",
    "doctor.msg.config_missing": "还没有配置({path})。",
    "doctor.fix.config_missing": "跑 `karvyloop init`,或启动 `karvyloop console` 按配置页走一遍。",
    "doctor.msg.config_unreadable": "配置读不了({path}):{err}。",
    "doctor.fix.config_unreadable": "修一下 YAML,或 `karvyloop init --force` 重写。",
    "doctor.msg.no_default_model": "没设默认 chat 模型。",
    "doctor.fix.no_default_model": "在控制台(🤖 模型)加一个,或跑 `karvyloop init`。",
    "doctor.msg.no_key": "你的模型需要 API key,但没有可用的。",
    "doctor.fix.no_key": "在配置页加一个 key(它会告诉你去哪拿),或设好对应 provider 的 API-key 环境变量。",
    "doctor.msg.model_not_ready": "模型未就绪({reason})。",
    "doctor.fix.model_not_ready": "打开控制台配置页,把模型配完。",
    "doctor.msg.model_ready": "模型就绪:{model}。",
    "doctor.msg.deps_ok": "核心依赖齐全。",
    "doctor.msg.dep_missing": "缺必需依赖:{pkg}。",
    "doctor.fix.dep_missing": "装上:`pip install {pkg}`(或 `pip install -e .`)。",
    "doctor.msg.dep_optional_missing": "可选依赖没装:{pkg}(对应功能不可用)。",
    "doctor.msg.data_fresh": "数据目录还没建 —— 首次用时会出现。",
    "doctor.msg.data_ok": "你的数据完好({dir})。",
    "doctor.msg.data_corrupt": "有几个数据文件解析不了:{files}。",
    "doctor.fix.data_corrupt": "备份后删掉它们 —— KarvyLoop 会把这几个从空重建(其余数据不动)。",
    "doctor.msg.version_current": "版本 {current}(最新)。",
    "doctor.msg.version_newer": "版本 {current} —— 有新版 {latest}。",
    "doctor.fix.version_newer": "升级:{command}",
    "doctor.msg.port_busy": "控制台端口 {port} 被占用(已在跑,或端口冲突)。",
    "doctor.fix.port_busy": "换个端口:`karvyloop console --port <N>`,或停掉占用 {port} 的进程。",
    "doctor.msg.port_free": "控制台端口 {port} 空闲。",
    "doctor.msg.check_error": "某项检查没跑成({err})—— 已跳过。",
    "doctor.fixing": "正在自动修复安全的那些:",
    "doctor.after_fix": "修复后:",
    "doctor.nothing_to_fix": "没有可安全自动修的 —— 上面剩下的都得你拍。",
    "doctor.msg.repaired_data_corrupt": "已备份并重置坏掉的数据:{files}(存为 <name>.corrupt.bak)。",
    "doctor.msg.repaired_config_missing": "已创建初始配置:{path}。编辑它(或跑 `karvyloop init`)填上你的模型和 key。",
    "doctor.msg.repaired_config_unreadable": "已把坏掉的配置备份到 {backup},并在 {path} 写了一份新的初始配置。",
    "doctor.confirm.config_unreadable": "{path} 的配置解析不了。要备份它并重写一份初始配置吗?",
    "doctor.confirm.skipped": "已跳过(原样不动)。",
    "doctor.log_at": "(完整日志:{path})",
    "doctor.msg.endpoint_reachable": "模型端点连得上:{host}({provider})。",
    "doctor.msg.endpoint_unreachable": "模型端点连不上:{host}({provider})—— 配是配了,但连不通。",
    "doctor.fix.endpoint_unreachable": "查一下网络/DNS、provider 是否正常、或你的 base_url。(你的 key 看起来是设了的——这是连通性问题,不是缺 key。)",
    "doctor.msg.local_endpoint_down": "本地模型服务没响应:{host}:{port}({provider})。",
    "doctor.fix.local_endpoint_down": "起一下它(如 `ollama serve`),或把配置指到一个在跑的端点。",
    "doctor.msg.liveness_skipped": "活性探测已跳过({reason})—— 先配好一个模型(见上)。",
    "doctor.msg.disk_writable": "数据目录可写({dir})。",
    "doctor.msg.disk_not_writable": "数据目录不可写({dir}):{err}。",
    "doctor.fix.disk_not_writable": "查一下 {dir} 的权限/剩余空间。",
    "doctor.msg.sandbox_ok": "沙箱就绪({impl})。",
    "doctor.msg.sandbox_degraded": "沙箱降级但可用({impl})—— 第一方直通,不受信脚本拒跑。",
    "doctor.msg.sandbox_stub": "沙箱不可用({impl})—— 代码执行 fail-closed(拒跑)。",
    "doctor.fix.sandbox_stub": "Linux 装 bubblewrap(`bwrap`);macOS 应自带 sandbox-exec。在此之前,跑代码的技能无法运行。",
    "doctor.msg.sandbox_error": "沙箱探测失败({err})。",
    "cli.desc": "KarvyLoop — AI-Native Agent 运行时(M0 雏形)",
    "cli.help.lang_global": "UI 语言:en(默认)或 zh",
    "cli.help.init": "写 ~/.karvyloop/config.yaml(本地优先默认)",
    "cli.help.init.config": "配置路径(默认 ~/.karvyloop/config.yaml)",
    "cli.help.init.force": "覆盖已存在",
    "cli.help.init.no_wizard": "跳过交互式 wizard(开发者 / CI 用),直接写默认 config",
    "cli.help.run": "一句话→沙箱执行→流式返回(垂直切片;M3+ 批 4 接 MainLoop)",
    "cli.help.run.intent": "自然语言意图(必填)",
    "cli.help.run.workspace": "工作区根(默认 cwd)",
    "cli.help.run.model": "覆盖默认 chat 模型(provider/id 形式)",
    "cli.help.run.json": "NDJSON 输出(直跳 forge,不走 MainLoop)",
    "cli.help.run.no_recall": "完全跳 MainLoop(直跳 forge,1:1 旧行为,debug/手动测试用)",
    "cli.help.run.skills_dir": "已结晶技能目录(默认 ~/.karvyloop/skills,可被 config.yaml crystallize.skills_dir 覆盖)",
    "cli.help.chat": "启动 KarvyLoop Workbench TUI(L0+L1+L2+L3)",
    "cli.help.chat.headless": "headless 模式(自动 quit,给测试用)",
    "cli.help.chat.serve": "textual-serve 远程(默认 127.0.0.1:8765)",
    "cli.help.chat.host": "serve host(默认 127.0.0.1,不绑 LAN)",
    "cli.help.chat.port": "serve port(默认 8765)",
    "cli.help.replay": "按 task_id 重放一次 drive 的 trace 事件(NDJSON)",
    "cli.help.replay.task_id": "drive 任务 ID(uuid4 hex[:16];给了 --run 可省)",
    "cli.help.replay.run": "只输出该 run_id 的条目(run_id 见 Trace / token 账本的 run_id 字段)",
    "cli.help.replay.trace_path": "trace.sqlite 路径(默认 ~/.karvyloop/trace.sqlite)",
    "cli.run.config_missing": "配置不存在:{path}。先跑 karvyloop init。",
    "cli.interrupted": "中断。",
    "cli.chat.readonly_warning": "[karvyloop] config.yaml 不存在({path});TUI 只读视图 — intent 提交将失败,先跑 `karvyloop init`",
    "chat.empty_retry_fallback": "(这次没接住,能再说一遍吗?)",
    "wizard.choose_provider": "选择你的 LLM provider:",
    "wizard.choose_prompt": "选 [1..{n}] (默认 1=本地): ",
    "wizard.unknown_provider": "未知 provider: {raw}",
    "wizard.provider_hint": "选 1..{n} 或 {names}",
    "wizard.apikey_prompt": "{env_var} (输入会隐藏但本版不屏蔽,真生产用 getpass): ",
    "wizard.apikey_skipped": "  跳过(将在 config.yaml 写 {env_var} 占位,你 export 后再跑)",
    "wizard.apikey_bad": "API key 格式有问题: {err}",
    "wizard.apikey_hint": "检查 {env_var} 是否复制完整(无空格/换行/占位符),或重跑 karvyloop init 选 skip",
    "wizard.written": "✓ 已写入:{target}",
    "wizard.next_ollama": "下一步:启动 ollama (默认 http://127.0.0.1:11434),然后 karvyloop run \"...\"",
    "wizard.next_apikey": "下一步:直接 karvyloop run \"...\" 即可(API key 已写入 config.yaml)",
    "wizard.next_export": "注意:config.yaml 写的是 ${{{env_var}}} 占位 —— 环境变量没设之前跑不起来(先 export {env_var}=...,再 karvyloop run \"...\")",
    "wizard.custom_desc": "自定义 OpenAI 兼容端点(自己的 base_url + 模型 id:vLLM / Ark / 自建网关)",
    "wizard.custom_base_prompt": "OpenAI 兼容端点的 Base URL(如 https://host/v1): ",
    "wizard.custom_base_bad": "base URL 必须以 http:// 或 https:// 开头",
    "wizard.custom_model_prompt": "端点认的模型 id(如 gpt-4o / Ark 的 endpoint id): ",
    "wizard.custom_model_bad": "模型 id 不能为空",
    "wizard.custom_key_prompt": "API key(仅免 key/本地端点可留空): ",
    # 模型配置(gateway/console 共用;fail-loud 出人话)
    "models.api_unimplemented_choice": (
        "API 形态「{api}」本版未实现 —— 存下来每次聊天都会失败。"
        "OpenAI 兼容端点(vLLM / Ollama-OpenAI / Ark / 多数网关)请选 'openai-completions';"
        "Anthropic 兼容端点请选 'anthropic-messages'。"
    ),
    "models.kimi_coding_key_hint": (
        "这是 Kimi For Coding 的 key(sk-kimi-…):只能走 coding 端点 "
        "https://api.kimi.com/coding/v1(有 User-Agent 白名单门,本客户端未过审前可能 403),"
        "在 Moonshot 聊天端点上必失败。聊天请去 platform.moonshot.ai(Global)或 "
        "platform.moonshot.cn(中国区)拿 key;或选「Kimi For Coding」预设在它自己的端点上用这把 key。"
    ),
    "gateway.api_unimplemented": (
        "API 形态「{api}」本版未实现。请改这个模型的 api(console 🤖 模型面板或 config.yaml):"
        "OpenAI 兼容端点 → 'openai-completions';Anthropic 兼容端点 → 'anthropic-messages'。"
    ),
    "gateway.api_embed_unimplemented": "API 形态「{api}」的 embedding 本版未实现。",
    "cli.help.console": "启动本地 HTML 控制台(K3/K4 只读,K5 工厂)",
    "cli.help.console.config": "config.yaml 路径(默认 ~/.karvyloop/config.yaml)",
    "cli.help.console.host": "绑定 host(默认 127.0.0.1;LAN 需显式 0.0.0.0)",
    "cli.help.console.port": "绑定 port(默认 8766;区别于 textual-serve 8765)",
    "cli.help.console.no_browser": "不自动开浏览器(headless / smoke 用)",
    "cli.help.console.no_llm": "跳过 LLM 注入(只读视图 + chat_history 仍可用)",
    "cli.help.lang": "UI 语言:en(默认)或 zh",
    # karvyloop export(打包带走)
    "cli.export.help": "把你的实例(~/.karvyloop)打成一个可携带压缩包 —— 密钥除外",
    "cli.export.done": "已导出你的实例:{n} 个文件({size})-> {path}",
    "cli.export.excluded": "刻意排除:config.yaml(你的 API 密钥留在原地)、console.runtime.json、*.lock",
    "cli.export.restore": "恢复:在新机器解压到 ~/.karvyloop,补上密钥,然后 karvyloop console",
    # karvyloop import(export 的回程:一键迁移)
    "cli.import.help": "把导出的实例包恢复到 ~/.karvyloop —— `karvyloop export` 的回程",
    "cli.import.help.archive": "`karvyloop export` 产出的包(.zip 或 .tar.gz)",
    "cli.import.help.force": "合并进已有实例:冲突文件逐个覆盖(本机独有文件保留)",
    "cli.import.help.dry_run": "只列出会恢复什么,不写盘",
    "cli.import.not_found": "找不到包:{path}",
    "cli.import.unreadable": "读不了这个包(不是 zip/tar.gz,或已截断/损坏):{path} —— 没有写入任何东西",
    "cli.import.unsafe": "拒收这个包:不安全成员 '{name}'(绝对路径、'..' 或链接)—— 没有写入任何东西",
    "cli.import.nothing": "包里没有可恢复的实例数据:{path}",
    "cli.import.refuse": "{root} 已有实例数据 —— 不加 --force 不合并。",
    "cli.import.refuse.collisions": "会被覆盖的顶层项:{items}",
    "cli.import.refuse.no_collisions": "没有文件冲突 —— 加 --force 合并不会覆盖任何东西。",
    "cli.import.refuse.hint": "用 --dry-run 看完整清单,或 --force 合并(逐文件覆盖;本机独有文件保留)。",
    "cli.import.dry_run.header": "干跑 —— 零写盘。将恢复 {n} 个文件到 {root}:",
    "cli.import.skipped": "刻意跳过:{items}(秘密/锁永不落地;MANIFEST.txt 是包自己的说明书)",
    "cli.import.done": "已恢复你的实例:{n} 个文件 -> {root}",
    "cli.import.overwrote": "覆盖了 {n} 个已有文件(--force)",
    "cli.import.config_kept": "本机 config.yaml 一字未动 —— API 密钥按机器各留各的。",
    "cli.import.next": "下一步:补上模型 API key(`karvyloop init`,或编辑 ~/.karvyloop/config.yaml),然后 `karvyloop console` —— 你的技能、知识和历史都在原位。",
    # channels.webhook(出站推送通知正文;用户在 ntfy/Bark/Slack 等承接端看到)
    "channels.webhook.title": "[KarvyLoop] {n} 张决策卡待处理",
    "channels.webhook.aging": "⏳挂了{days}天 ·",
    "channels.webhook.high_risk": "⚠ 高危 —— 请回控制台确认",
    "channels.webhook.more": "…还有 {n} 张",
    "channels.webhook.open": "回控制台拍板:{url}",
    "channels.webhook.reply_code": "↩ 回批码:{code}",
    "channels.webhook.reply_hint": "回复「ACCEPT <码>」(或 REJECT / DEFER)即拍板 —— 码单次有效、限时;高危卡请回控制台。",
    # residents(原住民引荐入住,docs/60;卡文案在出卡时按当前 locale 定稿)
    "residents.referral.summary": "🏠 你的 Karvy 世界还空着 —— 认识一下第一位原住民:{names}。让 TA 入住吗?",
    "residents.referral.basis_footer": (
        "ACCEPT = 真的建出这个角色:TA 的身份、性情、验证门、协作契约全是明文文件,"
        "你随时能打开看、照着改 —— 这就是「怎么约束一个 agent」的活教材。"
        "目录权限是记在能力台账上的硬白名单(随时可见、可撤);删除任何东西都必须你亲手确认,"
        "且先备份。REJECT = 以后绝不再提;DEFER = 卡先挂着。"
    ),
    "residents.referral.accepted": (
        "{names} 已入住。目录权限已授(能力总览里随时可撤):{dirs}。"
        "TA 动任何文件都先给你看预览 —— 没你点头,什么都不会被删。"
    ),
    "residents.referral.no_registry": "角色库未接线 —— 没法安排入住。",
    "residents.referral.none_found": "这个安装包里找不到原住民镜像(打包问题?)—— 没有建任何东西。",
    "residents.referral.failed": "「{name}」入住失败:{error}",
    # butler first lesson(文件管家第一课:方案预览卡 + 兑现回执;卡文案出卡时按 locale 定稿)
    "butler.lesson.summary": "📁 文件管家的第一单 —— {dirs} 的整理方案:{n} 项移动,预览在下面。你拍板之前,一个文件都不会动。",
    "butler.lesson.basis_scan": "只读盘点:{dirs} 里清点了 {n} 个文件(只读元数据,没碰任何东西)。",
    "butler.lesson.mode_by_type": "分桶方式:按类型(图片/文档/安装包…)。",
    "butler.lesson.mode_by_time": "分桶方式:按时间(年-月文件夹)。",
    "butler.lesson.mode_from_intake": "—— 按你入门问答里选的整理习惯。",
    "butler.lesson.basis_dups": "查重发现:{n} 组内容完全相同的文件(hash 核实过)。只报告 —— 第一课绝不删除,要不要清重复得你另行拍板。",
    "butler.lesson.basis_hogs": "占位大户:{top}。只报告,不挪动。",
    "butler.lesson.basis_truncated": "注:有文件夹超过 {cap} 项 —— 本方案只覆盖前 {cap} 项。",
    "butler.lesson.basis_safety": (
        "ACCEPT = 严格按这份方案执行:只移动、只在这些文件夹之内,绝不删除、绝不覆盖"
        "(目标已存在就跳过并如实报),每一步都记台账、可撤回。"
        "REJECT = 只看看不动 —— 什么都不会发生,这也是完全合法的选择。"
    ),
    "butler.lesson.receipt": (
        "完成:{moved} 个文件已归位,{skipped} 个跳过(目标已存在/中途消失/越界 —— 如实列账,绝不硬来)。"
        "没有删除任何文件;每一步移动都记在 butler_moves.json 台账里,随时可以撤回。"
    ),
    "butler.lesson.receipt_none": "没什么需要动的 —— 方案已经满足了。",
    "butler.lesson.bad_plan": "这张卡上的方案读不出来 —— 拒绝碰任何文件(重扫一次拿新方案)。",
    # 管理面 CLI(名词-动词,覆盖既有后端:角色/域/记忆/技能/定时/token)
    "cli.help.role": "管理角色(agent 镜像):list / show",
    "cli.help.role.list": "列出本实例所有角色",
    "cli.help.role.show": "看一个角色的身份 + 配方(原子/技能)",
    "cli.help.role.id": "角色 id(目录名)",
    "cli.help.domain": "管理业务域:list / show",
    "cli.help.domain.list": "列出所有业务域",
    "cli.help.domain.show": "看一个业务域(value.md、成员查询、生命周期)",
    "cli.help.domain.id": "业务域 id",
    "cli.help.memory": "你的知识库:recall 召回 / add 沉淀",
    "cli.help.memory.recall": "按 query 召回 Belief(grep/词面重叠,无向量)",
    "cli.help.memory.recall.query": "召回的查询文本",
    "cli.help.memory.add": "往个人知识库加一条 Belief",
    "cli.help.memory.add.belief": "要记住的这条内容",
    "cli.help.memory.scope": "scope:personal(默认)或 domain",
    "cli.help.memory.limit": "最多返回几条(默认 8)",
    "cli.help.skill": "你结晶的技能:list",
    "cli.help.skill.list": "列出所有已结晶 + 系统技能",
    "cli.help.schedule": "定时任务(只 Karvy 能起):list",
    "cli.help.schedule.list": "列出所有定时任务",
    "cli.help.token": "token 用量账本:report",
    "cli.help.token.report": "按 source / model / day 聚合报 token 用量",
    "cli.help.token.by": "聚合维度:source(默认)、model 或 day",
    "cli.help.json": "输出机器可读 JSON(不打表格)",
    "cli.help.yes": "非交互确认(在非 TTY 里 create/mutate 必带)",
    "cli.manage.no_instance": "{path} 没有实例 —— 先跑 `karvyloop init`,或传 --config。",
    "cli.manage.role_none": "还没有角色。",
    "cli.manage.role_not_found": "找不到角色:{id}",
    "cli.manage.domain_none": "还没有业务域。",
    "cli.manage.domain_not_found": "找不到业务域:{id}",
    "cli.manage.memory_none": "没有匹配的 Belief。",
    "cli.manage.memory_added": "已记住:{belief}",
    "cli.manage.memory_add_failed": "已写入内存,但落盘失败:{error}",
    "cli.manage.memory_unavailable": "记忆库不可用 —— 这是个合法实例吗?",
    "cli.manage.skill_none": "还没有结晶技能。",
    "cli.manage.schedule_none": "没有定时任务。",
    "cli.manage.token_none": "还没有记录 token 用量。",
    "cli.manage.needs_yes": "这会创建/改动数据。在非终端环境请加 --yes 确认。",
    "cli.manage.confirm_add": "把这条 Belief 加进知识库?",
    "cli.manage.aborted": "已取消。",
    # ---- create/mutate 子命令(list/show 之外的动词)----
    "cli.help.role.create": "新建一个角色(agent 镜像)",
    "cli.help.role.create.id": "角色 id(目录名;字母/数字/_/-)",
    "cli.help.role.create.identity": "身份文本(这个角色是谁)",
    "cli.help.role.create.soul": "灵魂文本(SOUL.md)",
    "cli.help.role.create.nickname": "进域时的显示名(如「张三」)",
    "cli.help.role.create.model": "角色级模型引用(空=层叠到默认)",
    "cli.help.role.rm": "删除一个角色(破坏性 —— 删掉它的镜像目录)",
    "cli.help.domain.create": "新建业务域(带 --parent 则建子域)",
    "cli.help.domain.create.name": "业务域名称",
    "cli.help.domain.create.parent": "父业务域 id(子域继承 value.md + deontic)",
    "cli.help.domain.archive": "归档业务域(软删除 —— 之后只读)",
    "cli.help.schedule.add": "用自然语言加一条定时任务(NL→cron)",
    "cli.help.schedule.add.text": "自然语言描述(如「每天早上8点汇总进展」)",
    "cli.help.schedule.rm": "删除一条定时任务",
    "cli.help.schedule.toggle": "启用/停用一条定时任务",
    "cli.help.schedule.on": "启用该任务",
    "cli.help.schedule.off": "停用该任务",
    "cli.help.schedule.id": "定时任务 id",
    "cli.help.skill.import": "导入第三方技能(Agent-Skills 开放标准;沙箱执行)",
    "cli.help.skill.import.source": "github 来源 / .zip|.skill 的 url 或路径 / 本地文件夹",
    "cli.help.skill.import.overwrite": "覆盖同名的已有技能",
    "cli.manage.role_id_required": "必须给角色 id(--id)。",
    "cli.manage.role_create_failed": "无法创建角色:{error}",
    "cli.manage.role_created": "已创建角色:{id}",
    "cli.manage.role_removed": "已删除角色:{id}",
    "cli.manage.confirm_role_rm": "删除角色「{id}」及其镜像目录?此操作不可撤销。",
    "cli.manage.domain_name_required": "必须给业务域名称(--name)。",
    "cli.manage.domain_create_failed": "无法创建业务域:{error}",
    "cli.manage.domain_created": "已创建业务域:{name}({id})",
    "cli.manage.domain_archived": "已归档业务域:{id}",
    "cli.manage.confirm_domain_create": "创建业务域「{name}」?",
    "cli.manage.confirm_domain_archive": "归档业务域「{id}」(之后只读)?",
    "cli.manage.confirm_role_create": "创建角色「{id}」?",
    "cli.manage.schedule_text_required": "必须给自然语言描述。",
    "cli.manage.schedule_no_llm": "没配模型 —— NL→cron 需要 LLM。先跑 `karvyloop init`,或传 --config。",
    "cli.manage.schedule_not_understood": "没听懂明确的时间 —— 换种说法(如「每天早上8点……」)。",
    "cli.manage.schedule_parsed": "解析出:cron={cron}  intent={intent}",
    "cli.manage.confirm_schedule_add": "加这条定时任务?",
    "cli.manage.schedule_added": "已定时:{id}  [{cron}]  {title}",
    "cli.manage.schedule_removed": "已删除定时任务:{id}",
    "cli.manage.confirm_schedule_rm": "删除定时任务「{id}」?",
    "cli.manage.schedule_not_found": "找不到定时任务:{id}",
    "cli.manage.schedule_toggled": "定时任务 {id} 现在是 {state}。",
    "cli.manage.state_on": "启用",
    "cli.manage.state_off": "停用",
    "cli.manage.confirm_skill_import": "从「{source}」导入技能(第三方,不可信)?",
    "cli.manage.skill_import_failed": "技能导入失败:{error}",
    "cli.manage.skill_imported": "已导入技能:{name}({files} 个文件){scripts}",
    "cli.manage.skill_scripts_note": " —— 含 scripts/(执行走沙箱)",
    # mesh 任务板:跨设备接活 H2A 卡(docs/74 §6.2/§6.3)
    "mesh.takeover.summary": "你的「{device}」设备上的任务中断了:{intent} —— 要在这台接着跑吗?",
    "mesh.takeover.basis": (
        "任务 {task_id} 最后由你的「{device}」设备认领,但 lease 到期没有心跳续租 —— "
        "判定它中途离线(来源设备:{source})。ACCEPT = 在本机从头重跑,并在你的共享任务板上"
        "记下这次接管(其它设备不再重复提醒)。REJECT / 不拍 = 什么都不发生;之后你的任一设备仍可接。"
    ),
    "mesh.takeover.receipt": "{detail}(已在你的共享任务板记下这次接管)",
    # mesh 任务板:Pursuit 跨设备接管(docs/88 第三刀 #3)
    "mesh.takeover.pursuit_summary": (
        "你的「{device}」设备正在追一个目标,然后没声了:{statement} "
        "—— 已推进 {advances} 轮{gate}。要在这台设备上接着追吗?"
    ),
    "mesh.takeover.pursuit_gate_suffix": ";完成判据:{gate_desc}",
    "mesh.takeover.pursuit_basis": (
        "长线目标 {pursuit_id} 此前在你的「{device}」设备上运行(来源:{source});它的 lease 到期都没有心跳,"
        "看起来是中断了。ACCEPT 会从已保存的进度接着追(第 {advances} 轮 —— checkpoint 已在你的设备间同步),"
        "不是从零开始。"
    ),
    "mesh.takeover.pursuit_receipt": (
        "已接管目标「{statement}」—— 带着已保存的进度,从第 {advances} 轮接着追。"
    ),
    "mesh.takeover.pursuit_claim_lost": (
        "你的另一台设备({device})已经把这个目标接走了 —— 这台就此站开。"
    ),
    "mesh.takeover.pursuit_no_store": (
        "这台设备没有接 pursuit 存储,没法接管这个目标。"
    ),
    "mesh.takeover.pursuit_bad_checkpoint": (
        "你另一台设备保存的进度读不出来({error})—— 不接管了。"
    ),
    # ---- 提案工厂 summary/basis(zh 保持既有原文,行为零回归)----
    # confirm_decision_pref(decision_wire)
    "proposal.confirm_pref.kind_constraint": "约束",
    "proposal.confirm_pref.kind_taste": "品味",
    "proposal.confirm_pref.kind_standing": "站位",
    "proposal.confirm_pref.kind_default": "偏好",
    "proposal.confirm_pref.summary": "记成你的默认偏好吗?[{label}] {content}",
    "proposal.confirm_pref.basis": "我从你的拍板里注意到这条;记下来后,我提案会提前按它对齐,你少拒、少重复解释自己。",
    # run_task resume(proactive)
    "proposal.run_task.summary": "上次「{intent}」没跑完(出错/中断)—— 要我重试吗?",
    "proposal.run_task.basis": (
        "「{who}」执行的任务「{intent}」状态 = error(出错/中断),没跑完。"
        "原因/最后输出:{err}。重试 = 用同样的意图再跑一遍。"
    ),
    "proposal.run_task.default_error": "出错/中断",
    "proposal.run_task.default_who": "小卡",
    # silence(挣来的静音:授权/续期/吊销)
    "proposal.silence.domain_suffix": "(域「{d}」)",
    "proposal.silence_grant.summary": (
        "「{kind}」{dom}这类板,我最近 {n} 次押中 {hits} 次"
        "(95% 置信下界 {lb}%)—— 要不要以后这类替你静音处理?"
    ),
    "proposal.silence_grant.basis": (
        "这不是要更多权限 —— 是同类卡上我 {n} 次押中 {hits} 次的成绩单,按 95% 置信"
        "下界算也 ≥{min_lb}%(不是碰巧连中,批/拒两向各自过线),其中我押"
        "你会拒 {reject_pred} 次、押对 {reject_correct} 次"
        "(证明我能替你挡坏的,不只会点头)。"
        "ACCEPT 后 30 天内:这类卡我只替你办「我押你会 ACCEPT 且把握 ≥"
        "{min_conf}%」的;押 REJECT 或没把握的照旧问你;"
        "我还会不定期抽一部分照常出卡对答案(哪张是抽查不告诉你);删除/外发/付款/"
        "上线这类不可逆的永远问你。每次静音处理完整留痕(运行记录+台账)、满 30 天"
        "要你亲手续期;我押错一次立即自动收回授权,你也随时可撤。"
        "REJECT=保持现状,每张都问你。"
    ),
    "proposal.silence_renew.audit_some": "抽查对账 {audit_n} 次中 {audit_hits} 次",
    "proposal.silence_renew.audit_none": "本期没攒到抽查对账样本",
    "proposal.silence_renew.mark_overturned": "⚠翻案 ",
    "proposal.silence_renew.mark_failed": "✗失败 ",
    "proposal.silence_renew.review_item": "{mark}「{gist}」",
    "proposal.silence_renew.review_disp": "。本期风险最高的 {n} 条(翻案/失败/最贵优先):{gists}",
    "proposal.silence_renew.summary": (
        "「{kind}」{dom}的静音授权满 30 天到期 —— 上月替你静音 {silenced_n} 次,"
        "{audit};要续 30 天吗?"
    ),
    "proposal.silence_renew.basis_head": (
        "静音授权只有 30 天,到期必须你亲手续 —— 没人看的对账不算数,不点就停"
        "(这类卡已恢复逐张问你)。本期账:静音 {silenced_n} 次、{audit}"
    ),
    "proposal.silence_renew.basis_oldest": "、最老一条留痕 {pid}",
    "proposal.silence_renew.basis_tail": (
        ";每条都在台账/运行记录里可查,逐条看完再决定。ACCEPT=续 30 天(规则不变:"
        "只办押你会 ACCEPT 且把握 ≥{min_conf}% 的,"
        "继续不定期抽查,押错一次立即收回);REJECT=不续,每张都问你。"
    ),
    "proposal.silence_revoked.summary": "已自动收回「{kind}」{dom}的静音授权 —— 这类卡恢复逐张问你",
    "proposal.silence_revoked.reason_default": "我押错了一次你的拍板",
    "proposal.silence_revoked.basis": (
        "{reason}。挣来的静音只在命中率兑现时有效 —— "
        "押错一次立即收回(保守边界);要重新拿授权,得吊销之后重新攒新鲜对账"
        "(95% 置信下界 ≥{min_lb}%,至少 {min_n} 次)我才会再问你。ACCEPT=知悉。"
    ),
    # cocreate_finalize(cocreation)
    "proposal.cocreate.summary_template": "共创定稿:一键开出模板域「{name}」",
    "proposal.cocreate.basis_template": (
        "共创会话里你选定了现成模板「{name}」。ACCEPT = 走既有 instantiate 路径"
        "真开出该域和配好灵魂的角色(幂等:同名活跃域已存在会被拒并如实说)。"
    ),
    "proposal.cocreate.summary_custom": "共创定稿:建业务域「{name}」+ {n} 个角色",
    "proposal.cocreate.basis_custom": (
        "这是共创会话的最终草案(S1/S2 期间没写过任何东西 —— 零副作用)。"
        "ACCEPT 才真建:角色走 RoleRegistry.create(尽责契约 COMMITMENT 统一 seed,"
        "与系统默认/导入同一份),域落 value.md + deontic 真护栏。"
        "卡上任何字段不对,可直接改了再批。"
    ),
    # weekly_digest
    "proposal.weekly_digest.gist_quiet": "这周很安静(无任务/无消耗)",
    "proposal.weekly_digest.gist": "跑了 {runs} 次任务(成 {ok}/败 {fail}),烧了 {tokens} tokens",
    "proposal.weekly_digest.summary": "周报 {start}→{end}:{gist}",
    "proposal.weekly_digest.basis": (
        "数字全部从 Trace / tokens.db / 决策流水确定性汇总,零 LLM、可回链"
        "(每条带 trace_ref/id)。ACCEPT 仅表示已读,不触发任何执行。"
    ),
    # weekly-digest (card body markdown skeleton — 骨架走 i18n,动态数据/gist/回链是数据不翻)
    "weekly.md.title": "周报",
    "weekly.md.quiet": "这周很安静:没有任务运行、没烧 token、没有结晶/修订,也没有要你拍的板。",
    "weekly.md.h_tasks": "任务",
    "weekly.md.h_token": "Token",
    "weekly.md.h_skills": "技能",
    "weekly.md.h_decisions": "你拍的板",
    "weekly.md.h_pending": "还挂着的",
    "weekly.md.h_summary": "一句话",
    "weekly.md.tasks_line": "- 跑了 **{runs}** 次:成 {ok} / 败 {fail}(成功率 {rate})",
    "weekly.md.recall_line": "- 快脑/召回命中率 {rate}(stable 回放 {replays} + 技能制导重跑 {reruns})",
    "weekly.md.failures_head": "- 失败清单(最近 {n} 条{more}):",
    "weekly.md.failures_more": ",另有 {m} 条未列",
    "weekly.md.token_unwired": "- 账本未接线(无数据,不猜)",
    "weekly.md.token_line": "- 共 **{total}** tokens / {calls} 次调用(in {input} / out {output})",
    "weekly.md.token_source": "  - {source}: {total}({calls} 次)",
    "weekly.md.skills_new": "- 新结晶 **{n}** 个",
    "weekly.md.crystallized_item": "  - {name}(sig {sig},回链 {ref})",
    "weekly.md.revisions_line": "- 修订:落地 {landed} / 出卡待你拍 {proposed}",
    "weekly.md.revision_item": "  - {name}({mode},回链 {ref})",
    "weekly.md.decisions_unwired": "- 决策流水未接线(无数据,不猜)",
    "weekly.md.taste_line": "- 口味命中率(滚动窗):**{rate}**{arrow}(n={n})",
    "weekly.md.taste_insufficient": "- 口味命中率:样本不足(n={n} < {min}),不报百分比",
    "weekly.md.taste_unwired": "- 口味命中率:口味押注未接线",
    "weekly.md.pending_line": "- **{count}** 张卡等你拍{age}",
    "weekly.md.pending_age": ",最老挂了 {days} 天",
    "weekly.md.pending_none": "- 没有挂着的卡",
    # archive_stale(knowledge_tick)
    "proposal.archive_stale.summary": "🗄️ {n} 条知识一年没用了,归档?",
    "proposal.archive_stale.basis": (
        "这 {n} 条知识超过一年没被召回过(也没更新),疑似过时:{shown}。"
        "ACCEPT = 打失效标记归档(失效不删:仍留库可审计、可翻案,只是不再进召回);"
        "REJECT = 留着继续参与召回。"
    ),
    # promote_experience(promotion_tick)
    "proposal.promote_exp.line": "原(域内):{before}\n升(通用):{content}",
    "proposal.promote_exp.line_why": "\n  ↳ 为什么泛化:{why}",
    "proposal.promote_exp.summary": "📜 「{role}」有 {n} 条域内经验可升为通用兵法,升吗?",
    "proposal.promote_exp.basis": (
        "「{role}」在域「{domain}」的这些经验通过了泛化判定与脱敏改写。"
        "ACCEPT = 升为该角色的通用兵法(跨域可用;将来对外可见面也只有这一层);"
        "REJECT = 这轮不升,域内照用(这批经验没有新变化就不再重提)。升层后删域不再自动撤——"
        "要撤在记忆面板单条失效。\n\n{lines}"
    ),
    # route_to_role
    "proposal.route.summary": "把「{requirement}」转给业务域「{domain_name}」的「{role}」",
    "proposal.route.basis": (
        "这件事属于业务域「{domain_name}」的职责;我不越界自己做,"
        "而是委派给「{role}」在该域 value.md 治理下执行。你 ACCEPT 才真正转过去。"
    ),
    # roundtable
    "proposal.roundtable.who_default": "群里的角色",
    "proposal.roundtable.summary": "在「{group}」开圆桌,叫上 {who} 讨论「{topic}」",
    "proposal.roundtable.basis": (
        "你想让多个角色一起讨论,这是圆桌(几个人坐一起),不是把活交给一个人(委派)。"
        "我会在群「{group}」拉上 {who},先和你对齐目标再开始讨论。你 ACCEPT 才真正开桌。"
    ),
    # roundtable_conclusion(高风险圆桌结论落认知库确认卡)
    "proposal.roundtable_conclusion.summary": (
        "圆桌「{topic}」得出了结论 —— 要把它记进你的认知库吗?"
    ),
    "proposal.roundtable_conclusion.risk_shared": (
        "它会进入你的共享认知层(以后每次决策都会被召回)"
    ),
    "proposal.roundtable_conclusion.risk_dissent": (
        "它是带着未解决的分歧收口的(附少数派报告)"
    ),
    "proposal.roundtable_conclusion.risk_no_consensus": (
        "圆桌到了轮数上限仍未达成共识"
    ),
    "proposal.roundtable_conclusion.risk_default": "它被标为高影响",
    "proposal.roundtable_conclusion.basis": (
        "高风险圆桌结论:{risk}。"
        "ACCEPT = 记进认知库(分歧一并留档);"
        "REJECT = 只留在讨论线里,什么都不落库。"
    ),
    "receipt.roundtable_conclusion.ok": "已把这条圆桌结论记进你的认知库。",
    "receipt.roundtable_conclusion.ok_dissent": (
        "已把这条圆桌结论记进你的认知库(保留了 {n} 条分歧记录)。"
    ),
    "receipt.roundtable_conclusion.empty": "这条结论是空的 —— 没什么可记的。",
    "receipt.roundtable_conclusion.no_memory": "认知库未接线 —— 没法记录这条结论。",
    "receipt.roundtable_conclusion.write_failed": "这条结论没记进去:{error}",
    # ops_fix
    "proposal.ops_fix.fallback_summary": "运维诊断",
    "proposal.ops_fix.cause": "可能原因:{cause}",
    "proposal.ops_fix.fix": "建议修法:{fix}",
    "proposal.ops_fix.auto": "ACCEPT 将执行确定性可逆修复(先备份再重置,可从 .corrupt.bak 找回),不调模型改系统。",
    "proposal.ops_fix.manual": "这是 LLM 诊断、未经验证;ACCEPT 只表示你认可,系统不会自动改——请按上面步骤手动处理。",
    # merge_atoms
    "proposal.merge_atoms.head": "把 {n} 个近义原子合并成规范原子「{canon}」:{members}。",
    "proposal.merge_atoms.reason": "判断依据:{reason}",
    "proposal.merge_atoms.why": "合并 = 减少重复、提升复用(护城河:批量导入的原子常因近义不并而 reuse 偏低)。",
    "proposal.merge_atoms.accept": (
        "ACCEPT 会 rewire-before-delete:先把所有引用这些原子的角色改写到规范原子,"
        "再删冗余,绝不留悬空引用;不动也安全(只是不并)。"
    ),
    "proposal.merge_atoms.summary": "合并 {n} 个近义原子 → 「{canon}」",
    # fs_access
    "proposal.fs_access.op_read": "读取",
    "proposal.fs_access.op_write": "写入",
    "proposal.fs_access.op_read_write": "读写",
    "proposal.fs_access.who_role": "角色「{role}」",
    "proposal.fs_access.who_default": "执行中的角色",
    "proposal.fs_access.summary": "{who}请求{op}工作区外路径:{path}",
    "proposal.fs_access.basis": (
        "它在干活时需要碰这个路径,但该路径在你的工作区之外 —— 按最小权限原则默认关闭。"
        "ACCEPT=永久放行该路径(能力总览随时可撤);密钥/凭据类路径永远不会出现在这里(硬地板)。"
    ),
    # merge_knowledge
    "proposal.merge_knowledge.head": "这 {n} 条知识点讲的基本是同一件事:{shown}。",
    "proposal.merge_knowledge.reason": "判断依据:{reason}",
    "proposal.merge_knowledge.accept": (
        "建议合并成一条「{label}」。ACCEPT = 先写入合并条、再删被并旧条(中途失败不丢数据);"
        "不动也安全(只是库里留着近重复)。"
    ),
    "proposal.merge_knowledge.summary": "🧹 合并 {n} 条近重复知识 → 「{label}」",
    # confirm_result
    "proposal.confirm_result.default_role": "角色",
    "proposal.confirm_result.default_req": "这个任务",
    "proposal.confirm_result.basis": (
        "「{role}」为完成「{req}」临时造了 {n} 个新能力:{lines}。"
        "你认可这次结果 → 由 {role} 综合裁哪些值得留进自己的工具箱(被别的角色复用才正式转正);"
        "不处理 / 不认可 → 它们留作试用,长期没人用会被自动清掉。"
    ),
    "proposal.confirm_result.summary": "「{role}」做完「{req}」,新造了 {n} 个能力 —— 认可结果就留有用的?",
    # infeasible_report
    "proposal.infeasible.default_goal": "(未命名目标)",
    "proposal.infeasible.default_role": "角色",
    "proposal.infeasible.attempt_unfinished": "未完成",
    "proposal.infeasible.attempt_line": "第 {i} 次:{term}",
    "proposal.infeasible.attempt_note": "（{note}）",
    "proposal.infeasible.no_trail": "（无轨迹）",
    "proposal.infeasible.basis": (
        "「{role}」为完成「{goal}」自助重规划了 {n} 次仍没成。轨迹:{trail}。"
        "系统靠自动重规划突破不了 —— 这是带证据的结论,不是问你「怎么办」:"
        "请你定夺(接纳并放下 / 暂缓 / 我来调整目标或补资源再试)。"
    ),
    "proposal.infeasible.summary": "「{role}」追求「{goal}」未达成(自助重规划 {n} 次)",
    # inbox(邮件管道)
    "proposal.inbox.no_body": "(无正文)",
    "proposal.inbox_decision.default_reason": "分诊判定需要你拍板",
    "proposal.inbox_decision.default_action": "(见邮件)",
    "proposal.inbox_decision.summary": "📧 需要拍板:{sender} 「{subject}」",
    "proposal.inbox_decision.basis": (
        "这封邮件被分诊为需要你拍板({reason})。建议动作:{action}。"
        "正文摘要:{snippet}。"
        "本管道只通知与建议 —— 未经你确认绝不对外发信;ACCEPT 也只是记录你的决定,"
        "不会自动回信或执行任何外部动作。"
    ),
    "proposal.inbox_reply.default_reason": "分诊判定可以先代拟回复",
    "proposal.inbox_reply.summary": "✉️ 代拟回复待批:{sender} 「{subject}」",
    "proposal.inbox_reply.basis": (
        "这封邮件被分诊为需要回复({reason}),已代拟草稿(可就地修改后再批)。"
        "ACCEPT = 把草稿存进台账并显示给你,由你自行复制发送 —— "
        "系统不代发任何邮件(未经确认绝不对外发信是硬规矩)。"
    ),
    # revise_skill(crystallize/revision)
    "proposal.revise_skill.summary": "技能「{skill}」近几次客观信号差,建议大幅修订方法(重写/删步骤过半,需你过目)",
    "proposal.revise_skill.basis": (
        "触发依据:{trigger};失败样本 traces: {traces}。"
        "改动幅度过大(方法重写/删步骤过半),按问责链升 H2A,不静默换方法。"
    ),
    "proposal.revise_skill.traces_rolled": "(原文已滚动)",
    "proposal.revise_skill.trigger": "confidence={conf}(<{thresh}触发) bad={n_bad}/{total}(≥{min_bad}触发)",
    # external_adopt(external_collab)
    "proposal.external_adopt.default_citizen": "外部同事",
    "proposal.external_adopt.summary": "采纳 {badge} 「{cid}」的产出?(外部执行体·不可信数据)",
    "proposal.external_adopt.basis_head": "这是外部执行体「{badge} {cid}」的产出——不可信数据(它不担你的责、无问责链)。",
    "proposal.external_adopt.basis_ctx": "背景:{ctx}",
    "proposal.external_adopt.basis_tail": (
        "ACCEPT = 你拍板采纳这份产出,它才穿过来源边界(可进记忆/当结论/交给下游角色);"
        "REJECT/不处理 = 只当参考,永不自动进记忆、不触发别人。原始产出:\n{preview}"
    ),
    "proposal.external_adopt.empty": "(空)",
    # spend_budget 提醒卡(llm/spend_budget + console/entry 兜底)
    "proposal.spend.period_month": "本月",
    "proposal.spend.period_day": "今天",
    "proposal.spend.summary_blocked": (
        "预算已用满:{period}已花 {used} / 上限 {limit}（{pct}%）"
        "—— 后台自动任务已暂停,前台照常。要继续请提高上限或改 on_limit。"
    ),
    "proposal.spend.summary_warn": "花费提醒:{period}已花 {used} / 上限 {limit}（{pct}%,达 {tier}%）",
    "proposal.spend.fallback_summary": "花费提醒",
    # resolve_conflict(domain/skill_conflict → proposal_from_conflict)
    "conflict.rule_forbid": "禁止项",
    "conflict.rule_oblige": "强制项",
    "conflict.rule_value": "价值观",
    "conflict.rule_generic": "规则",
    "conflict.summary": "技能「{skill}」可能违反域「{domain}」的{label}「{rule}」({role})",
    "conflict.judge_reason": "技能用途文本命中{label}关键词,疑似冲突,请确认",
    # crystallize_skill ACCEPT 回执(proposal_handlers)
    "receipt.crystallize.default_habit": "这个习惯",
    "receipt.crystallize.accepted": "已采纳「{summary}」— 你继续这样用,系统会自动把它结晶成技能",
    # ---- agent-import 段(docs/84 #2 判型分流:api_agent_import 用户可见 note)----
    "agent_import.note.pure_executor": (
        "这个 agent 是纯执行体(只有能力步骤、无立场、不担你的责)——没有建角色席位。"
        "已落 {n} 个公共原子,任何角色都能组合它们;要给它决策席,请自建角色并绑定这些原子。"
    ),
    "agent_import.note.skill_like": (
        "这个 agent 本质是一段流程剧本(技能),不是「谁」——本次未写入角色库/原子库。"
        "请改走技能库导入。"
    ),
    "agent_import.note.advisory_persona": (
        "这个角色暂无可立即执行的原子(纯人设或需外部集成)。已按顾问角色导入。"
        "要让它真能干活 → 去技能库建或导一个 skill 给它(skill 会落到写代码跑 / 连 MCP)。"
    ),
    "agent_import.note.v0_fallback": "未接 LLM 或拆解未成 → 走确定性 adapter(tools 仅列名,未出原子)",
    # ---- task-insight 段(docs/82 非任务认知沉淀:记忆面板来源列/kind 标签)----
    "memory.source.task_insight": "执行洞察(从运行记录自动沉淀,暂记待核)",
    # ---- memory.source 人话标签(D2 冲突卡「旧那条的来源」+ 记忆面板)----
    "memory.source.fed": "你喂料确认的知识",
    "memory.source.user_edit": "你手动改的",
    "memory.source.cli": "命令行添加的",
    "memory.source.user_explicit": "你直接告诉我的",
    "memory.source.ingest": "你摄入的材料",
    "memory.source.role_experience": "某角色积累的经验",
    # ---- D2 记忆冲突卡(memory_conflict:supersede 要推翻你钉住/人审的记忆 → 不自动,升卡让你裁)----
    "proposal.memory_conflict.summary": (
        "新信息和你确认过的记忆冲突了 —— 保留旧的、采纳新的,还是两条都留?"
        "(旧:「{old}」/ 新:「{new}」)"
    ),
    "proposal.memory_conflict.origin_pinned": "你钉住的({src},{when})",
    "proposal.memory_conflict.origin_reviewed": "{src},{when}",
    "proposal.memory_conflict.basis": (
        "这和一条你确认过/钉住的记忆冲突,我不会背着你悄悄改。\n"
        "• 原有:「{old}」—— {origin}\n"
        "• 新的:「{new}」\n"
        "你裁:保留旧的(丢弃新的)、采纳新的(旧的失效不删、可翻案),或两条都留"
        "(默认 —— 不管选哪个都不丢数据;你不发话就谁都不失效)。"
    ),
    "receipt.memory_conflict.keep_both": "两条都留了 —— 没有失效任何一条。",
    "receipt.memory_conflict.adopt_new": "采纳了新的;旧的已失效(仍在考古层,可翻案)。留下:「{keep}」。",
    "receipt.memory_conflict.keep_old": "保留了旧的;新的已失效。留下:「{keep}」。",
    "receipt.memory_conflict.gone": "那条记忆已经不在了 —— 无需处理。",
    "receipt.memory_conflict.already": "那条已经失效了 —— 无需处理。",
    "receipt.memory_conflict.bad_resolution": "未知裁决「{r}」—— 没有改动。",
    "insight.kind.env": "环境事实",
    "insight.kind.correction": "纠错经验",
    "insight.kind.observation": "顺带观察",
    # ---- schedule-catchup 段(跨天离线追赶:关机期间错过的定时任务 → 聚合补跑确认卡)----
    "proposal.schedule_catchup.summary": (
        "你不在的时候,「{title}」错过了 {n} 次(最近该跑:{when})—— 要补跑一次吗?"
    ),
    "proposal.schedule_catchup.basis": (
        "「{title}」到点时系统没开机,这 {n} 场都没跑成。补跑 = 现在按原意图跑一次,"
        "不是把错过的每一场都重放。你拒绝或不理会就不补,这批旧账下次开机也不会再提。"
    ),
    # ---- schedule-suggest 段(docs/90 刀3c:手动跑到第 N 次 → 温和的"要不要每周自动跑"提示)----
    "proposal.schedule_suggest.summary": (
        "你手动跑了 {n} 次「{intent}」—— 要不要设成定时自动跑?"
    ),
    "proposal.schedule_suggest.basis": (
        "这件事你亲手做了 {n} 次,也许值得自动化。接受不会立刻建定时任务 —— 我会把它带到"
        "定时设置,你补一句「多久一次、几点」(比如「每周一早八」)再确认,我绝不替你瞎定时间。"
        "忽略的话,这条我以后不再提。"
    ),
    "receipt.schedule_suggest.accepted": (
        "已把这件事带到定时设置 —— 告诉我多久一次、几点(比如「每周一早八」),我就替你建。"
        "时间我不替你拍。"
    ),
    # ---- scene 段(docs/94 刀1:场景触发换心脏 —— 当下场景卡文案 + 日预算回执)----
    "proposal.scene_schedule_due.summary": (
        "「{title}」还有 {mins} 分钟就要跑了 —— 上次它失败了。要我现在先试跑一遍吗?"
    ),
    "proposal.scene_schedule_due.basis": (
        "定时任务「{title}」{mins} 分钟内到点,而它上次跑失败了:{err}。"
        "试跑 = 立刻按原意图先跑一次 —— 再失败你能赶在正点前知道。定时任务本身不动。"
    ),
    "scene.big_job.basis_prefix": (
        "你刚跑完「{intent}」(用时约 {mins} 分钟)。"
    ),
    "scene.budget.receipt": (
        "今天的主动建议就这些({n}/{n})—— 要更多跟我说。"
    ),
    # ---- scene-ready 段(docs/94 刀2:预测定向预执行 —— 「已备好」卡 + ACCEPT 落地文案)----
    "proposal.scene_ready.summary": (
        "我趁空已经把「{what}」先干好了 —— 查收?"
    ),
    "proposal.scene_ready.basis_done": (
        "这活我在空闲时先干完了,产物一瞥:{gist}"
    ),
    "proposal.scene_ready.basis_deliver": (
        "接受 = 产物发进我们的会话(文件落进工作区 karvy_prepared/);拒绝或放到过期 = 全部丢弃"
        "—— 你没点头之前,它绝不进你的知识库/记忆/技能。"
    ),
    "proposal.scene_ready.basis_rerun": (
        "接受 = 带着我备好的诊断和修法草稿重跑一次;拒绝或放到过期 = 草稿丢弃,什么都不会跑。"
    ),
    "receipt.scene_ready.gone": (
        "那份备好的产物已过期清理 —— 没有落地任何东西。"
    ),
    "receipt.scene_ready.no_intent": "缺原始意图 —— 没法重跑。",
    "receipt.scene_ready.delivered": (
        "已把提前备好的内容发进会话{files}。"
    ),
    "receipt.scene_ready.files_moved": (
        "(并把 {n} 个文件移进工作区 karvy_prepared/{sid})"
    ),
    "receipt.scene_ready.deliver_failed": (
        "没能把备好的产物送达(会话不可用)—— 它仍留在暂存区,稍后会自动清理。"
    ),
    "receipt.scene_ready.files_only": (
        "会话不可用,文字没发出去{files}。"
    ),
    "scene_ready.deliver.message": "这是我提前备好的:\n\n{text}",
    "scene_ready.deliver.user_line": "✅ 已接受:{what}",
    "scene_ready.rerun.diagnosis_block": (
        "【已备好的失败诊断与修法草稿】\n{text}"
    ),
    "scene_preexec.task_intent": "空闲预执行:{what}",
    "scene_preexec.product_error_diag": (
        "试跑又失败了。错误:{err}\n\n试跑过程中看到的:\n{text}"
    ),
    # ---- system-import 段(docs/84 #3 多 agent 系统导入:plan/apply 用户可见文案)----
    "system_import.note.no_llm": (
        "未接 LLM(--no-llm?),读不了协作拓扑。可以改走常规 agent 导入,把每个 agent 逐个导进来"
        "(每个会各跑一次拆解)。"
    ),
    "system_import.note.triage_failed": (
        "系统读谱没有产出合法结构,拓扑丢失 —— 如实报,未写入任何东西。"
        "可以改走常规 agent 导入,把每个 agent 逐个导进来(每个会各跑一次拆解)。"
    ),
    "system_import.note.skill_agent": (
        "「{name}」本质是一段流程剧本(技能),不是「谁」—— 不落角色/原子;请走技能库导入。"
    ),
    "system_import.note.executor_folded": (
        "「{name}」是纯执行体 —— 只落公共原子(不给决策席);它在流水线里的那一步折进相邻角色的步骤。"
    ),
    "system_import.note.skills_to_import": (
        "识别出内含技能:{skills}。本次没有写入 —— 需要时去技能库导入。"
    ),
    "system_import.identity.report_note": (
        "原系统中 {reporters} 向该角色汇报。KarvyLoop 绝不建 role→role 问责链 —— 问责已重接到你;"
        "该角色以「评审它们的产出」为职务,不是它们的上级。"
    ),
    "system_import.relocate.supervisor": (
        "supervisor 的静态分派权已上移:小卡规划、你拍板 —— 没有任何 agent 对另一个 agent 握有路由权。"
    ),
    "system_import.task.step_fallback": "承接上游产出,完成你负责的这一环。",
    "system_import.task.review": "评审「{target}」的产出:扎实吗?完整吗?下游能直接用吗?",
    "system_import.workflow.name": "{domain}·导入流程",
    "system_import.seed.topic_fallback": "围绕系统目标开一场圆桌",
    "system_import.degrade.topology.why": "协作拓扑没能读成合法结构。",
    "system_import.degrade.topology.fallback": "拓扑丢失(如实报)。各 agent 可走常规 agent 导入逐个导。",
    "system_import.degrade.dynamic_route.why": (
        "动态路由(走哪条分支由某个 agent 运行时现场决定)—— workflow 模板装不下运行时路由器。"
    ),
    "system_import.degrade.dynamic_route.fallback": (
        "已静态化成顺序依赖;或不结晶这一跳,每次由小卡现规划。"
    ),
    "system_import.degrade.loop.why": "workflow 模板不支持循环(诚实 P1)。",
    "system_import.degrade.loop.fallback": (
        "循环边已丢弃。替代:编辑模板时手工展开成 ≤2 轮显式步骤,或改开圆桌。"
    ),
    "system_import.degrade.report_chain.why": (
        "原系统里「{src}」向「{dst}」汇报 —— KarvyLoop 绝不建 role→role 问责链"
        "(问责只有 role→你 / atom→role 两种)。"
    ),
    "system_import.degrade.report_chain.fallback": (
        "降级为评审步 + 职务写进「{dst}」的 IDENTITY;问责已重接到你。"
    ),
    "system_import.degrade.blackboard.why": (
        "原系统的 agent 之间共享一块可写黑板/共享状态 —— KarvyLoop 没有共享可写板。"
    ),
    "system_import.degrade.blackboard.fallback": (
        "上游产出直接喂下游步骤(inputs),沉淀性的知识进域知识库。"
    ),
    "system_import.degrade.schedule.why": "原系统里「{agent}」定时常驻({when})。",
    "system_import.degrade.schedule.fallback": (
        "本次不落(daemon 原子 + 域 Routine 是二期)。现在就要的话,去 ⏰ 定时任务里重建一条。"
    ),
    "system_import.apply.missing_registry": "运行时注册表未接线(缺 atom/role/domain registry)。",
    "system_import.apply.no_domain_name": "plan 里没有域名 —— 先给落地的业务域起个名字。",
    "system_import.apply.same_name": (
        "已有同名活跃业务域「{name}」—— 换个名字,或先归档旧的。本次未写入任何东西。"
    ),
    "system_import.apply.bad_role_id": "非法角色 id「{role_id}」(只能含字母/数字/下划线/连字符)。本次未写入任何东西。",
    "system_import.apply.bad_kind": "角色「{role_id}」的判型非法(decision/executor/hybrid/skill 四选一)。本次未写入任何东西。",
    "system_import.apply.failed": "落地中途失败:{error}。本次新建的已全部回滚 —— 不留孤儿。",
    "system_import.apply.per_agent_mode": (
        "这份 plan 是逐个导入的降级模式(拓扑已丢失)—— 没有可作为系统落地的内容。"
        "请走常规 agent 导入逐个导。"
    ),
    # ---- docs/88 外环 Pursuit(招牌"闭环完整性":跨天持久目标)----
    "proposal.pursuit_commit.no_trigger": "(无)",
    "proposal.pursuit_commit.summary": "承诺一个跨天目标:「{statement}」?",
    "proposal.pursuit_commit.basis": (
        "这是个跨天的持久目标 —— 我会自己一直推进,只在 承诺 / 修订 / 完成 三个点回来找你。"
        "完成判据(确定性,绝不问模型):{gate}。修订触发:{trigger}。"
        "ACCEPT = 你承诺它、我开始替你自跑几天;REJECT / DEFER = 不承诺。"
    ),
    "proposal.pursuit_revise.reason_default": "命中了一个修订触发器",
    "proposal.pursuit_revise.summary": "一个追求到了修订点 —— 要你拍板:「{statement}」",
    "proposal.pursuit_revise.basis": (
        "原因:{reason}。改方向是你的决策 —— 我绝不自己给追求改向。"
        "ACCEPT = 把这个追求放下(想换方向就新建一条;自动重规划是后面的刀);"
        "REJECT / DEFER = 留着挂起,我不自动跑它。"
    ),
    "receipt.pursuit.no_store": "Pursuit 未接线(--no-llm 启动?)—— 没法处理。",
    "receipt.pursuit.gone": "这个追求已不在(可能已完成或放下)。",
    "receipt.pursuit.terminal": "这个追求已是 {status} 态 —— 无需处理。",
    "receipt.pursuit_commit.already": "已经承诺过了 —— 我正在替你跑。",
    "receipt.pursuit_commit.ok": "已承诺:「{statement}」—— 我会一直推进,到 完成 / 修订 再回来找你。",
    "receipt.pursuit_revise.dropped": (
        "已放下「{statement}」—— 想换方向就新建一条追求(自动重规划是后面的刀)。"
    ),
    "receipt.pursuit_revise.resumed": (
        "接着追:「{statement}」—— 我会继续推进,完成 / 需改方向时再来找你。"
    ),
    "pursuit.progress.done": "已完成 —— 目标的验证门通过了",
    "pursuit.progress.transferred": "已被你的设备「{device}」接管 —— 这台设备站开。",
    "pursuit.progress.remote_done": "已在你的设备「{device}」上完成。",
    "pursuit.receipt.done": "✅ 追求达成:「{statement}」(验证门通过)",
    "pursuit.revise.reason_trigger": "命中了一个修订触发器",
    "pursuit.revise.reason_max_advances": "推进 {n} 次仍没过完成门 —— 你来定(继续 / 改方向 / 放弃)",
    "pursuit.revise.reason_consecutive_failures": "连续 {n} 轮推进都失败了,先暂停 —— 你来定(继续 / 改方向 / 放弃)",
    "pursuit.triage.duplicate": "已经在追这个了 ——「{statement}」就在你的追求里,不再开第二条。如果这真是个不同的新目标,去「我的追求」面板建。",
    "pursuit.triage.duplicate_paused": "这个目标先前暂停了 ——「{statement}」就在你的追求里、等你拍板。去「我的追求」面板点「继续」把它接着跑起来(不再开第二条)。",
    "pursuit.err.gate_not_dict": "完成判据必须是一个带类型的对象。",
    "pursuit.err.gate_type": "完成判据目前只支持这几种:{allowed}。",
    "pursuit.err.gate_cmd": "「跑测试」这类完成判据要填一条要跑的命令(命令退出码 0 就算完成)。",
    "pursuit.err.gate_cmd_unsplittable": "这条要跑的命令解析不出可执行命令:{cmd}",
    "pursuit.err.gate_path": "「看文件在不在」这类完成判据要填要检查的文件路径。",
    "pursuit.err.gate_path_placeholder": "文件路径不能含 {{...}} 占位符(暂不支持路径模板):{path}",
    "pursuit.err.no_store": "Pursuit 未接线(--no-llm 启动?)。",
    "pursuit.err.not_found": "这条追求已经不在了(可能刚完成或被放下)。",
    "pursuit.err.terminal_no_resume": "这条追求已经是 {status} 了 —— 没什么可继续的。",
    "pursuit.err.terminal_no_drop": "这条追求已经是 {status} 了 —— 没什么可放下的。",
    "pursuit.err.bad_pursuit": "建 Pursuit 失败:{error}",
    "pursuit.gate_desc.test_pass": "命令 `{cmd}` 退出码为 0",
    "pursuit.gate_desc.file_exists": "文件 `{path}` 存在",
    # docs/88 真伤7:test_pass 门 fail-loud 原因(cognition 层出稳定码 → 这里出人话进 progress_note)
    "pursuit.gate_note.no_isolation": "这台设备没有真正的隔离沙箱,无法安全地跑这个测试判据。",
    "pursuit.gate_note.net_downgrade": "跑完成判据时没有网络隔离(这台设备做不出网络隔离)。",
    "pursuit.gate_note.timed_out": "完成判据跑得太久,被中止了。",
    "pursuit.gate_note.net_suspect": "完成判据大概连不上网(已被隔离)。",
    # docs/88 第三刀 #2:「让小卡讲讲」的确定性兜底(gateway 无/失败/空回复时用;零 LLM)
    "pursuit.narrate.fb_advances": "到现在已经推进了 {n} 次。",
    "pursuit.narrate.fb_last_ok": "最近一轮跑完了。",
    "pursuit.narrate.fb_last_fail": "最近一轮卡住了:{err}",
    "pursuit.narrate.fb_stuck": "连着卡了 {n} 轮 —— 等你拍板。",
    "pursuit.narrate.fb_progress": "现在的进展:{note}",
    "pursuit.narrate.fb_none": "这条还没开始动 —— 下一轮我会带上它。",
    # docs/88 第二刀:聊天判型 create(小卡识别跨天目标 → 升承诺卡)的聊天回执 + REJECT 清理回执
    "pursuit.triage.card_text": (
        "听起来这是个要跨几天一直推进的目标:「{statement}」。"
        "完成判据(每轮推进后我都确定性核一遍,不问模型):{gate}。"
        "我把它包成了一张承诺卡 —— 你点了同意才算数;之后每轮推进会派生任务,"
        "最多 {max_rounds} 轮没过完成判据就自动暂停来问你。(到 🤝 H2A 处置)"
    ),
    "receipt.pursuit_commit.rejected_cleaned": (
        "好,不追「{statement}」了 —— 这个目标的记录已清掉,不留垃圾。想追随时再说一声。"
    ),
    # D(内测 U-06)多模态降级:模型**显式声明** text-only → 图不拼进请求,占位一句人话(拼在该轮 user 内容里)
    "executor.images_unsupported": (
        "(附了 {n} 张图,但当前模型 {model} 看不了图(它的配置声明只收文本)—— 文字我照常处理。"
        "要用图,换一个支持视觉的模型;若这个模型其实能看图,在模型配置的 `input_modalities` 里补上 `image`。)"
    ),
    # 配置外改检测(内测实拍:终端+WebUI 双写 config,坏配置潜伏到重启才炸)
    "config.external_reloaded": (
        "检测到模型配置在本控制台之外被修改(终端/编辑器)—— 已重新加载。"
        "如果那次修改不是你想要的,去「模型(全局)」检查。"
    ),
    "config.external_reload_failed": (
        "检测到模型配置在本控制台之外被修改(终端/编辑器),但新配置加载失败:{reason} —— "
        "修好之前聊天可能会失败,去「模型(全局)」处理。"
    ),
    # 任务失败咽喉人话化(内测实拍:聊天气泡糊「✗ infra_dead」裸码)
    "task.err.infra_dead": (
        "⚠ 模型服务不可用(模型/网络/沙箱调不通)—— 这不是任务本身的问题。"
        "去「模型(全局)」检查配置和网络,然后重跑。"
    ),
    "task.err.max_turns": "⚠ 达到单次执行步数上限,没做完 —— 重跑可继续。",
    "task.err.blocking_limit": "⚠ token/成本预算用尽,没做完 —— 调高预算或重跑。",
    "task.err.circuit_open": "⚠ 连续失败触发断路,已停下 —— 看看哪步卡住了再重跑。",
    "task.err.aborted": "⚠ 执行被中断,结果可能不完整。",
    "task.err.hook_stopped": "⚠ 被规则/钩子拦下停止。",
    # D② drive 聊天路径 provider 错误人话化(人话在前,真因原文在后 —— fail-loud 不丢真因)
    "drive.err.bad_key": (
        "模型服务拒绝了密钥(401/403)—— 检查模型配置里的 API key。(真因: {cause})"
    ),
    "drive.err.bad_url": (
        "模型端点地址不对(404)—— 检查模型配置里的 base_url/路径。(真因: {cause})"
    ),
    "drive.err.unreachable": (
        "连不上模型服务(网络/超时)—— 检查网络或端点后再试。(真因: {cause})"
    ),
    "drive.err.bad_request": (
        "模型服务拒绝了这次请求(4xx)。通常是请求里带了这个模型不支持的内容"
        "(比如给纯文本模型发图),或协议不匹配。这次没跑成 —— 调整模型配置"
        "或去掉不支持的内容后再试。(真因: {cause})"
    ),
    # K① 决策卡/路由提示(此前硬编码中文 f-string)
    "report.approach_route": "由「{role}」在域治理下执行",
    "report.approach_rerun": "重跑「{intent}」",
    "route.roundtable_hint": (
        "想让 {who} 一起讨论 —— 这是开**圆桌**(几个人坐一起),不是交给一个人。"
        "要在「{group}」开桌讨论「{topic}」吗?(到 🤝 H2A 处置)"
    ),
    "route.delegate_hint": (
        "这件事属于业务域「{domain_name}」 — 要不要转给「{role}」去做?(到 🤝 H2A 处置)"
    ),
    # U-03:私聊小卡 @角色 → 快通道委派卡(点名=已填好的单,拍一下就开工)
    "route.mention_fastlane_hint": "已给「{role}」备好委派单 —— 右边拍一下就开工。",
    "route.mention_multi_hint": (
        "这里一次 @ 一位;想让几个人一起干,去群里 @ 他们,或说「开个圆桌」。"
    ),
    "route.mention_no_domain": "个人",
    # K② 卡侧 LLM prompt 的应答语言指令(跟界面语言;拼进 system prompt 末尾)
    "prompt.lang.answer": "用中文回答。",
    "prompt.lang.json_why": "\"why\" 字段用中文写。",
    "prompt.lang.title": "主题名用中文。",
    # I:CLI run 默认路径的即时阶段提示(stderr;后续工具/文本事件实时流)
    "cli.run.progress_start": (
        "[karvyloop] 运行中:先检索技能 → 没命中再调模型;下面会实时流工具/文本事件"
    ),
}
