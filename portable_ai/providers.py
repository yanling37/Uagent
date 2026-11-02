"""HTTP-only provider adapters, with no third-party runtime dependencies."""
from __future__ import annotations
import json
import ssl
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError('拒绝 API 重定向，避免密钥发送至其他主机')


class HttpClient:
    def __init__(self, proxy: str | None = None):
        self.proxy = proxy

    def post(self, url: str, api_key: str, body: dict) -> dict:
        if not api_key:
            raise ProviderError('请先输入 API Key（不会保存到U盘）')
        data=json.dumps(body, ensure_ascii=False).encode('utf-8')
        request=urllib.request.Request(url, data=data, method='POST', headers={
            'Authorization': 'Bearer '+api_key,
            'Content-Type': 'application/json',
        })
        handlers=[NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({'http':self.proxy,'https':self.proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler()) # Respect host proxy env / system defaults.
        opener=urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=80) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ProviderError('API 返回超过安全大小限制')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise ProviderError(f'HTTP {error.code}: API 请求失败（响应内容未写入日志）') from None
        except (OSError, ValueError) as error:
            raise ProviderError(f'网络或返回数据错误: {error}') from error


def deepseek_completion(http: HttpClient, key: str, model: str, messages: list, tools: list):
    response = http.post('https://api.deepseek.com/chat/completions', key, {
        'model':model, 'messages':messages, 'tools':tools,
        'tool_choice':'auto', 'stream':False, 'max_tokens':4096,
        'thinking':{'type':'disabled'},
    })
    try:
        message = response['choices'][0]['message']
        if not isinstance(message, dict):
            raise ValueError()
        return message
    except (KeyError, IndexError, TypeError, ValueError):
        raise ProviderError('DeepSeek 返回结构无效') from None


def openai_responses(http: HttpClient, key: str, model: str, items: list, tools: list, instructions: str):
    result = http.post('https://api.openai.com/v1/responses', key, {
        'model':model, 'instructions':instructions,
        'input':items, 'tools':tools, 'store':False, 'max_output_tokens':4096,
    })
    if not isinstance(result, dict) or not isinstance(result.get('output'), list):
        raise ProviderError('OpenAI 返回结构无效')
    return result


def extract_openai_text(output: list) -> str:
    fragments=[]
    for item in output:
        if item.get('type')=='message':
            for part in item.get('content', []):
                if part.get('type')=='output_text':
                    fragments.append(part.get('text',''))
    return '\n'.join(fragments)
