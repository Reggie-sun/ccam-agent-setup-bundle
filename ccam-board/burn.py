"""Live token burn per ccam account.

Quota is only known as a percentage, so speed comes from the transcripts of the
sessions each account is running: new input, output and cache-creation tokens
written since the last sample, divided by the time between samples. Cache reads
are left out, they barely count against the quota. History before the tracker
started is not counted.

Tokens land in a transcript once per finished request, so a plain average over
the last seconds is 0 most of the time and jumps when a request ends. Each
amount is spread over the following TAU seconds instead (an exponentially
decaying rate), so the figure fades out rather than dropping to 0, and a
steady load reads as its true rate.
"""

import json
import math
import re
import time
from collections import deque
from pathlib import Path

PROFILES = Path.home() / ".local/share/ccam/profiles"
MAIN = Path.home() / ".claude"
TAU = 30.0  # 新增的 token 按这个时间常数（秒）指数衰减地摊开
HISTORY = 90  # 迷你曲线保留的采样点数


def config_dir(name):
    return MAIN if name == "main" else PROFILES / name


def slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def started(pid):
    """进程启动时刻（/proc stat 第 22 列）；进程不在就是 None。"""
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


class Tracker:
    def __init__(self):
        self.files = {}  # transcript path -> [offset, {message id: tokens}]
        self.total = {}  # account -> cumulative tokens seen
        self.ema = {}  # account -> (time, tokens/s)
        self.rates = {}  # account -> deque of tokens/s samples
        self.quota = {}  # account -> deque[(time, five-hour percent)]

    def owners(self, names):
        """对话记录 → 账号。同一份对话同时开在几个进程里时，算给最后启动的那个。"""
        best = {}
        for name in names:
            for meta in (config_dir(name) / "sessions").glob("*.json"):
                try:
                    data = json.loads(meta.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                since = started(data.get("pid", 0))
                path = config_dir(name) / "projects" / slug(data.get("cwd", "")) / f"{data.get('sessionId')}.jsonl"
                if since is None or not path.exists():
                    continue
                key = path.resolve()
                if key not in best or since > best[key][0]:
                    best[key] = (since, name)
        return {path: name for path, (_since, name) in best.items()}

    def read_new(self, path):
        size = path.stat().st_size
        state = self.files.get(path)
        if state is None:  # 第一次见到：从末尾开始，不算历史
            self.files[path] = [size, {}]
            return 0
        offset, seen = state
        if size <= offset:
            state[0] = size
            return 0
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read(size - offset)
        end = chunk.rfind(b"\n") + 1  # 只处理完整的行
        state[0] = offset + end
        gained = 0
        for line in chunk[:end].splitlines():
            if b'"usage"' not in line:
                continue
            try:
                message = json.loads(line).get("message") or {}
            except json.JSONDecodeError:
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) \
                + (usage.get("cache_creation_input_tokens") or 0)
            key = message.get("id") or line[:80]
            gained += max(tokens - seen.get(key, 0), 0)  # 同一条消息的流式更新只算新增部分
            seen[key] = max(tokens, seen.get(key, 0))
        return gained

    def sample(self, names):
        now = time.monotonic()
        owners = self.owners(names)
        for name in names:
            gained = sum(self.read_new(path) for path, owner in owners.items() if owner == name)
            self.total[name] = self.total.get(name, 0) + gained
            rate = self.feed(name, gained, now)
            self.rates.setdefault(name, deque(maxlen=HISTORY)).append(rate)

    def feed(self, name, gained, now):
        """Add gained tokens seen at now; return the account's tokens/s."""
        last, rate = self.ema.get(name, (now, 0.0))
        rate = rate * math.exp(-(now - last) / TAU) + gained / TAU
        self.ema[name] = (now, rate)
        return rate

    def rate(self, name):
        history = self.rates.get(name)
        return history[-1] if history else 0.0

    def history(self, name):
        return list(self.rates.get(name, ()))

    def note_quota(self, name, percent):
        now = time.monotonic()
        points = self.quota.setdefault(name, deque())
        points.append((now, percent))
        while points and now - points[0][0] > 600:
            points.popleft()

    def minutes_to_full(self, name):
        """按最近 10 分钟 5 小时额度的上升速度，估计多久用满；没有上升就是 None。"""
        points = self.quota.get(name)
        if not points or len(points) < 2:
            return None
        (t0, p0), (t1, p1) = points[0], points[-1]
        if t1 - t0 < 60 or p1 <= p0:
            return None
        per_minute = (p1 - p0) / ((t1 - t0) / 60)
        return (100 - p1) / per_minute


def format_rate(rate):
    if rate >= 1000:
        return f"{rate / 1000:.1f}k"
    return f"{rate:.0f}"
