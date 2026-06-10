#!/usr/bin/env python3
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ROOT_DIR = BASE_DIR.parent
SKILL_PATH = ROOT_DIR / "SKILL.md"
MARKET_PATH = ROOT_DIR / "行情记录.md"
PRODUCT_PATH = ROOT_DIR / "产品库.md"
ENV_PATH = BASE_DIR / ".env"

FUND_INFO = {
    "017560": "科创芯片",
    "011145": "汇宏",
    "025759": "新兴动力",
    "020982": "机器人",
    "160424": "创业板50",
    "014542": "新能源主题",
    "025733": "航天航空",
    "020867": "港股央企红利",
    "007168": "安和债券",
    "016071": "智联混合",
    "000217": "黄金",
    "017825": "新材料",
    "014978": "纳斯达克100",
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(ENV_PATH)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8766"))
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "claude_cli")
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
MODEL_API_BASE = os.getenv("MODEL_API_BASE", "").rstrip("/")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_API_TIMEOUT = int(os.getenv("MODEL_API_TIMEOUT", "90"))
ANTHROPIC_VERSION = os.getenv("ANTHROPIC_VERSION", "2023-06-01")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "65536"))
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "1.0"))
OPENAI_THINKING_ENABLED = os.getenv("OPENAI_THINKING_ENABLED", "0") in ("1", "true", "TRUE", "yes", "YES")
NAV_REFRESH_ENABLED = os.getenv("NAV_REFRESH_ENABLED", "1") in ("1", "true", "TRUE", "yes", "YES")
FUND_NAV_API_BASE = os.getenv("FUND_NAV_API_BASE", "https://fundgz.1234567.com.cn/js")
FUND_HISTORY_API_BASE = os.getenv("FUND_HISTORY_API_BASE", "http://fundf10.eastmoney.com/F10DataApi.aspx")
import ssl as _ssl
_NAV_SSL_CONTEXT = _ssl._create_unverified_context()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def fetch_previous_nav(code: str) -> dict | None:
    url = f"{FUND_NAV_API_BASE}/{code}.js"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fund.eastmoney.com/",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=15, context=_NAV_SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
    except Exception as exc:
        print(f"[nav-refresh] {code} 拉取失败: {exc}")
        return None

    match = re.search(r"jsonpgz\((\{.*\})\);?$", raw)
    if not match:
        print(f"[nav-refresh] {code} 返回格式异常")
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        print(f"[nav-refresh] {code} 解析失败: {exc}")
        return None

    nav = str(data.get("dwjz", "")).strip()
    nav_date = str(data.get("jzrq", "")).strip()
    if not nav or not nav_date:
        print(f"[nav-refresh] {code} 缺少净值字段")
        return None

    return {
        "code": code,
        "name": str(data.get("name", "")).strip(),
        "nav": nav,
        "date": nav_date,
    }


def fetch_previous_change(code: str) -> dict | None:
    url = f"{FUND_HISTORY_API_BASE}?type=lsjz&code={code}&page=1&per=1&sdate=&edate="
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://fund.eastmoney.com/",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=15, context=_NAV_SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[nav-refresh] {code} 历史涨跌幅拉取失败: {exc}")
        return None

    row_match = re.search(r"<tbody>\s*<tr>(.*?)</tr>\s*</tbody>", raw, re.S)
    if not row_match:
        print(f"[nav-refresh] {code} 历史涨跌幅返回格式异常")
        return None

    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), re.S)
    cleaned = [re.sub(r"<.*?>", "", cell).replace("&nbsp;", "").strip() for cell in cells]
    if len(cleaned) < 4:
        print(f"[nav-refresh] {code} 历史涨跌幅字段不足")
        return None

    trade_date = cleaned[0]
    change_pct = cleaned[3] or "0.00%"
    return {
        "code": code,
        "date": trade_date,
        "change_pct": change_pct,
    }


def fetch_all_previous_changes() -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    failed: list[str] = []
    for code, alias in FUND_INFO.items():
        item = fetch_previous_change(code)
        if not item:
            failed.append(code)
            continue
        item["alias"] = alias
        items.append(item)
    return items, failed


def previous_trade_day_label(now: datetime, trade_date: str) -> str:
    try:
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        return "前一交易日"

    delta_days = (now.date() - trade_dt.date()).days
    if delta_days == 1:
        return "昨日"
    if now.weekday() == 0 and delta_days == 3 and trade_dt.weekday() == 4:
        return "上周五"
    if delta_days > 1:
        return "前一交易日"
    return "昨日"


def build_market_nav_block(items: list[dict]) -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    nav_date = items[0]["date"] if items else ""
    day_label = previous_trade_day_label(now, nav_date) if nav_date else "前一交易日"
    lines = [
        f"## {today}",
        "",
        f"### {day_label}收盘涨跌幅（自动刷新 {current_time}）",
    ]
    for item in items:
        lines.append(f"- {item['code']} {item['alias']}: {item['change_pct']}（{item['date']}）")
    if nav_date:
        lines.extend(["", f"> 数据来源：天天基金，涨跌幅对应净值日期为 {nav_date}"])
    return "\n".join(lines)


