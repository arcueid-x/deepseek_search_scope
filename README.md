# DeepSeek 搜索范围限制 (deepseek_search_scope)

限制 AstrBot 内置的 DeepSeek 网页搜索范围，指定只允许（或禁止）查询哪些网站。

AstrBot 的「网页搜索」能力是全局开关：启用后，机器人可以搜索整个互联网。本插件在
DeepSeek 搜索工具的调用边界加上域名限制，让机器人只在指定网站范围内检索。

## 工作原理

插件在加载时包装 AstrBot 内置的两个 DeepSeek 工具实例
（`web_search_deepseek` 与 `deepseek_fetch_web_page`），每次调用时执行三层限制：

1. **查询注入（源头限制）**
   改写搜索 query，注入 DeepSeek Search API 原生支持的 `site:` / `-site:` 操作符：
   - 白名单：`原查询 site:github.com OR site:huggingface.co`
   - 黑名单：`原查询 -site:example.com -site:spam.net`

2. **结果过滤（双保险）**
   即使 `site:` 操作符未被搜索引擎完整执行，返回结果也会在交给模型前按域名过滤，
   并重新编号来源引用，保证 `<ref>abcd.1</ref>` 之类的引用与来源一一对应。

3. **网页提取拦截**
   调用 `deepseek_fetch_web_page` 提取网页正文时校验 URL 域名，越界直接拒绝，
   不发起 API 请求、不产生费用。

所有限制逻辑位于 `scope_restrict.py`（纯函数、无 AstrBot 依赖），
工具包装逻辑位于 `main.py`。

## 安装

- **AstrBot 版本要求**：>= 4.29.1（该版本起内置 DeepSeek 搜索工具）
- **前置配置**：WebUI `配置文件 → AI → 能力 → 网页搜索` 中启用网页搜索，
  提供商选择 `DeepSeek Search API` 并填写 API Key

安装方式二选一：

1. WebUI 插件页 →「安装插件」→ 输入本仓库地址；
2. 或下载本仓库 zip，在插件页上传安装。

## 配置

打开插件「配置」：

| 配置项 | 说明 |
| --- | --- |
| 启用插件 | 总开关，关闭后不进行任何拦截 |
| 限制模式 | 白名单（只允许列表中的网站）/ 黑名单（禁止列表中的网站） |
| 域名列表 | 每行一个域名，如 `docs.astrbot.app`、`github.com`；可直接粘贴完整网址；条目同时匹配其子域名 |
| 注入 site: 操作符限制搜索查询 | 查询注入开关 |
| 过滤搜索结果 | 结果二次过滤开关（建议开启） |
| 限制网页正文提取 | 提取 URL 域名校验开关 |
| 域名条目同时匹配其子域名 | 子域名匹配开关 |

> 白名单模式下列表为空 = 不限制；黑名单模式下列表为空 = 不拦截任何网站。

## 常见问题

**为什么搜索有时返回"未在允许的网站范围内找到结果"？**
说明白名单内的网站没有相关内容。可以扩充域名列表，或改用黑名单模式。

**插件对搜索提供商有要求吗？**
有。本插件只作用于 `DeepSeek Search API` 提供商；切换到 Tavily、SerpApi 等
其他提供商时不生效。

**AstrBot 升级后插件会失效吗？**
插件做了容错：若内置工具名称/位置变更导致包装失败，会记录警告日志并优雅降级，
不会影响机器人正常运行。

## 开发与测试

```bash
# 纯逻辑单元测试（无需安装 AstrBot）
python test_scope_restrict.py

# 语法检查
python -m py_compile main.py scope_restrict.py
```

## 发布

1. 把本仓库推送到你自己的 GitHub 仓库；
2. 检查 `metadata.yaml` 中的 `repo`、`author` 与你的仓库一致；
3. 前往 [AstrBot 插件发布页面](https://cloud.astrbot.app/publish) 注册账号并提交仓库；
4. 插件 zip 包不超过 16MB（本插件仅含源码，远小于限制）。

## License

MIT
