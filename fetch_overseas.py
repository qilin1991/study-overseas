#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VPS 侧：拉取 GitHub 仓库里 Actions 采集的 news_overseas.json，
合并进 content/news.json（按 id/url 去重），供前端展示被墙源文章。

由 systemd timer 定时触发（study-overseas-fetch.timer），或手动执行。
可用环境变量覆盖：
  OVERSEAS_URL  原始 JSON 地址（默认 GitHub raw）
  OVERSEAS_RAW  本地兜底文件路径
"""
import os
import re
import sys
import json
import datetime
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(BASE, 'content')
NEWS_FILE = os.path.join(CONTENT, 'news.json')
KEEP = 200

# 默认从 GitHub raw 拉取（仓库名稍后由部署脚本替换）
DEFAULT_URL = os.environ.get(
    'OVERSEAS_URL',
    'https://raw.githubusercontent.com/qilin1991/study-overseas/main/news_overseas.json')
UA = {'User-Agent': 'Mozilla/5.0 (Study-VPS-Merger)'}


def norm_url(u):
    u = (u or '').strip().split('?', 1)[0].rstrip('/')
    u = re.sub(r'^https?://', '', u, flags=re.I)
    return re.sub(r'^www\.', '', u, flags=re.I)


def main():
    url = DEFAULT_URL
    for a in sys.argv[1:]:
        if a.startswith('--url='):
            url = a.split('=', 1)[1]
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        remote = r.json()
    except Exception as e:
        print('FETCH_FAIL', type(e).__name__, str(e)[:120])
        return 1
    rows = remote.get('articles') if isinstance(remote, dict) else remote
    if not rows:
        print('EMPTY remote')
        return 1

    data = {'articles': []}
    if os.path.exists(NEWS_FILE):
        try:
            data = json.load(open(NEWS_FILE, encoding='utf-8'))
            data.setdefault('articles', [])
        except Exception:
            data = {'articles': []}
    have_ids = {a.get('id') for a in data['articles']}
    have_urls = {norm_url(a.get('url')) for a in data['articles'] if a.get('url')}

    added = 0
    for a in rows:
        aid = a.get('id')
        nu = norm_url(a.get('url'))
        if aid in have_ids or nu in have_urls:
            continue
        data['articles'].insert(0, a)
        have_ids.add(aid)
        have_urls.add(nu)
        added += 1

    data['articles'] = data['articles'][:KEEP]
    with open(NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('MERGED added', added, 'total', len(data['articles']),
          'at', datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
