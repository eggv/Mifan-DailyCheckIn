#!/usr/bin/env python3
"""
🍚 米饭APP日常脚本 - 优化版
==============================================================
支持模式:
  1) 青龙面板模式 —— 通过 MIFAN_USER / MIFAN_PASSWORD 环境变量传参
  2) 本地终端交互模式 —— 手动输入账号并选择任务
==============================================================
"""

import hashlib
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ======================================================================
# 配置常量
# ======================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

# 是否自动模式（通过环境变量 MIFAN_USER + MIFAN_PASSWORD 判断）
# 在青龙面板、crontab、或手动设置环境变量时均为自动模式
IS_AUTO_MODE = bool(os.environ.get("MIFAN_USER") and os.environ.get("MIFAN_PASSWORD"))

# 请求重试
RETRY_TIMES = 3
RETRY_DELAY_S = 5
REQUEST_TIMEOUT = 15

# 持久化文件名（与旧版兼容）
STATE_FILE = "request_state.json"
PROGRESS_FILE = "comment_progress.json"

# 默认评论（无 AI API Key 时使用）
FALLBACK_COMMENTS = [
    "不错！", "学习到了！", "给力👍", "有意思！", "太棒了！",
    "好厉害呀！", "赞一个！", "收藏了～", "这个好！", "谢谢分享！",
    "确实如此！", "学到了！", "哈哈不错", "支持一下！",
]

# URL 常量
URLS = {
    "LOGIN":       "https://mifan.61.com/api/v1/login",
    "LOGOUT":      "https://mifan.61.com/api/v1/logout",
    "PROFILE":     "https://mifan.61.com/api/v1/profile",
    "DAILY_SIGN":  "https://mifan.61.com/api/v1/event/dailysign/",
    "SIGN_STATUS": "https://mifan.61.com/api/v1/event/dailysign/status/",
    "LIKE":        "https://mifan.61.com/api/v1/article/likes/{id}/",
    "FEED":        "https://mifan.61.com/api/v1/feed",
    "COMMENT":     "https://mifan.61.com/api/v1/article/comment",
    "COMPLEMENT_RECENT":  "https://mifan.61.com/api/v1/event/dailysign/recent",
    "COMPLEMENT_DO":      "https://mifan.61.com/api/v1/event/dailysign/complement",
}


# ======================================================================
# AI 提供商配置
# ======================================================================

AI_PROVIDERS = {
    "moonshot": {
        "env_key": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k",
        "env_model": "MOONSHOT_MODEL",
        "label": "Moonshot",
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-flash",
        "env_model": "DEEPSEEK_MODEL",
        "label": "DeepSeek",
    },
}

# 当前选中的 AI 提供商（通过环境变量 AI_PROVIDER 切换，默认 deepseek）
AI_PROVIDER = os.environ.get("AI_PROVIDER", "deepseek").strip().lower()
if AI_PROVIDER not in AI_PROVIDERS:
    AI_PROVIDER = "deepseek"
AI_CFG = AI_PROVIDERS[AI_PROVIDER]


# ======================================================================
# Logger 工具类（来自 mifan.js 的设计）
# ======================================================================

class Logger:
    """统一日志输出：同时写入文件 + 控制台"""
    _logger: logging.Logger = None

    @classmethod
    def init(cls, log_file: str = "mifan_optimized.log"):
        if cls._logger is not None:
            return
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        cls._logger = logging.getLogger("mifan")

    @classmethod
    def info(cls, msg: str):
        cls._ensure()
        cls._logger.info(msg)

    @classmethod
    def success(cls, msg: str):
        cls._ensure()
        cls._logger.info(f"✅ {msg}")

    @classmethod
    def warning(cls, msg: str):
        cls._ensure()
        cls._logger.warning(f"⚠️  {msg}")

    @classmethod
    def error(cls, msg: str):
        cls._ensure()
        cls._logger.error(f"❌ {msg}")

    @classmethod
    def _ensure(cls):
        if cls._logger is None:
            cls.init()


# ======================================================================
# 网络工具类（来自 mifan.js 的 NetworkUtils）
# ======================================================================

