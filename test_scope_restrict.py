"""scope_restrict.py 的独立单元测试。

无需安装 AstrBot 或 pytest，直接运行：

    python test_scope_restrict.py

全部通过时输出 OK，任何断言失败都会抛出异常。
"""

import json

from scope_restrict import (
    check_fetch_url,
    filter_search_results,
    host_allowed,
    normalize_sites,
    rewrite_query,
)


def _payload(results):
    return json.dumps({"results": results}, ensure_ascii=False)


def test_normalize_sites():
    assert normalize_sites(["https://Docs.Astrbot.app/x", "GitHub.com"]) == [
        "docs.astrbot.app",
        "github.com",
    ]
    assert normalize_sites(["example.com", "https://example.com/a"]) == [
        "example.com"
    ]
    assert normalize_sites([".example.com", "EXAMPLE.com"]) == ["example.com"]
    assert normalize_sites(["", None, 123, "https://"]) == []
    assert normalize_sites(None) == []


def test_host_allowed():
    assert host_allowed("github.com", ["github.com"])
    assert host_allowed("docs.github.com", ["github.com"])
    assert host_allowed("github.com.evil.com", ["github.com"]) is False
    assert host_allowed("github.com", ["example.com"]) is False
    # 关闭子域名匹配时仅精确匹配
    assert host_allowed("docs.github.com", ["github.com"], allow_subdomains=False) is False


def test_rewrite_query_whitelist():
    # 正常注入，并移除模型自带的 site: / OR 子句
    q = rewrite_query(
        'DeepSeek "API reference" site:github.com OR site:hf.co',
        "whitelist",
        ["docs.astrbot.app", "github.com"],
    )
    assert q == 'DeepSeek "API reference" site:docs.astrbot.app OR site:github.com'

    # 无站点配置时只清理不注入
    assert rewrite_query("DeepSeek site:github.com", "whitelist", []) == "DeepSeek"

    # 纯 site: 查询时退化为纯限制子句
    assert (
        rewrite_query("site:github.com", "whitelist", ["example.com"])
        == "site:example.com"
    )


def test_rewrite_query_blacklist():
    q = rewrite_query("DeepSeek news", "blacklist", ["spam.com", "ads.net"])
    assert q == "DeepSeek news -site:spam.com -site:ads.net"


def test_rewrite_query_length_budget():
    # 原查询 + 后缀超过 128 字符时裁剪原查询，后缀完整保留
    query = "x" * 120
    q = rewrite_query(query, "whitelist", ["example.com"])
    assert q.endswith("site:example.com")
    assert len(q) <= 128

    # 后缀本身太长（放不下）时原样返回（由结果过滤兜底）
    long_sites = ["a" * 20 + ".com"] * 10
    q = rewrite_query("hello", "whitelist", long_sites)
    assert q == "hello"


def test_filter_search_results_whitelist():
    payload = _payload(
        [
            {"title": "a", "url": "https://docs.astrbot.app/x", "index": "abcd.1"},
            {"title": "b", "url": "https://evil.com/x", "index": "abcd.2"},
            {"title": "c", "url": "https://github.com/x", "index": "abcd.3"},
        ]
    )
    out = json.loads(filter_search_results(payload, "whitelist", ["astrbot.app", "github.com"]))
    urls = [r["url"] for r in out["results"]]
    assert urls == ["https://docs.astrbot.app/x", "https://github.com/x"]
    # 索引重新连续编号，前缀保持
    assert [r["index"] for r in out["results"]] == ["abcd.1", "abcd.2"]

    # 列表为空时不过滤
    assert filter_search_results(payload, "whitelist", []) == payload

    # 非 JSON（错误信息）原样返回
    assert filter_search_results("Error: something", "whitelist", ["a.com"]) == (
        "Error: something"
    )


def test_filter_search_results_all_filtered():
    payload = _payload(
        [{"title": "b", "url": "https://evil.com/x", "index": "abcd.1"}]
    )
    out = filter_search_results(payload, "whitelist", ["good.com"])
    assert out.startswith("Error:")
    assert "good.com" in out


def test_filter_search_results_blacklist():
    payload = _payload(
        [
            {"title": "a", "url": "https://good.com/x", "index": "abcd.1"},
            {"title": "b", "url": "https://spam.com/x", "index": "abcd.2"},
        ]
    )
    out = json.loads(filter_search_results(payload, "blacklist", ["spam.com"]))
    assert [r["url"] for r in out["results"]] == ["https://good.com/x"]
    assert out["results"][0]["index"] == "abcd.1"


def test_check_fetch_url():
    assert check_fetch_url("https://docs.astrbot.app/x", "whitelist", ["astrbot.app"]) is None
    blocked = check_fetch_url("https://evil.com/x", "whitelist", ["astrbot.app"])
    assert blocked and blocked.startswith("Error:") and "evil.com" in blocked

    # 黑名单：命中即拦截
    blocked = check_fetch_url("https://spam.com/x", "blacklist", ["spam.com"])
    assert blocked and "spam.com" in blocked
    assert check_fetch_url("https://good.com/x", "blacklist", ["spam.com"]) is None

    # 非法 URL 交还给内置工具报错；列表为空不拦截
    assert check_fetch_url("not a url", "whitelist", ["a.com"]) is None
    assert check_fetch_url("https://evil.com/x", "whitelist", []) is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nOK: {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
