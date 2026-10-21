"""DeepSeek 搜索范围限制的纯逻辑模块。

包含域名匹配、查询改写、结果过滤与网页提取校验。本模块不依赖 AstrBot，
可以独立进行单元测试（见 test_scope_restrict.py）。
"""

import json
import re
import uuid
from urllib.parse import urlsplit

# DeepSeek Search API 的 query 参数上限（Unicode 字符数）。
MAX_QUERY_LEN = 128

# 模型可能自行写入的 site: 语法 token，以及连接这些 token 的布尔词。
_SITE_TOKEN_RE = re.compile(r"^-?site:", re.IGNORECASE)
_JOINER_RE = re.compile(r"^(or|and)$", re.IGNORECASE)
# 按空白切分，但保留带引号的短语（如 -"release notes"）为单个 token。
_TOKENS_RE = re.compile(r'"(?:\\.|[^"\\])*"|\S+')


def _host_of(url) -> str | None:
    """从 URL 或域名文本中提取小写主机名，无法解析时返回 None。

    Args:
        url: 待解析的 URL 或域名字符串。

    Returns:
        小写主机名；输入不是字符串或解析失败时返回 None。
    """
    if not isinstance(url, str):
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def normalize_sites(entries) -> list[str]:
    """把用户配置的域名条目规范化为主机名列表。

    支持直接粘贴完整网址（自动提取域名），去重并忽略无效条目。

    Args:
        entries: 域名/网址字符串列表。

    Returns:
        去重后的小写主机名列表。
    """
    if not isinstance(entries, (list, tuple)):
        return []
    hosts: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        host = _host_of(entry) or entry
        host = host.strip().lower().lstrip(".")
        # 无主机名的网址（如 "https://"）与含空白/斜杠的无效文本直接丢弃。
        if not host or "/" in host or any(ch.isspace() for ch in host):
            continue
        if host not in hosts:
            hosts.append(host)
    return hosts


def host_allowed(host: str, sites: list[str], allow_subdomains: bool = True) -> bool:
    """判断主机名是否命中域名列表。

    Args:
        host: 待判断的主机名。
        sites: 已规范化的域名列表。
        allow_subdomains: 为 True 时，条目 example.com 同时匹配 *.example.com。

    Returns:
        命中返回 True。
    """
    host = (host or "").lower()
    for site in sites:
        site = site.lower()
        if host == site or (allow_subdomains and host.endswith("." + site)):
            return True
    return False


def rewrite_query(query: str, mode: str, sites: list[str]) -> str:
    """改写搜索 query：移除模型自带的 site: 子句并注入本插件的限制子句。

    DeepSeek Search API 原生支持 site: / -site: 操作符：
    - 白名单：追加 `site:a.com OR site:b.com`；
    - 黑名单：追加 `-site:a.com -site:b.com`。

    Args:
        query: 模型生成的原始查询。
        mode: "whitelist" 或 "blacklist"。
        sites: 已规范化的域名列表。

    Returns:
        改写后的 query。query 超长导致无法容纳限制子句时，裁剪原查询后注入；
        空间不足时原样返回（由结果过滤兜底）。
    """
    tokens = [
        t
        for t in _TOKENS_RE.findall(query or "")
        if not (_SITE_TOKEN_RE.match(t) or _JOINER_RE.match(t))
    ]
    cleaned = " ".join(tokens).strip()

    if not sites:
        return cleaned or (query or "")

    if mode == "blacklist":
        clause = " ".join(f"-site:{site}" for site in sites)
    else:
        clause = " OR ".join(f"site:{site}" for site in sites)

    if not cleaned:
        return clause.strip()

    suffix = f" {clause}"
    if len(cleaned) + len(suffix) <= MAX_QUERY_LEN:
        return cleaned + suffix
    budget = MAX_QUERY_LEN - len(suffix)
    if budget >= 8:
        return cleaned[:budget].rstrip() + suffix
    return cleaned


def _index_prefix(kept_items: list[dict]) -> str:
    """从保留下来的结果中提取索引前缀（如 abcd.3 -> abcd）。

    Args:
        kept_items: 过滤后保留的结果条目。

    Returns:
        四字符索引前缀；提取失败时随机生成。
    """
    for item in kept_items:
        index = item.get("index") if isinstance(item, dict) else None
        if isinstance(index, str) and "." in index:
            prefix = index.rsplit(".", 1)[0]
            if prefix:
                return prefix
    return uuid.uuid4().hex[:4]


def _empty_result_message(mode: str, sites: list[str]) -> str:
    """生成"过滤后无结果"的错误信息，供模型向用户解释。

    Args:
        mode: "whitelist" 或 "blacklist"。
        sites: 已规范化的域名列表。

    Returns:
        以 "Error:" 开头的说明文本（与内置工具的错误风格一致）。
    """
    joined = ", ".join(sites)
    if mode == "blacklist":
        return (
            "Error: all DeepSeek web search results came from blocked websites "
            f"({joined}) and were removed. No usable results remain within the "
            "configured search scope."
        )
    return (
        "Error: DeepSeek web search returned no results from the allowed websites "
        f"({joined}). No answer was found within the configured search scope."
    )


def filter_search_results(
    payload: str,
    mode: str,
    sites: list[str],
    allow_subdomains: bool = True,
) -> str:
    """对内置搜索工具返回的结果 JSON 做域名过滤并重新编号索引。

    与查询注入构成双保险：即使 site: 操作符未被搜索引擎完整执行，
    结果也会在返回给模型前被过滤。

    Args:
        payload: 内置工具返回的 JSON 字符串（{"results": [...]}）。
        mode: "whitelist" 或 "blacklist"。
        sites: 已规范化的域名列表。
        allow_subdomains: 条目是否同时匹配其子域名。

    Returns:
        过滤并重新编号后的 JSON 字符串；无需过滤或无法解析时原样返回。
    """
    if not sites:
        return payload
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return payload
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return payload

    kept: list[dict] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        host = _host_of(item.get("url"))
        if host is None:
            continue
        allowed = host_allowed(host, sites, allow_subdomains)
        if mode == "blacklist":
            allowed = not allowed
        if allowed:
            kept.append(item)

    if not kept:
        return _empty_result_message(mode, sites)

    prefix = _index_prefix(kept)
    for idx, item in enumerate(kept, 1):
        item["index"] = f"{prefix}.{idx}"
    data["results"] = kept
    return json.dumps(data, ensure_ascii=False)


def check_fetch_url(
    url: str,
    mode: str,
    sites: list[str],
    allow_subdomains: bool = True,
) -> str | None:
    """校验网页正文提取的 URL 是否在允许范围内。

    Args:
        url: 待提取的 URL。
        mode: "whitelist" 或 "blacklist"。
        sites: 已规范化的域名列表。
        allow_subdomains: 条目是否同时匹配其子域名。

    Returns:
        被拦截时返回错误信息（模型会停止重试并转告用户）；
        放行或无需校验时返回 None。
    """
    if not sites:
        return None
    host = _host_of(url)
    if host is None:
        # 非法 URL 交给内置工具自身的参数校验报错。
        return None
    allowed = host_allowed(host, sites, allow_subdomains)
    if mode == "blacklist":
        allowed = not allowed
    if allowed:
        return None

    joined = ", ".join(sites)
    if mode == "blacklist":
        return (
            f"Error: fetching {host} is blocked by the DeepSeek search scope "
            f"restriction (blocked websites: {joined}). Do not retry this URL."
        )
    return (
        f"Error: fetching {host} is not allowed by the DeepSeek search scope "
        f"restriction (allowed websites: {joined}). Do not retry this URL."
    )