class NetworkUtils:
    """统一 HTTP 请求封装，自带重试机制"""

    @staticmethod
    def request(
        method: str,
        url: str,
        headers: dict = None,
        data=None,
        params: dict = None,
        timeout: int = REQUEST_TIMEOUT,
        retries: int = RETRY_TIMES,
    ) -> Optional[requests.Response]:
        """发送 HTTP 请求，失败自动重试"""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    headers=headers or {},
                    data=data,
                    params=params,
                    timeout=timeout,
                    verify=False,
                )
                return resp
            except Exception as e:
                last_error = e
                if attempt < retries:
                    Logger.warning(
                        f"请求失败 ({attempt}/{retries})，"
                        f"{RETRY_DELAY_S}秒后重试: {e}"
                    )
                    time.sleep(RETRY_DELAY_S)
        Logger.error(f"请求重试耗尽: {last_error}")
        return None

    @staticmethod
    def parse_response(resp: Optional[requests.Response]) -> Optional[dict]:
        """解析响应，自动处理 gzip 与 JSON"""
        if resp is None:
            return None
        try:
            raw = resp.content
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            Logger.warning(f"响应解析失败: {e} (status={resp.status_code})")
            return None


# ======================================================================
# 通知工具（青龙面板兼容）
# ======================================================================

class Notifier:
    """青龙面板通知——调用青龙内置 notify 模块实现真实推送"""

    _notify_available = None

    @classmethod
    def send(cls, title: str, content: str):
        """发送通知：优先调用青龙 notify 模块，失败则写日志"""
        if cls._try_ql_notify(title, content):
            return
        # 本地模式：仅输出到日志
        Logger.info(f"\n{'='*48}")
        Logger.info(f"📬 {title}")
        for line in content.strip().split("\n"):
            Logger.info(f"   {line}")
        Logger.info(f"{'='*48}\n")

    @classmethod
    def _try_ql_notify(cls, title: str, content: str) -> bool:
        """尝试调用青龙面板内置 notify 模块"""
        if cls._notify_available is False:
            return False
        try:
            from notify import send as ql_send
            cls._notify_available = True
            ql_send(title, content)
            return True
        except ImportError:
            cls._notify_available = False
            Logger.info("青龙 notify 模块不可用（本地模式），通知内容输出到日志")
            return False
        except Exception as e:
            cls._notify_available = False
            Logger.warning(f"青龙通知发送失败: {e}")
            return False


# ======================================================================
# 认证模块（来自 mifan.js：login → token → logout）
# ======================================================================

class MiFanAuth:
    """账号认证管理（登录、登出、Token 维护）"""

    def __init__(self, username: str, password: str, gid: int = 689):
        self.username = username
        self.password = password
        self.gid = gid
        self.token: Optional[str] = None
        self._headers: Optional[dict] = None

    # ---- 公共请求头 ----

    def _base_headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mifan.61.com/dist/",
            "Origin": "https://mifan.61.com",
        }

    def _auth_headers(self, token: str) -> dict:
        h = self._base_headers()
        h["Authorization"] = token
        h["Content-Type"] = "application/json;charset=utf-8"
        return h

    def _form_headers(self, token: str) -> dict:
        h = self._base_headers()
        h["Authorization"] = token
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return h

    # ---- 登录 ----

    def login(self) -> bool:
        """
        使用用户名 + MD5 密码登录
        返回 True 表示登录成功，self.token 为有效 Token
        """
        Logger.info(f"🔑 账号 [{self.username}] 正在登录...")
        hashed_pwd = hashlib.md5(self.password.encode()).hexdigest()
        payload = (
            f"gid={self.gid}&uid={self.username}"
            f"&password={hashed_pwd}&tad=&encrypt=true"
        )
        resp = NetworkUtils.request(
            "POST", URLS["LOGIN"],
            headers=self._form_headers(""),
            data=payload,
        )
        result = NetworkUtils.parse_response(resp)
        if result and result.get("code") == 200:
            self.token = result.get("token")
            if not self.token:
                Logger.error("登录返回成功但无 token")
                return False
            self._headers = self._auth_headers(self.token)
            Logger.success(f"登录成功 (token 前缀: {self.token[:16]}...)")
            return True
        msg = result.get("data", "未知错误") if result else "无响应"
        Logger.error(f"登录失败: {msg}")
        return False

    # ---- 登出 ----

    def logout(self) -> bool:
        """登出当前账号"""
        if not self.token:
            Logger.warning("未登录，无需登出")
            return True
        Logger.info(f"🔒 账号 [{self.username}] 正在登出...")
        resp = NetworkUtils.request(
            "POST", URLS["LOGOUT"],
            headers=self._auth_headers(self.token),
        )
        result = NetworkUtils.parse_response(resp)
        if result and result.get("code") == 200:
            Logger.success("登出成功")
            self.token = None
            self._headers = None
            return True
        Logger.warning("登出失败（可能 token 已过期）")
        self.token = None
        self._headers = None
        return False

    # ---- 获取公共请求头 ----

    def get_headers(self, content_type: str = "json") -> dict:
        if not self.token:
            raise RuntimeError("未登录，无法获取请求头")
        if content_type == "form":
            return self._form_headers(self.token)
        return self._auth_headers(self.token)


