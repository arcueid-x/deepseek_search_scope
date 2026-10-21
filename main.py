"""DeepSeek 搜索范围限制插件。

在 AstrBot 内置 DeepSeek 搜索工具的调用边界注入域名限制：

- 搜索（web_search_deepseek）：改写 query，注入 site: / -site: 操作符
  （DeepSeek Search API 原生支持），并对返回结果做域名二次过滤（双保险）。
- 网页提取（deepseek_fetch_web_page）：校验 URL 域名，越界直接拒绝，
  不发起 API 请求。

要求 AstrBot >= 4.29.1（该版本起内置 DeepSeek 搜索工具）。
插件配置见 _conf_schema.json，纯逻辑见 scope_restrict.py。
"""

from astrbot.api.star import Context, Star

from .scope_restrict import (
    check_fetch_url,
    filter_search_results,
    normalize_sites,
    rewrite_query,
)

# 打在工具实例上的补丁标记，避免重复包装。
_WRAPPED_ATTR = "_deepseek_search_scope_wrapped"


class DeepSeekSearchScope(Star):
    """限制 DeepSeek 网页搜索/网页提取只能查询指定网站的插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        # 插件配置由 AstrBot 依据 _conf_schema.json 解析后传入（dict 子类）。
        self.config = config or {}
        self._wrapped_tools: list = []

    async def initialize(self) -> None:
        """加载插件时包装内置 DeepSeek 搜索工具的单例实例。"""
        try:
            from astrbot.core.tools.web_search_tools import (
                DeepSeekFetchWebPageTool,
                DeepSeekWebSearchTool,
            )
        except ImportError as e:
            self.logger.warning(
                "未找到 AstrBot 内置 DeepSeek 搜索工具，插件不生效"
                f"（需要 AstrBot >= 4.29.1）: {e}"
            )
            return

        try:
            tool_mgr = self.context.get_llm_tool_manager()
            search_tool = tool_mgr.get_builtin_tool(DeepSeekWebSearchTool)
            fetch_tool = tool_mgr.get_builtin_tool(DeepSeekFetchWebPageTool)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"获取内置 DeepSeek 搜索工具失败，插件不生效: {e}")
            return

        self._wrap_search_tool(search_tool)
        self._wrap_fetch_tool(fetch_tool)

        cfg = self._read_config()
        self.logger.info(
            "DeepSeek 搜索范围限制已启用（模式: %s，站点: %s）",
            cfg["mode"],
            ", ".join(cfg["sites"]) or "未配置（不限制）",
        )

    async def terminate(self) -> None:
        """卸载插件时还原内置工具的原始 call 方法。"""
        for tool in self._wrapped_tools:
            try:
                object.__delattr__(tool, "call")
            except AttributeError:
                pass
            try:
                object.__delattr__(tool, _WRAPPED_ATTR)
            except AttributeError:
                pass
        self._wrapped_tools.clear()
        self.logger.info("DeepSeek 搜索范围限制已卸载，已还原内置工具。")

    # ---- 内部实现 ----

    def _read_config(self) -> dict:
        """读取插件配置并规范化。

        Returns:
            包含 enable/mode/sites/restrict_search/filter_results/
            restrict_fetch/allow_subdomains 的字典。
        """
        cfg = self.config or {}
        mode = str(cfg.get("mode", "whitelist"))
        if mode not in ("whitelist", "blacklist"):
            mode = "whitelist"
        return {
            "enable": bool(cfg.get("enable", True)),
            "mode": mode,
            "sites": normalize_sites(cfg.get("sites")),
            "restrict_search": bool(cfg.get("restrict_search", True)),
            "filter_results": bool(cfg.get("filter_results", True)),
            "restrict_fetch": bool(cfg.get("restrict_fetch", True)),
            "allow_subdomains": bool(cfg.get("allow_subdomains", True)),
        }

    def _wrap_search_tool(self, tool) -> None:
        """包装 web_search_deepseek：注入查询限制并过滤返回结果。

        Args:
            tool: 内置 DeepSeekWebSearchTool 的单例实例。
        """
        if getattr(tool, _WRAPPED_ATTR, False):
            return
        original_call = tool.call
        plugin = self

        async def restricted_call(context, **kwargs):
            cfg = plugin._read_config()
            if not cfg["enable"] or not cfg["sites"]:
                return await original_call(context, **kwargs)

            query = kwargs.get("query")
            if cfg["restrict_search"] and isinstance(query, str) and query.strip():
                kwargs["query"] = rewrite_query(query, cfg["mode"], cfg["sites"])

            result = await original_call(context, **kwargs)
            if cfg["filter_results"] and isinstance(result, str):
                return filter_search_results(
                    result,
                    cfg["mode"],
                    cfg["sites"],
                    cfg["allow_subdomains"],
                )
            return result

        object.__setattr__(tool, "call", restricted_call)
        object.__setattr__(tool, _WRAPPED_ATTR, True)
        self._wrapped_tools.append(tool)

    def _wrap_fetch_tool(self, tool) -> None:
        """包装 deepseek_fetch_web_page：校验 URL 域名后再发起请求。

        Args:
            tool: 内置 DeepSeekFetchWebPageTool 的单例实例。
        """
        if getattr(tool, _WRAPPED_ATTR, False):
            return
        original_call = tool.call
        plugin = self

        async def restricted_call(context, **kwargs):
            cfg = plugin._read_config()
            if cfg["enable"] and cfg["restrict_fetch"] and cfg["sites"]:
                url = kwargs.get("url")
                if isinstance(url, str):
                    blocked = check_fetch_url(
                        url,
                        cfg["mode"],
                        cfg["sites"],
                        cfg["allow_subdomains"],
                    )
                    if blocked:
                        return blocked
            return await original_call(context, **kwargs)

        object.__setattr__(tool, "call", restricted_call)
        object.__setattr__(tool, _WRAPPED_ATTR, True)
        self._wrapped_tools.append(tool)
