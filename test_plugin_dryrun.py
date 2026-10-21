"""main.py 的干跑集成测试：以 stub 模块模拟 AstrBot 运行时。

在未安装 AstrBot 的环境中验证插件完整生命周期：
导入 -> 实例化 -> initialize 包装内置工具 -> 工具调用改写与过滤 -> terminate 还原。

运行：

    python test_plugin_dryrun.py
"""

import asyncio
import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any


# ---- 构造 astrbot 模块树 stub ----

def _install_stub_modules() -> None:
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api_star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    core.__path__ = []
    core_tools = types.ModuleType("astrbot.core.tools")
    core_tools.__path__ = []

    class _FakeLogger:
        def info(self, msg, *args):
            print("[plugin-log] INFO " + msg % args)

        def warning(self, msg, *args):
            print("[plugin-log] WARN " + msg % args)

    class _FakeContext:
        """模拟 astrbot.api.star.Context。"""

        def __init__(self, tool_manager):
            self._tool_manager = tool_manager

        def get_llm_tool_manager(self):
            return self._tool_manager

    class _FakeStar:
        """模拟 astrbot.core.star.base.Star（保留 __init_subclass__ 注册语义）。"""

        def __init__(self, context, config=None):
            self.context = context
            self.logger = _FakeLogger()

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)

    api_star.Context = _FakeContext
    api_star.Star = _FakeStar

    astrbot.api = api
    astrbot.api.star = api_star
    astrbot.core = core
    astrbot.core.tools = core_tools

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.star": api_star,
            "astrbot.core": core,
            "astrbot.core.tools": core_tools,
        }
    )


@dataclass
class _FakeDeepSeekSearchTool:
    """模拟内置 DeepSeekWebSearchTool 单例。"""

    name: str = "web_search_deepseek"
    description: str = "Search the web using DeepSeek."
    parameters: dict = field(default_factory=dict)
    queries: list = field(default_factory=list)

    async def call(self, context, **kwargs):
        self.queries.append(dict(kwargs))
        return json.dumps(
            {
                "results": [
                    {
                        "title": "good",
                        "url": "https://docs.astrbot.app/x",
                        "snippet": "s",
                        "index": "abcd.1",
                    },
                    {
                        "title": "bad",
                        "url": "https://evil.com/x",
                        "snippet": "s",
                        "index": "abcd.2",
                    },
                ]
            },
            ensure_ascii=False,
        )


@dataclass
class _FakeDeepSeekFetchTool:
    name: str = "deepseek_fetch_web_page"
    description: str = "Fetch a web page."
    parameters: dict = field(default_factory=dict)
    urls: list = field(default_factory=list)

    async def call(self, context, **kwargs):
        self.urls.append(kwargs.get("url"))
        return f"URL: {kwargs.get('url')}\nTitle: t\nContent: c"


class _FakeToolManager:
    def __init__(self, search_cls, fetch_cls):
        self.search_cls = search_cls
        self.fetch_cls = fetch_cls
        self.search_tool = search_cls()
        self.fetch_tool = fetch_cls()

    def get_builtin_tool(self, tool_cls):
        return (
            self.search_tool
            if tool_cls is self.search_cls
            else self.fetch_tool
        )


async def main() -> None:
    _install_stub_modules()

    # 在 stub 就位后以包形式导入插件（模拟 AstrBot 的 data.plugins.<目录>.main）
    from pathlib import Path
    import importlib.util

    plugin_root = Path(__file__).resolve().parent
    pkg = types.ModuleType("deepseek_search_scope")
    pkg.__path__ = [str(plugin_root)]
    sys.modules["deepseek_search_scope"] = pkg

    web_search_tools = types.ModuleType("astrbot.core.tools.web_search_tools")
    web_search_tools.DeepSeekWebSearchTool = _FakeDeepSeekSearchTool
    web_search_tools.DeepSeekFetchWebPageTool = _FakeDeepSeekFetchTool
    sys.modules["astrbot.core.tools.web_search_tools"] = web_search_tools

    spec = importlib.util.spec_from_file_location(
        "deepseek_search_scope.main",
        plugin_root / "main.py",
        submodule_search_locations=[str(plugin_root)],
    )
    plugin_main = importlib.util.module_from_spec(spec)
    sys.modules["deepseek_search_scope.main"] = plugin_main
    spec.loader.exec_module(plugin_main)

    tool_manager = _FakeToolManager(
        _FakeDeepSeekSearchTool,
        _FakeDeepSeekFetchTool,
    )
    context = plugin_main.Context(tool_manager)
    config = {
        "enable": True,
        "mode": "whitelist",
        "sites": ["docs.astrbot.app", "github.com"],
        "restrict_search": True,
        "filter_results": True,
        "restrict_fetch": True,
        "allow_subdomains": True,
    }

    star = plugin_main.DeepSeekSearchScope(context, config)
    await star.initialize()

    # ---- 搜索：query 注入 + 结果过滤 ----
    result = await tool_manager.search_tool.call("MOCK_CTX", query="DeepSeek release")
    sent_query = tool_manager.search_tool.queries[0]["query"]
    assert sent_query == (
        "DeepSeek release site:docs.astrbot.app OR site:github.com"
    ), sent_query
    out = json.loads(result)
    assert [r["url"] for r in out["results"]] == ["https://docs.astrbot.app/x"]
    assert out["results"][0]["index"] == "abcd.1"

    # ---- 提取：白名单内放行，越界拦截 ----
    ok = await tool_manager.fetch_tool.call("MOCK_CTX", url="https://github.com/a")
    assert ok.startswith("URL: https://github.com/a")
    blocked = await tool_manager.fetch_tool.call("MOCK_CTX", url="https://evil.com/a")
    assert blocked.startswith("Error:") and "evil.com" in blocked

    # ---- 关闭插件：不再拦截 ----
    star.config["enable"] = False
    result = await tool_manager.search_tool.call("MOCK_CTX", query="hello")
    assert tool_manager.search_tool.queries[1]["query"] == "hello"
    assert len(json.loads(result)["results"]) == 2

    # ---- terminate：还原原始 call ----
    await star.terminate()
    result = await tool_manager.search_tool.call("MOCK_CTX", query="hello")
    assert tool_manager.search_tool.queries[2]["query"] == "hello"
    assert len(json.loads(result)["results"]) == 2

    print("OK: plugin dry-run verified (init / wrap / restrict / disable / restore).")


asyncio.run(main())