# ======================================================================
# 业务功能模块
# ======================================================================

class MiFanClient:
    """
    封装所有 API 操作，依赖 Auth 模块提供的 token 和 headers
    """

    def __init__(self, auth: MiFanAuth):
        self.auth = auth

    # ---- 获取米粒余额 / 用户信息 ----

    def get_gold_balance(self) -> Optional[int]:
        """查询当前米粒余额"""
        resp = NetworkUtils.request(
            "POST", URLS["PROFILE"],
            headers=self.auth.get_headers(),
        )
        result = NetworkUtils.parse_response(resp)
        if result:
            gold = result.get("gold")
            nick = result.get("nickname", "未知")
            Logger.info(f"👤 用户: {nick} | 米粒余额: {gold}")
            return gold
        return None

    # ---- 签到 ----

    def daily_sign(self) -> bool:
        """
        执行每日签到
        返回 True 表示签到成功
        注意：在交互模式下，如果已签到会询问是否重签
        """
        Logger.info("📝 执行每日签到...")

        def _do_sign() -> Optional[dict]:
            resp = NetworkUtils.request(
                "POST", URLS["DAILY_SIGN"],
                headers=self.auth.get_headers(),
            )
            return NetworkUtils.parse_response(resp)

        # 首次请求 → 检查是否已签到
        result = _do_sign()
        if result is None:
            Logger.error("签到请求失败")
            return False

        status = result.get("data") if isinstance(result, dict) else None
        if status == "已签到":
            # 青龙模式：直接跳过
            if IS_AUTO_MODE:
                Logger.info("今日已签到（青龙模式，自动跳过）")
                return True
            # 交互模式：询问是否重签
            print("\n⚠️  今日已签到!")
            choice = input("是否重新执行签到？(y/n): ").strip().lower()
            if choice != "y":
                Logger.info("用户选择跳过重复签到")
                return True
            result = _do_sign()

        if result and result.get("code") == 200:
            Logger.success(f"签到成功: {result.get('data')}")
            return True
        else:
            Logger.warning(f"签到失败: {result}")
            return False

    # ---- 点赞 ----

    def batch_like(self, count: int = 20) -> None:
        """
        自动点赞：从动态流获取最新文章 ID 进行点赞
        记录最高 ID 作为断点，下次只点赞更新的帖子
        """
        Logger.info("👍 开始自动点赞...")

        # 从 feed 拉取最新文章列表
        feed_items = self._fetch_feed_items()
        article_ids = []
        for it in feed_items:
            if it.get("cmd_id") == 7003:
                aid = it.get("data", {}).get("article_id")
                if aid:
                    article_ids.append(aid)

        if not article_ids:
            Logger.warning("动态流中未找到可点赞的文章")
            return

        # 按 ID 降序排列（最新在前）
        article_ids.sort(reverse=True)
        Logger.info(f"动态流最新文章 ID 区间: {article_ids[-1]} ~ {article_ids[0]}")

        # 读取断点，只点赞比断点更新的文章
        checkpoint = self._load_like_state()
        if checkpoint:
            new_ids = [aid for aid in article_ids if aid > checkpoint]
            if not new_ids:
                Logger.info(f"没有比断点 {checkpoint} 更新的文章，跳过点赞")
                return
            Logger.info(f"断点 {checkpoint} → 发现 {len(new_ids)} 篇新文章")
        else:
            new_ids = article_ids[:count]

        # 限制点赞数量
        to_like = new_ids[:count]
        for aid in to_like:
            self._like_one(aid)
            time.sleep(random.uniform(3, 10))

        # 保存新断点（本次见到的最新 ID）
        new_checkpoint = max(article_ids)
        self._save_like_state(new_checkpoint)
        Logger.success(
            f"点赞完成 {len(to_like)} 篇, 最新 ID: {to_like[0]}, "
            f"断点已更新至: {new_checkpoint}"
        )

    def _fetch_feed_items(self) -> list:
        """调用动态流接口获取最新帖子列表"""
        try:
            sess = requests.Session()
            sess.headers.update({
                "Authorization": self.auth.token,
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mifan.61.com/dist/",
                "Origin": "https://mifan.61.com",
            })
            resp = sess.post(
                URLS["FEED"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "type": "latest",
                    "offset": -1,
                    "count": 20,
                    "start_time": 0,
                    "end_time": 0,
                    "tag_id": "undefined",
                    "timestamp": int(time.time()),
                },
                timeout=30,
            )
            parsed = NetworkUtils.parse_response(resp)
            if parsed:
                data = parsed.get("data") or {}
                return data.get("current_page", [])
        except Exception as e:
            Logger.error(f"获取动态流失败: {e}")
        return []

    def _load_like_state(self) -> int:
        try:
            with open(STATE_FILE, "r") as f:
                s = json.load(f)
                cid = s["current_id"]
                if cid > 0:
                    Logger.info(f"📄 恢复点赞断点: {cid}")
                    return cid
        except Exception:
            pass
        Logger.info("📄 未找到点赞断点，本次从动态流最新文章开始")
        return 0

    def _save_like_state(self, current_id: int) -> None:
        with open(STATE_FILE, "w") as f:
            json.dump({"current_id": current_id}, f)
        Logger.info(f"📂 点赞进度已保存至 {STATE_FILE}")

    def _like_one(self, article_id: int) -> bool:
        url = URLS["LIKE"].format(id=article_id)
        resp = NetworkUtils.request(
            "POST", url,
            headers=self.auth.get_headers(),
        )
        if resp and resp.status_code == 200:
            Logger.success(f"点赞 ID {article_id}")
            return True
        else:
            code = resp.status_code if resp else "无响应"
            Logger.warning(f"点赞 ID {article_id} 异常: {code}")
            return False

    # ---- AI 自动评论 ----

    def auto_comment(self, max_comments: int = 10) -> None:
        """
        从动态流中获取未评论的帖子
        有 AI Key 时调用 AI 生成评论，否则使用默认评论
        """
        ai_config = self._get_ai_provider_config()
        use_fallback = ai_config is None
        if use_fallback:
            Logger.info("💬 开始自动评论（使用默认评论）...")
        else:
            Logger.info(f"💬 开始自动评论（{ai_config['label']} AI）...")

        # 首次查询米粒余额
        start_gold = self.get_gold_balance()
        prog = self._load_comment_progress()
        commented = set(prog.get("commented", []))
        offset = prog.get("offset", -1)
        success_count = 0

        # 拉取动态流
        sess = requests.Session()
        sess.headers.update({
            "Authorization": self.auth.token,
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mifan.61.com/dist/",
            "Origin": "https://mifan.61.com",
        })

        resp = sess.post(
            URLS["FEED"],
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "type": "latest",
                "offset": offset,
                "count": 15,
                "start_time": 0,
                "end_time": 0,
                "tag_id": "undefined",
                "timestamp": int(time.time()),
            },
            timeout=30,
        )
        content = NetworkUtils.parse_response(resp)
        if not content:
            Logger.warning("获取动态流失败")
            return

        data = content.get("data") or {}
        items = data.get("current_page", [])

        # 过滤出可评论的帖子
        actionable = []
        for it in items:
            if it.get("cmd_id") != 7003:
                continue
            text = (it.get("data", {}).get("text") or "").strip()
            aid = str(it.get("data", {}).get("article_id", ""))
            if text and aid not in commented:
                actionable.append(it)

        if not actionable:
            Logger.info("暂无可评论帖子")
            return

        offset = data.get("next_offset", -1)

        for it in actionable:
            if success_count >= max_comments:
                break

            aid = it["data"]["article_id"]
            txt = (it["data"]["text"] or "").strip()
            Logger.info(f"📝 处理帖子 {aid}: {txt[:40]}...")

            # 生成评论（AI 或默认）
            if use_fallback:
                comment = random.choice(FALLBACK_COMMENTS)
            else:
                comment = self._ai_comment(txt, ai_config)
            try:
                post_resp = sess.post(
                    URLS["COMMENT"],
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "comment_article_id": aid,
                        "post_text": comment,
                        "post_atcount": 0,
                    },
                    timeout=10,
                )
                res = post_resp.json()
                gold = res.get("gold")
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if res.get("code") == 200 and res.get("data"):
                    success_count += 1
                    commented.add(str(aid))
                    prog.update({
                        "commented": list(commented),
                        "offset": offset,
                    })
                    self._save_comment_progress(prog)
                    Logger.success(
                        f"评论 {aid}: {comment} 米粒: {gold}"
                    )
                    print(
                        f"[✅]{now_str} 评论 {aid}: {comment} 米粒: {gold}"
                    )
                    if gold == -1:
                        Logger.info("无法获得米粒，停止评论")
                        break
                    # 长间隔模拟真人
                    time.sleep(random.uniform(30, 60))
                else:
                    Logger.warning(f"评论失败 {aid}: {res}")
                    time.sleep(20)
            except Exception as e:
                Logger.error(f"评论请求异常 {aid}: {e}")
                time.sleep(20)

        # 结束统计
        end_gold = self.get_gold_balance()
        if start_gold is not None and end_gold is not None:
            Logger.info(
                f"评论模块米粒变化: {start_gold} → {end_gold} "
                f"(+{end_gold - start_gold})"
            )
        Logger.info(f"💬 自动评论完成，共 {success_count} 条")

    def _get_ai_provider_config(self) -> Optional[dict]:
        """获取 AI 提供商配置（api_key / base_url / model）"""
        global AI_PROVIDER, AI_CFG

        api_key = os.environ.get(AI_CFG["env_key"])
        if api_key:
            model = os.environ.get(AI_CFG["env_model"], AI_CFG["default_model"])
            return {
                "provider": AI_PROVIDER,
                "label": AI_CFG["label"],
                "api_key": api_key,
                "base_url": AI_CFG["base_url"],
                "model": model,
            }

        # 非自动模式：交互输入 API Key
        if not IS_AUTO_MODE:
            print(f"\n当前 AI 提供商: {AI_CFG['label']} ({AI_PROVIDER})")
            print(f"  如需切换请设置 AI_PROVIDER=deepseek 或 AI_PROVIDER=moonshot")
            key = input(f"请输入 {AI_CFG['label']} API Key (留空跳过评论): ").strip()
            if key:
                model = os.environ.get(AI_CFG["env_model"], AI_CFG["default_model"])
                return {
                    "provider": AI_PROVIDER,
                    "label": AI_CFG["label"],
                    "api_key": key,
                    "base_url": AI_CFG["base_url"],
                    "model": model,
                }

        Logger.info(
            f"未配置 {AI_CFG['env_key']}，将使用默认评论。"
            f"设置 {AI_CFG['env_key']} 或切换 AI_PROVIDER=moonshot 可启用 AI"
        )
        return None

    def _ai_comment(self, post_text: str, cfg: dict) -> str:
        """调用 AI API 生成评论（支持 Moonshot / DeepSeek）"""
        fallbacks = FALLBACK_COMMENTS[:]
        try:
            payload = {
                "model": cfg["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是一个在摩尔庄园社区活跃多年的真实玩家。"
                            "你的回复特点是：1）简短自然 2）贴合帖子内容 3）语气像真人"
                            " 4）多样化，避免重复句式。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"请为这篇帖子写一条中文评论（5-20字），"
                            f"要贴合帖子内容、语气自然：\n“{post_text}”"
                        ),
                    },
                ],
                "temperature": 0.9,
            }
            url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            if resp.ok:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                return text
        except Exception as e:
            Logger.warning(f"{cfg['label']} 评论生成失败: {e}")
        return random.choice(fallbacks)

    def _load_comment_progress(self) -> dict:
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"commented": [], "offset": -1}

    def _save_comment_progress(self, prog: dict) -> None:
        try:
            with open(PROGRESS_FILE, "w") as f:
                json.dump(prog, f, indent=2, ensure_ascii=False)
        except Exception as e:
            Logger.error(f"保存评论进度失败: {e}")

    # ---- 自动补签 ----

    def auto_repair_sign(self) -> None:
        """检查最近签到记录并补签"""
        Logger.info("🛠️  开始自动补签...")

        # 获取最近签到数据
        resp = NetworkUtils.request(
            "GET", URLS["COMPLEMENT_RECENT"],
            headers=self.auth.get_headers(),
        )
        result = NetworkUtils.parse_response(resp)
        days = result.get("data", []) if result else []

        if not days:
            Logger.warning("获取补签数据失败")
            return

        # 找出未签到日期
        missed = [d for d in days if list(d.values())[0] == 0]
        if not missed:
            Logger.success("无需补签，最近都已签到")
            return

        Logger.info(f"发现 {len(missed)} 天需要补签")
        for day in missed:
            date = list(day.keys())[0]
            Logger.info(f"🗓️  补签 {date} ...")
            resp = NetworkUtils.request(
                "POST",
                URLS["COMPLEMENT_DO"],
                headers=self.auth.get_headers("form"),
                data=f"complement_date={date}",
            )
            result = NetworkUtils.parse_response(resp)
            if result and result.get("code") == 200:
                Logger.success(f"补签 {date} 成功")
            else:
                Logger.warning(f"补签 {date} 失败: {result}")
            time.sleep(3)

        Logger.success("自动补签完成")


