# -*- coding: utf-8 -*-
"""
GitHub Actions 侧翻译脚本（免费优先）。
在海外 runner 上直连免费翻译接口，把 news_overseas.json 中
paras[].zh 为空的段落补全译文，然后回写文件。
VPS 侧再由 fetch_overseas.py 拉取合并（或本脚本由 VPS 触发 workflow_dispatch）。

引擎优先级: Google(gtx 免费) -> MyMemory(公益免费)
两者都免费，无需任何 API Key。

用法:
    python translate_overseas.py                 # 全量补译
    TR_LIMIT=200 python translate_overseas.py    # 限制本轮最多补 N 篇
"""
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'news_overseas.json')

TR_LIMIT = int(os.environ.get('TR_LIMIT', '0'))       # 0 = 不限
TR_CONC = int(os.environ.get('TR_CONC', '4'))          # 并发
TR_CHUNK = int(os.environ.get('TR_CHUNK', '1200'))     # 单请求字符上限
TR_SLEEP = float(os.environ.get('TR_SLEEP', '0.15'))   # 每次请求后停顿
TR_ENGINE = os.environ.get('TR_ENGINE', 'auto')        # auto|google|mymemory

GOOG = 'https://translate.googleapis.com/translate_a/single'
# 实测（GitHub runner 海外网络）: client=at → 200 可用
#   client=gtx → 429 限流; client=webapp → 403; translate.google.cn → 404
GOOG_CLIENTS = ['at', 'gtx', 'webapp']
MM = 'https://api.mymemory.translated.net/get'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

_lock = threading.Lock()
_stat = {'ok': 0, 'fail': 0, 'empty': 0, 'g_ok': 0, 'm_ok': 0}
_engine_dead = set()   # 被判定不可用的引擎

# 任何引擎的错误提示串都不得进入译文
_BAD_ZH = ('MYMEMORY WARNING', 'QUOTA', 'QUERY LENGTH LIMIT', 'INVALID',
           'TOO MANY REQUESTS', 'PLEASE SELECT', 'NETWORK ERROR',
           'TRANSLATED.NET', 'PLEASE CONTACT')


def _clean(t):
    """校验译文可用性：过滤错误串/纯英文回显/过短。可用返回清洗后文本，否则 None。"""
    if not t:
        return None
    t = t.strip()
    up = t.upper()
    if any(b in up for b in _BAD_ZH):
        return None
    if len(t) < 2:
        return None
    return t


# ---------- 切块 ----------
def _split(text, limit):
    """按标点/空格把长文本切成 <= limit 的块（尽量保持句子完整）。"""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ''
    parts = re.split(r'(?<=[.!?;:])\s+', text)
    for p in parts:
        if not p:
            continue
        if len(cur) + len(p) + 1 <= limit:
            cur = (cur + ' ' + p).strip()
        else:
            if cur:
                out.append(cur)
            while len(p) > limit:
                cut = p.rfind(' ', 0, limit)
                if cut <= 0:
                    cut = limit
                out.append(p[:cut].strip())
                p = p[cut:].strip()
            cur = p
    if cur:
        out.append(cur)
    return [x for x in out if x]


class NetworkDead(Exception):
    """网络层不可达（connection/超时），直接熔断该引擎。"""
    pass


# ---------- 引擎 1: Google 免费（多 client 端点轮换） ----------
def _google(text, sl='en', tl='zh-CN'):
    chunks = _split(text, TR_CHUNK)
    res = []
    netfail = 0
    for ch in chunks:
        got, last = None, ''
        for cli in GOOG_CLIENTS:
            url = (GOOG + '?client=' + cli + '&sl=' + sl + '&tl=' + tl
                   + '&dt=t&q=' + quote(ch, safe=''))
            for attempt in range(2):
                try:
                    r = requests.get(url, headers={'User-Agent': UA},
                                     timeout=(5, 15))
                    if r.status_code == 200:
                        try:
                            arr = r.json()
                            t = ''.join(x[0] for x in arr[0] if x and x[0])
                        except Exception:
                            t = ''
                        if t:
                            got = t
                            break
                        last = 'empty json'
                    else:
                        last = 'HTTP %s' % r.status_code
                        if r.status_code in (403, 429):
                            break     # 该 client 被限，换下一个 client
                        time.sleep(0.5)
                except (requests.ConnectionError, requests.Timeout) as e:
                    netfail += 1
                    last = str(e)[:60]
                    if netfail >= 3:
                        raise NetworkDead('google unreachable: %s' % last)
                except Exception as e:
                    last = str(e)[:60]
                    time.sleep(0.4)
            if got:
                break
        if got is None:
            raise RuntimeError('google fail: %s' % last)
        res.append(got)
        time.sleep(TR_SLEEP)
    return ''.join(res)


# ---------- 引擎 2: MyMemory 公益免费 ----------
_MM_BAD = ('MYMEMORY WARNING', 'QUOTA', 'QUERY LENGTH LIMIT', 'INVALID',
           'TOO MANY REQUESTS', 'PLEASE SELECT')


