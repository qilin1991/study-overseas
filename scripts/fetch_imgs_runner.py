# -*- coding: utf-8 -*-
"""GitHub runner 端：按 INPUT_PAYLOAD（[{id,url}, ...]）在海外网络抓取文章配图，
下载到 imgs/<id>/，再 SCP 回 VPS ~/staging_imgs，最后 SSH 触发 VPS 端 ingest_staged.py
上传 COS 并写入 news.json。COS 凭证只留在 VPS，本 runner 不碰。

依赖: requests（Workflow 已 pip install）。SSH 私钥来自 $RELAY_KEY 文件，主机来自 $VPS_HOST/$VPS_USER。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0 Safari/537.36'}
MAX_IMG = 3
FETCH_TO = 25
DL_TO = 30

MAGIC = {b'\xff\xd8\xff': 'jpg', b'\x89PNG': 'png', b'RIFF': 'webp', b'GIF8': 'gif'}


def ext_of(url, content):
    u = url.lower().split('?')[0]
    for e in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        if u.endswith(e):
            return 'jpg' if e == '.jpeg' else e[1:]
    for head, e in MAGIC.items():
        if content[:len(head)] == head:
            if e == 'webp' and content[8:12] != b'WEBP':
                continue
            return e
    return 'jpg'


def is_img(content):
    if len(content) < 1500:
        return False
    for head in MAGIC:
        if content[:len(head)] == head:
            if head == b'RIFF' and content[8:12] != b'WEBP':
                continue
            return True
    return False


def extract_imgs(html, base):
    out = []
    for pat in [
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
        r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+name="twitter:image"',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            out.append(m.group(1).strip())
    for m in re.finditer(r'<img\b[^>]*>', html, re.I):
        tag = m.group(0)
        sm = re.search(r'src="([^"]+)"', tag, re.I)
        if not sm:
            continue
        src = sm.group(1).strip()
        if src.startswith('data:'):
            continue
        if re.search(r'(logo|icon|avatar|pixel|spacer|1x1|sprite|\.svg$)', src, re.I):
            continue
        out.append(src)
    seen = set()
    res = []
    from urllib.parse import urljoin
    for u in out:
        try:
            a = urljoin(base, u)
        except Exception:
            continue
        if a in seen:
            continue
        seen.add(a)
        res.append(a)
    return res


def safeid(aid):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', aid or 'art')[:80]


def fetch_one(item, out_root):
    aid = item.get('id') or ''
    url = item.get('url') or ''
    if not url:
        return 0
    import requests
    try:
        r = requests.get(url, headers=UA, timeout=FETCH_TO)
        html = r.text if r.status_code == 200 else ''
    except Exception:
        return 0
    if not html:
        return 0
    urls = extract_imgs(html, url)[:MAX_IMG]
    d = os.path.join(out_root, safeid(aid))
    os.makedirs(d, exist_ok=True)
    n = 0
    for i, u in enumerate(urls):
        try:
            rr = requests.get(u, headers=UA, timeout=DL_TO)
            if rr.status_code != 200:
                continue
            c = rr.content
            if not is_img(c):
                continue
            ext = ext_of(u, c)
            fn = os.path.join(d, '%d.%s' % (i, ext))
            open(fn, 'wb').write(c)
            if os.path.getsize(fn) < 1500:
                os.remove(fn)
                continue
            n += 1
        except Exception:
            continue
    return n


def ssh_exec(cmd):
    key = os.environ['RELAY_KEY']
    host = os.environ['VPS_HOST']
    user = os.environ['VPS_USER']
    return subprocess.run(['ssh', '-i', key, '-o', 'StrictHostKeyChecking=no',
                           '-o', 'UserKnownHostsFile=/dev/null', '%s@%s' % (user, host), cmd],
                          check=True, capture_output=True, text=True)


def main():
    payload_raw = os.environ.get('INPUT_PAYLOAD', '').strip()
    if not payload_raw:
        print('无 payload，跳过')
        return
    items = json.loads(payload_raw)
    if isinstance(items, dict):
        items = items.get('items') or items.get('list') or []
    print('待抓图文章 %d 篇' % len(items))
    out_root = tempfile.mkdtemp(prefix='imgs_')
    total = 0
    for it in items:
        got = fetch_one(it, out_root)
        total += got
        print('  %s -> %d 张' % (it.get('id'), got), flush=True)
    print('共抓到 %d 张' % total)
    if total == 0:
        return
    key = os.environ['RELAY_KEY']
    host = os.environ['VPS_HOST']
    user = os.environ['VPS_USER']
    # 1) 建远端暂存目录
    ssh_exec('mkdir -p ~/staging_imgs')
    # 2) SCP 回 VPS（ubuntu home，避免 /opt/study 权限问题）
    scp = subprocess.run(['scp', '-i', key, '-o', 'StrictHostKeyChecking=no',
                          '-o', 'UserKnownHostsFile=/dev/null', '-r', out_root + '/.',
                          '%s@%s:~/staging_imgs/' % (user, host)],
                         check=True, capture_output=True, text=True)
    print('SCP 完成')
    # 3) 拷入 archive 属主 staging 并触发摄入
    ssh_exec('sudo cp -r ~/staging_imgs/. /opt/study/staging_imgs/ && '
             'sudo -u archive /opt/study/venv/bin/python /opt/study/ingest_staged.py && '
             'rm -rf ~/staging_imgs')
    print('VPS 摄入完成')


if __name__ == '__main__':
    main()