# ======================================================================
# 账号管理 —— 单个账号全流程编排
# ======================================================================

class MiFanAccount:
    """
    单个账号的完整执行流程
    登录 → 查询余额 → 执行任务 → 查询余额 → 登出 → 返回结果
    """

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.auth = MiFanAuth(username, password)
        self.client = MiFanClient(self.auth)  # 要在 login 后使用
        self.gold_before: Optional[int] = None
        self.gold_after: Optional[int] = None
        self.success = False

    def run(self, tasks: list[str]) -> dict:
        """
        执行选定的一组任务
        tasks: 任务名称列表，如 ['sign', 'like', 'comment', 'repair']
        返回 {'account': ..., 'status': ..., 'gold_change': ...}
        """
        Logger.info(f"\n{'='*50}")
        Logger.info(f"🚀 开始处理账号: {self.username}")

        if not self.auth.login():
            return {
                "account": self.username,
                "status": False,
                "message": "登录失败",
            }

        # 查询余额（任务前）
        self.gold_before = self.client.get_gold_balance()

        # 顺序执行任务
        task_map = {
            "sign":   ("📝 签到",       self.client.daily_sign),
            "like":   ("👍 点赞",       self.client.batch_like),
            "comment":("💬 评论",       lambda: self.client.auto_comment(10)),
            "repair": ("🛠️  补签",     self.client.auto_repair_sign),
        }

        for key in tasks:
            if key in task_map:
                label, func = task_map[key]
                Logger.info(f"\n{'─'*40}")
                Logger.info(f"执行任务: {label}")

                try:
                    func()
                except Exception as e:
                    Logger.error(f"任务 {label} 异常: {e}")
                Logger.info(f"{'─'*40}\n")

        # 查询余额（任务后）
        self.gold_after = self.client.get_gold_balance()

        # 登出
        self.auth.logout()

        # 统计
        gold_change = ""
        if self.gold_before is not None and self.gold_after is not None:
            diff = self.gold_after - self.gold_before
            gold_change = f"{self.gold_before} → {self.gold_after} (+{diff})"
            Logger.success(f"📈 米粒变化: {gold_change}")

        self.success = True
        return {
            "account": self.username,
            "status": True,
            "gold_before": self.gold_before,
            "gold_after": self.gold_after,
            "gold_change": gold_change,
        }


