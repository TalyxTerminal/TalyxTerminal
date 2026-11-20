"""Official connection identities and independent search endpoints."""

OFFICIAL_SITES = {
    ("alibaba", "domestic"): "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ("alibaba", "international"): "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ("deepseek", None): "https://api.deepseek.com",
    ("moonshotai", "domestic"): "https://api.moonshot.cn/v1",
    ("moonshotai", "international"): "https://api.moonshot.ai/v1",
    ("zhipuai", "domestic"): "https://open.bigmodel.cn/api/paas/v4",
    ("zhipuai", "international"): "https://api.z.ai/api/paas/v4",
}

SEARCH_ENDPOINTS = {
    ("deepseek", None): ("https://api.deepseek.com/v1/tools/search", "deepseek_search_request"),
    ("moonshotai", "domestic"): ("https://api.moonshot.cn/v1/tools/search_pro", "kimi_search_pro_request"),
    ("moonshotai", "international"): ("https://api.moonshot.ai/v1/tools/search_pro", "kimi_search_pro_request"),
    ("zhipuai", "domestic"): ("https://open.bigmodel.cn/api/paas/v4/web_search", "zhipu_search_pro_request"),
    ("zhipuai", "international"): ("https://api.z.ai/api/paas/v4/web_search", "zai_search_prime_request"),
}