def upsert_market_nav_block(markdown: str, block: str) -> str:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    title = "# 行情记录"
    normalized = markdown.strip()
    if title in normalized:
        _, body = normalized.split(title, 1)
        body = body.lstrip()
    else:
        body = normalized

    pattern = re.compile(
        rf"^## {re.escape(today)}\n(?:.*\n)*?(?=^---\n|^## \d{{4}}-\d{{2}}-\d{{2}}|\Z)",
        re.M,
    )
    if pattern.search(body):
        updated_body = pattern.sub(block.strip() + "\n\n", body, count=1)
    elif body:
        updated_body = block.strip() + "\n\n---\n\n" + body
    else:
        updated_body = block.strip()

    return f"{title}\n\n{updated_body.rstrip()}\n"


def refresh_market_navs() -> dict:
    if not NAV_REFRESH_ENABLED or not MARKET_PATH.exists():
        return {"updated": 0, "failed": [], "message": "行情刷新未启用"}

    items, failed = fetch_all_previous_changes()
    if not items:
        raise RuntimeError("没有抓到任何涨跌幅数据")

    original = MARKET_PATH.read_text(encoding="utf-8")
    block = build_market_nav_block(items)
    updated = upsert_market_nav_block(original, block)
    MARKET_PATH.write_text(updated, encoding="utf-8")

    message = f"已刷新 {len(items)} 只产品昨日涨跌幅，并写入行情记录"
    if failed:
        message += f"，失败 {len(failed)} 只：{', '.join(failed)}"
    print(f"[nav-refresh] {message}")
    return {"updated": len(items), "failed": failed, "message": message}


def current_time_info() -> tuple[str, str]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return now.strftime("%Y-%m-%d %H:%M"), ("上午盘" if now.hour < 12 else "下午盘")


def build_context() -> dict:
    current_time, period = current_time_info()
    return {
        "current_time": current_time,
        "period": period,
        "host_mode": "public" if HOST == "0.0.0.0" else "local",
        "model_provider": MODEL_PROVIDER,
        "model_name": MODEL_NAME,
        "market_notes": read_text(MARKET_PATH),
        "product_notes": read_text(PRODUCT_PATH),
        "skill_notes": read_text(SKILL_PATH),
    }


def build_prompt(data: dict) -> str:
    fund_code = str(data.get("fund_code", "")).strip()
    fund_name = FUND_INFO.get(fund_code, str(data.get("fund_name", "")).strip())
    theme = str(data.get("theme", "")).strip()
    post_type = str(data.get("post_type", "")).strip()
    hotspot = str(data.get("hotspot", "")).strip()
    extra = str(data.get("extra", "")).strip()
    count = max(1, min(20, int(data.get("count", 5) or 5)))
    current_time = str(data.get("current_time", "")).strip()
    period = str(data.get("period", "")).strip()

    if not fund_code or not fund_name:
        raise ValueError("请选择基金")
    if not theme or not post_type:
        raise ValueError("请选择主题和帖子类型")
    if not hotspot:
        raise ValueError("请填写热点描述")

    context = build_context()
    output_rule_map = {
        "千粉帖": f"生成 {count} 篇千粉帖，单篇 200-300 字，必须有标题，每篇用 --- 分隔。",
        "短帖": f"生成 {count} 条短帖，单条 80-120 字，不要标题，每条开头必须写“{fund_name}（{fund_code}）：”，每条用 --- 分隔。",
        "对比帖": f"生成 {count} 篇对比帖，单篇 200-300 字，必须有标题，要带净值和持仓差异感知，每篇用 --- 分隔。",
    }
    output_rule = output_rule_map.get(post_type, f"生成 {count} 条内容，每条用 --- 分隔。")

    return f"""你现在不是分析师，而是天天基金讨论区里的真实持仓用户写手。

【本次任务】
- 当前时间：{current_time or context["current_time"]}
- 盘面时段：{period or context["period"]}
- 基金：{fund_name}（{fund_code}）
- 主题：{theme}
- 帖子类型：{post_type}
- 数量：{count}
- 当日热点描述：{hotspot}
- 额外要求：{extra or "无"}

【技能规则】
{context["skill_notes"]}

【行情记录】
{context["market_notes"]}

【产品库】
{context["product_notes"]}

【硬性执行要求】
1. 先吸收当日行情，再写情绪，不需要刻意体现上午盘和下午盘的变化。
2. 每条先情绪，后逻辑，逻辑用大白话，不要研报腔。
3. 一定要结合 {fund_name}（{fund_code}） 的持仓特点，不能泛泛写 AI 或芯片。
4. 不写对话，不给明确买卖建议，不写场内盘中交易动作。
5. 每条情绪、句式、人设都要明显区分。
6. {output_rule}
7. 不要输出 Markdown 格式符号，不要出现 **、##、###、- 列表、> 引用、``` 代码块。
8. 除帖子正文外，不要输出任何解释说明。
"""


