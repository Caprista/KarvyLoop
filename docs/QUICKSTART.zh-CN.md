# 快速上手 —— 从安装到第一个结晶技能,大约 10 分钟

> 🌐 **语言**: [English](QUICKSTART.md) · **中文(当前)**

这是从 `install` 到 KarvyLoop 开始变成"你的"的最短诚实路径:一件重复的任务变成一份写下来的**技能**,控制台把回执亮给你看。

**开始前你需要什么 —— 不藏着掖着:**

- 机器上有 **Python 3.11+**。
- **你自己的模型 API key。** KarvyLoop 不捆绑任何模型,也不经任何人的云中转 —— 它从你的机器直连*你*选的服务商。任何 Anthropic 兼容或 OpenAI 兼容端点都能跑(`base_url` + key 就够)。一个 key 都不想要?本地 [Ollama](https://ollama.com) 也支持 —— 小模型效果会糙一些,但数据一步不出门。
- **受支持的操作系统。** Linux 是一等公民(完整沙箱:bubblewrap,内核支持时再加 Landlock 加固);macOS 受支持(内置 Seatbelt 沙箱,同一套 fail-closed 契约);Windows 受支持但有诚实边界:受限令牌沙箱做写隔离和资源上限,需要网络的第三方技能宁可拒跑也不裸奔,沙箱起不来时降级为"仅第一方可用"。沙箱之外的一切都是纯跨平台 Python。

只想先看看、不想配 key?`karvyloop console --no-llm` 会启动一个只读控制台。

---

## 第 0–2 分钟 —— 安装

一条命令,与系统 Python 隔离(在 PEP 668"外部管理"发行版上也安全):

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/Caprista/KarvyLoop/main/scripts/install.ps1 | iex
```

安装器会创建一个专用虚拟环境,并把 `karvyloop` 命令放上你的 PATH —— 没有别的要配。重跑即原地升级。(对着 clone 开发?`pip install -e .` —— 装完找不到 `karvyloop` 命令时,`python -m karvyloop` 永远能用。)

## 第 2–5 分钟 —— 接上一个模型

启动控制台:

```bash
karvyloop console --host 127.0.0.1 --port 8766
# 打开 http://127.0.0.1:8766
```

首次运行,控制台会出一个**配置向导**:选你的 AI 从哪来,粘贴 key,它会**先验证 key 真的能用**再放你进去。

内置预设(每条都带"去哪拿 key"的链接):**Anthropic (Claude)** · **OpenAI** · **DeepSeek** · **Kimi / Moonshot** · **OpenRouter**(一把 key 用很多模型) · **Ollama**(本地,免 key)。没列出的走通用 adapter —— 任何 OpenAI 兼容端点,`base_url` + key 即跑(怪端点可加 `extra_headers`)。

你的 key 写进 `~/.karvyloop/config.yaml` —— **在你的磁盘上、任何仓库之外、绝不上传**。习惯终端?`karvyloop init` 在 shell 里跑同一个向导;手写 YAML 的方式见 [README](../README.zh-CN.md#快速开始)。

## 第 5–7 分钟 —— 跑第一个任务

你会落在与内置助手**小卡 🦫** 的私聊里。说一件小而具体的事:

> *"列出我工作区里最大的 5 个文件"*

**你应该看到:**它真的跑起来了 —— 在沙箱里跑,工具调用可见 —— 然后把结果流式返回。这就是**执行 loop**:发现 → 干 → 验证 → 返回。幕后,这次运行被记进了 **Trace** —— 一条只增不改的日志,之后所有评价都从它派生。

## 第 7–9 分钟 —— 再跑一次,看它复利

再做一次同*类*的任务 —— 原样再问,或换个变体("……在我的下载目录里")。这是 KarvyLoop 和那些隔夜就忘了你的工具分岔的地方:

- **你一边打字**,相关的技能和知识就主动浮出在 **🧲 相关的料** 面板里 —— 纯本地匹配,零额外 LLM 调用。
- **重复够了之后** —— 简单任务最早第三次就够,只要过了晋级门(有验证门且通过、成功率 ≥80%、用得够多或跨变体泛化)—— 你会看到 **🔔 已结晶: {skill}**。你反复问的那件事,现在是一份写在可读的 `SKILL.md` 里的*方法*。
- **之后再跑**,召回回执来了:标为 *stable* 的技能瞬时回放(**⚡ 快脑命中**,零 LLM 成本);标为 *dynamic* 的技能(默认)绝不回放旧答案 —— 存下来的方法制导一次用今天的输入重跑,并且这次重跑会记在技能自己的时间线上。

诚实说明:结晶是挣来的,不是即时的。它刻意拒绝把一次侥幸存下来 —— 没有验证门,就没有晋级。

## 第 9–10 分钟 —— 打开技能面板

左侧导航 → **技能**。这是会复利的那个"wow":

- **📈 技能库成长曲线 —— 越用越像你**:一条真实曲线(技能数、晋级数、平均成功率、复用命中率),从 Trace 回放而来 —— 不是虚荣计数器。不用的技能会诚实衰减、最终归档;曲线会跌也会涨。
- 每个技能有一条**生命线**:结晶 → 修订 → 重跑,让"我的技能为什么变了"永远有答案。
- **能力总览**卡把每个技能、每个工具被允许碰什么,摆在一张表里。

到这里,loop 闭合了一整圈:跑 → 验证 → 结晶 → 召回。从此它自己复利。

## 接下来去哪

- **组建团队** —— 建一个*域*(像一家公司),配几个*角色*(像同事),然后让小卡把活儿交出去。不点头就不动:AI 提议、*你*拍板,永远如此 —— 一张**决策卡**先出现。[README 的「头 15 分钟(引导版)」](../README.zh-CN.md#头-15-分钟引导版)会带你走完。
- **[架构](ARCHITECTURE.zh-CN.md)** —— 双循环、实体阶梯、结晶化、决策卡、挣来的静音、Trace 和沙箱是怎么咬合在一起的。
- **[概念](CONCEPTS.zh-CN.md)** —— 一页纸的词汇表。
- **[理念](PHILOSOPHY.zh-CN.md)** —— 为什么非要"loop 原生"。

## 出问题了?

- `karvyloop doctor --fix` —— 诊断你的安装,安全的确定性故障自己修,还会探测模型端点是不是真的可达。
- `karvyloop: command not found` → `python -m karvyloop console`(用你安装时那个 Python),或看 [README 的 PATH 说明](../README.zh-CN.md)。
- 配置时 key 被拒?检查是否完整复制(没有空格/换行/占位文本),以及服务商和 key 的格式是否匹配。
- 还是卡住 → [提 issue](https://github.com/Caprista/KarvyLoop/issues)。Bug 报告是金子 —— 这是一个快速迭代中的 pre-1.0 项目。

---

🦫 *Your agent, your data, your rules.(你的 agent,你的数据,你说了算。)*
