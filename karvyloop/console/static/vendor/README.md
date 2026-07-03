# vendored 前端库(本地优先,不走 CDN)

| 文件 | 库 | 版本 | License | 源 |
|---|---|---|---|---|
| `markdown-it.min.js` | markdown-it | 14.1.0 | MIT | https://github.com/markdown-it/markdown-it |
| `purify.min.js` | DOMPurify | 3.1.6 | Apache-2.0 / MPL-2.0 | https://github.com/cure53/DOMPurify |
| `highlight.min.js` + `highlight-github.min.css` | highlight.js | 11.9.0 | BSD-3-Clause | https://github.com/highlightjs/highlight.js |
| `driver.min.js` + `driver.min.css`(+ `driver-LICENSE.txt`) | driver.js | 1.3.6 | MIT | https://github.com/kamranahmedse/driver.js |

为何 vendor:KarvyLoop 本地优先,console 不依赖运行时 CDN;markdown 渲染 + HTML 消毒是
**通用基建**(Q5"通用基建必借"),直接用成熟库,不手搓(手搓 markdown/消毒 = 重造 + XSS 风险)。

更新:`curl -sSL -o <file> <cdn jsdelivr npm 链接>`,改本表的版本。

新手引导 tour 用 driver.js(MIT,零依赖 ~5KB gz):intro.js / shepherd 是 AGPL,与 Apache-2.0 冲突,**不许用**(docs/46 §6)。按需加载:首启 tour / 重看引导时才注入,不压常驻包。