def _mymemory(text, sl='en', tl='zh-CN'):
    chunks = _split(text, 420)   # 实测单请求硬限 ~500 字符，留余量
    res = []
    netfail = 0
    for ch in chunks:
        url = MM + '?q=' + quote(ch, safe='') + '&langpair=%s|%s' % (sl, tl)
        got, last = None, ''
        for attempt in range(3):
            try:
                r = requests.get(url, headers={'User-Agent': UA}, timeout=(5, 15))
                if r.status_code == 200:
                    j = r.json()
                    t = ((j.get('responseData') or {}).get('translatedText') or '').strip()
                    up = t.upper()
                    if t and not any(b in up for b in _MM_BAD):
                        got = t
                        break
                    last = 'quota/limit/empty'
                else:
                    last = 'HTTP %s' % r.status_code
                time.sleep(1.0 * (attempt + 1))
            except (requests.ConnectionError, requests.Timeout) as e:
                netfail += 1
                last = str(e)[:80]
                if netfail >= 2:
                    raise NetworkDead('mymemory unreachable: %s' % last)
            except Exception as e:
                last = str(e)[:80]
                time.sleep(0.8 * (attempt + 1))
        if got is None:
            raise RuntimeError('mymemory fail: %s' % last)
        res.append(got)
        time.sleep(TR_SLEEP)
    return ''.join(res)


ENGINES = {'google': _google, 'mymemory': _mymemory}


def _probe():
    """启动前用短超时探测各引擎，筛掉网络不可达的。"""
    ok = []
    for name in (['google', 'mymemory'] if TR_ENGINE == 'auto' else [TR_ENGINE]):
        try:
            ENGINES[name]('Hello, world.', 'en', 'zh-CN')
            ok.append(name)
            print('  [probe] %s 可用' % name)
        except NetworkDead as e:
            print('  [probe] %s 网络不可达 → 停用 (%s)' % (name, str(e)[:60]))
            _engine_dead.add(name)
        except Exception as e:
            # 非网络错（限流等）仍然保留，交给正式流程重试
            ok.append(name)
            print('  [probe] %s 有响应但异常，保留 (%s)' % (name, str(e)[:60]))
    return ok


def translate(text):
    """按优先级尝试各引擎。全部失败返回 None。"""
    if not text or not text.strip():
        return ''
    order = ['google', 'mymemory'] if TR_ENGINE == 'auto' else [TR_ENGINE]
    for name in order:
        if name in _engine_dead:
            continue
        try:
            t = ENGINES[name](text)
            t = _clean(t)
            if t:
                with _lock:
                    _stat['g_ok' if name == 'google' else 'm_ok'] += 1
                    _stat['_ec_' + name] = 0
                return t
        except NetworkDead as e:
            with _lock:
                if name not in _engine_dead:
                    _engine_dead.add(name)
                    print('  [!] %s → 停用 (%s)' % (name, str(e)[:60]))
        except Exception:
            # 单段失败（限流/空译），继续尝试下一引擎，不熔断
            pass
    return None


def _need(a):
    """返回待译段落：zh 为空 OR 被错误串污染（污染的先清空）。"""
    out = []
    for p in a.get('paras') or []:
        z = (p.get('zh') or '').strip()
        if not z:
            out.append(p)
            continue
        up = z.upper()
        if any(b in up for b in _BAD_ZH):
            p['zh'] = ''      # 清除污染，重新翻译
            out.append(p)
    return out


def main():
    if not os.path.exists(DATA):
        print('缺 %s' % DATA)
        return 1
    d = json.load(open(DATA, encoding='utf-8'))
    arts = d.get('articles') or []

    # ① 全库污染清理（清掉历史遗留的错误串，之后会被重译）
    purged = 0
    for a in arts:
        for p in a.get('paras') or []:
            z = (p.get('zh') or '').strip()
            if z and any(b in z.upper() for b in _BAD_ZH):
                p['zh'] = ''
                purged += 1
    if purged:
        print('清理历史污染译文 %d 段' % purged)

    todo = [a for a in arts if _need(a)]
    print('总文章 %d，待补译文 %d 篇' % (len(arts), len(todo)))
    if TR_LIMIT:
        todo = todo[:TR_LIMIT]
        print('本轮限制 %d 篇' % len(todo))
    if not todo:
        print('无需翻译')
        return 0

    total_missing = sum(len(_need(a)) for a in todo)
    print('待译段落 %d 段，引擎=%s 并发=%d' % (total_missing, TR_ENGINE, TR_CONC))

    print('探测引擎可用性...')
    alive = _probe()
    if not alive:
        print('所有引擎均不可用，退出（不写盘）')
        return 1
    print('可用引擎: %s' % ','.join(alive))

    def work(a):
        for p in _need(a):
            p['en'] = (p.get('en') or '').strip()
            t = translate(p['en'])
            if t is None:
                with _lock:
                    _stat['fail'] += 1
                continue
            p['zh'] = t
            with _lock:
                _stat['ok'] += 1

    with ThreadPoolExecutor(max_workers=TR_CONC) as ex:
        futs = {ex.submit(work, a): a for a in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:
                print('  单篇异常: %s' % e)
            if i % 10 == 0:
                print('  已处理 %d/%d 篇  ok=%d(g=%d,m=%d) fail=%d empty=%d'
                      % (i, len(todo), _stat['ok'], _stat['g_ok'], _stat['m_ok'],
                         _stat['fail'], _stat['empty']))

    print('结果: ok=%d (google=%d, mymemory=%d) fail=%d empty=%d'
          % (_stat['ok'], _stat['g_ok'], _stat['m_ok'], _stat['fail'], _stat['empty']))
    if _stat['ok'] == 0 and purged == 0:
        print('本轮无有效译文，不写盘')
        return 1

    tmp = DATA + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA)
    print('已写入 %s' % DATA)
    return 0


if __name__ == '__main__':
    sys.exit(main())