# ======================================================================
#青龙面板通知
# ======================================================================

def ql_notify(title: str, content: str):
    """发送青龙通知（按 mifan.js 语义封装）"""
    Notifier.send(title, content)


# ======================================================================
# 主入口
# ======================================================================

def main():
    """主入口：青龙模式 / 本地终端交互模式"""
    Logger.init()

    # ---- 从环境变量读取配置 ----
    env_user = os.environ.get("MIFAN_USER", "").strip()
    env_password = os.environ.get("MIFAN_PASSWORD", "").strip()
    success_notify = os.environ.get("MIFAN_SUCCESS_NOTIFY", "false").lower()
    fail_notify = os.environ.get("MIFAN_FAIL_NOTIFY", "false").lower()

    # ---- 青龙模式：环境变量已配置账号 ----
    if env_user and env_password:
        users = [u.strip() for u in env_user.split(";") if u.strip()]
        passwords = [p.strip() for p in env_password.split(";") if p.strip()]

        if len(users) != len(passwords):
            Logger.error(
                "MIFAN_USER 和 MIFAN_PASSWORD 账号数量不一致，请检查配置"
            )
            sys.exit(1)

        Logger.info(f"🍚 米饭APP日常脚本 — 青龙模式")
        Logger.info(f"   检测到 {len(users)} 个账号")

        # 青龙全自动模式：默认签到 + 点赞
        tasks = ["sign", "like"]
        # AI 评论（有 Key 就用 AI，没有用默认评论）
        tasks.append("comment")
        ai_key_env = AI_CFG["env_key"]
        if os.environ.get(ai_key_env):
            Logger.info(f"检测到 {AI_CFG['label']} API Key，启用 AI 自动评论")
        # 补签（默认关闭，通过 ENABLE_REPAIR=true 开启）
        if os.environ.get("ENABLE_REPAIR", "false").lower() == "true":
            tasks.append("repair")
            Logger.info("检测到 ENABLE_REPAIR=true，启用自动补签")

        results = []
        for idx, (user, pwd) in enumerate(zip(users, passwords)):
            account = MiFanAccount(user, pwd)
            result = account.run(tasks)
            results.append(result)

            # 账号间延迟
            if idx < len(users) - 1:
                Logger.info("⏳ 账号间等待 3 秒...")
                time.sleep(3)

        # 统计汇总
        success_count = sum(1 for r in results if r.get("status"))
        fail_count = len(results) - success_count

        summary_lines = [
            f"账号总数: {len(results)}",
            f"成功: {success_count}",
            f"失败: {fail_count}",
        ]
        for r in results:
            acc = r.get("account", "?")
            status = "✅ 成功" if r.get("status") else "❌ 失败"
            gold = r.get("gold_change", "?")
            summary_lines.append(f"  {acc}: {status} (米粒: {gold})")

        summary = "\n".join(summary_lines)
        Logger.info(f"\n{'='*50}")
        Logger.info("📊 执行汇总")
        for line in summary_lines:
            Logger.info(f"  {line}")
        Logger.info(f"{'='*50}")

        # 通知
        if success_notify == "true" and success_count > 0:
            ql_notify("米饭APP签到通知", summary)
        if fail_notify == "true" and fail_count > 0:
            ql_notify("米饭APP签到通知(失败)", summary)

        return

    # ---- 本地终端交互模式 ----
    print("=" * 50)
    print("      🍚 米饭APP日常脚本 — 交互模式")
    print("=" * 50)

    # 输入账号
    user = input("\n请输入账号 (用户名): ").strip()
    if not user:
        Logger.error("账号不能为空")
        return
    pwd = input("请输入密码: ").strip()
    if not pwd:
        Logger.error("密码不能为空")
        return

    # 选择任务
    print("\n可选任务:")
    print("  0. 全部执行（签到+点赞+评论+补签）")
    print("  1. 每日签到")
    print("  2. 自动点赞")
    print("  3. 自动评论")
    print("  4. 自动补签")
    choice = input("\n请选择 (0-4): ").strip()

    task_map = {
        "0": ["sign", "like", "comment", "repair"],
        "1": ["sign"],
        "2": ["like"],
        "3": ["comment"],
        "4": ["repair"],
    }

    tasks = task_map.get(choice)
    if tasks is None:
        print("❌ 无效选择")
        return

    # 执行
    account = MiFanAccount(user, pwd)
    account.run(tasks)

    print("\n" + "=" * 50)
    print("✅ 所有任务执行完毕")
    print("=" * 50)


if __name__ == "__main__":
    main()