def call_model(prompt: str) -> dict:
    if MODEL_PROVIDER == "claude_cli":
        return call_claude_cli(prompt)
    if MODEL_PROVIDER == "openai_compatible":
        return call_openai_compatible_api(prompt)
    if MODEL_PROVIDER == "anthropic_compatible":
        return call_anthropic_compatible_api(prompt)
    return {"error": f"不支持的 MODEL_PROVIDER: {MODEL_PROVIDER}"}


def call_claude_cli(prompt: str) -> dict:
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", prompt],
            capture_output=True,
            text=True,
            timeout=90,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"error": "未找到 claude 命令，请确认 Claude Code CLI 已安装"}
    except subprocess.TimeoutExpired:
        return {"error": "生成超时，请重试"}
    except Exception as exc:
        return {"error": f"调用失败: {exc}"}

    if result.returncode != 0:
        return {"error": f"Claude CLI 错误: {result.stderr.strip()}"}
    return {"content": result.stdout.strip()}


def require_api_config() -> None:
    if not MODEL_API_BASE:
        raise ValueError("缺少 MODEL_API_BASE")
    if not MODEL_API_KEY:
        raise ValueError("缺少 MODEL_API_KEY")
    if not MODEL_NAME:
        raise ValueError("缺少 MODEL_NAME")


def post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=MODEL_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络错误: {exc.reason}") from exc


def call_openai_compatible_api(prompt: str) -> dict:
    try:
        require_api_config()
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": MODEL_TEMPERATURE,
            "max_tokens": MODEL_MAX_TOKENS,
        }
        if OPENAI_THINKING_ENABLED:
            payload["thinking"] = {"type": "enabled"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MODEL_API_KEY}",
        }
        data = post_json(f"{MODEL_API_BASE}/chat/completions", payload, headers)
        content = data["choices"][0]["message"]["content"].strip()
        return {"content": content}
    except Exception as exc:
        return {"error": f"API 调用失败: {exc}"}


def call_anthropic_compatible_api(prompt: str) -> dict:
    try:
        require_api_config()
        payload = {
            "model": MODEL_NAME,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MODEL_API_KEY}",
            "x-api-key": MODEL_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        data = post_json(f"{MODEL_API_BASE}/messages", payload, headers)
        blocks = data.get("content", [])
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()
        if not text:
            raise RuntimeError(f"响应里没有可用文本: {data}")
        return {"content": text}
    except Exception as exc:
        return {"error": f"API 调用失败: {exc}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _is_authorized(self) -> bool:
        if not ACCESS_TOKEN:
            return True
        header_token = self.headers.get("X-Access-Token", "").strip()
        return header_token == ACCESS_TOKEN

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _serve_static(self, rel_path: str) -> None:
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")
            return

        content_type = "text/plain; charset=utf-8"
        if target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), content_type)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_bytes(200, (TEMPLATE_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return

        if self.path.startswith("/api/") or self.path == "/healthz":
            if not self._is_authorized():
                self._send_json(401, {"error": "未授权访问"})
                return

        if self.path == "/healthz":
            self._send_json(
                200,
                {
                    "ok": True,
                    "host": HOST,
                    "port": PORT,
                    "model_provider": MODEL_PROVIDER,
                    "model_name": MODEL_NAME,
                    "claude_bin": CLAUDE_BIN,
                },
            )
            return

        if self.path.startswith("/static/"):
            self._serve_static(unquote(self.path.removeprefix("/static/")))
            return

        if self.path == "/api/context":
            try:
                self._send_json(200, build_context())
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if self.path == "/api/nav-refresh":
            try:
                result = refresh_market_navs()
                self._send_json(200, {"ok": True, **result})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path != "/api/generate":
            self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")
            return

        if not self._is_authorized():
            self._send_json(401, {"error": "未授权访问"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            prompt = build_prompt(data)
            self._send_json(200, call_model(prompt))
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def main():
    try:
        refresh_market_navs()
    except Exception as exc:
        print(f"[nav-refresh] 启动时刷新失败: {exc}")
    print("=" * 56)
    print("  热点转基金讨论区 - 全新前端程序")
    print(f"  地址: http://{HOST}:{PORT}")
    print("  说明: 独立 fresh_app，不依赖旧 server.py")
    print(f"  Provider: {MODEL_PROVIDER}")
    if MODEL_PROVIDER == "claude_cli":
        print(f"  Claude: {CLAUDE_BIN}")
    else:
        print(f"  API Base: {MODEL_API_BASE or '[未设置]'}")
        print(f"  Model: {MODEL_NAME or '[未设置]'}")
    print("=" * 56)
    HTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
