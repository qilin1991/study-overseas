# -*- coding: utf-8 -*-
"""境外采集器（在 GitHub Actions 上运行，出口在境外，可访问被墙站）。

- 源：被墙的欧美主流媒体（NYT/BBC/卫报/WaPo/CNN/路透/彭博/经济学人/WSJ/Fox 等）
- 抓取：RSS(requests) 取标题/链接/时间；trafilatura 取正文段落
- 翻译：mymemory 公益翻译（免 key，零 LLM 成本）
- 输出：news_overseas.json（与 VPS news.json 同结构，供 VPS 合并）

不依赖 LLM、不依赖 VPS 环境，纯 pip 依赖。
"""
import os
import re
import json
import html
import time
import datetime
import email.utils
import requests
import trafilatura
from feedparser import parse as feedparse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'news_overseas.json')
KEEP_PER_SRC = 3            # 每源本次最多抓几篇
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0 Safari/537.36'}

# ── 被墙源（境外机器可达）──
SOURCES = [
    {'key': 'nytimes',  'name': 'The New York Times', 'cat': '时政',
     'feed': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml', 'accent': '#000000'},
    {'key': 'bbc',      'name': 'BBC News', 'cat': '时政',
     'feed': 'https://feeds.bbci.co.uk/news/world/rss.xml', 'accent': '#BB1919'},
    {'key': 'guardian', 'name': 'The Guardian', 'cat': '时政',
     'feed': 'https://www.theguardian.com/world/rss', 'accent': '#052962'},
    {'key': 'wapo',     'name': 'Washington Post', 'cat': '时政',
     'feed': 'https://feeds.washingtonpost.com/rss/world', 'accent': '#111111'},
    {'key': 'cnn',      'name': 'CNN', 'cat': '时政',
     'feed': 'https://rss.cnn.com/rss/edition_world.rss', 'accent': '#CC0000'},
    {'key': 'reuters',  'name': 'Reuters', 'cat': '时政',
     'feed': 'https://www.reutersagency.com/feed/?best-topics=world&post_type=best', 'accent': '#F98100'},
    {'key': 'bloomberg', 'name': 'Bloomberg', 'cat': '财经',
     'feed': 'https://feeds.bloomberg.com/markets/news.rss', 'accent': '#000000'},
    {'key': 'economist', 'name': 'The Economist', 'cat': '财经',
     'feed': 'https://www.economist.com/finance-and-economics/rss.xml', 'accent': '#E3120B'},
    {'key': 'wsj',      'name': 'WSJ', 'cat': '财经',
     'feed': 'https://feeds.a.dj.com/rss/RSSWorldNews.xml', 'accent': '#0274B6'},
    {'key': 'fox',      'name': 'Fox News', 'cat': '时政',
     'feed': 'https://moxie.foxnews.com/google-publisher/latest.xml', 'accent': '#003366'},
    {'key': 'apnews',   'name': 'AP News', 'cat': '时政',
     'feed': 'https://rsshub.app/apnews/topics/apf-topnews', 'accent': '#D81E05'},
    {'key': 'nationalgeo', 'name': 'National Geographic', 'cat': '科学',
     'feed': 'https://www.nationalgeographic.com/feed', 'accent': '#FFCC00'},
]

NOISE = ['sign up', 'subscribe', 'newsletter', 'cookie', 'privacy policy',
         'terms of service', '© 20', '(c) 20', 'all rights reserved',
         'advertisement', 'read more', 'follow us', 'share this']

MM_EP = 'https://api.mymemory.translated.net/get'
_mm_cache = {}


def mm_translate(text):
    text = (text or '').strip()
    if not text:
        return ''
    if text in _mm_cache:
        return _mm_cache[text]
    try:
        r = requests.get(MM_EP, params={'q': text, 'langpair': 'en|zh-CN'},
                         headers=UA, timeout=10)
        if r.status_code == 200:
            d = r.json() or {}
            t = ((d.get('responseData') or {}).get('translatedText') or '').strip()
            if t and 'MYMEMORY WARNING' not in t and 'QUOTA' not in t.upper():
                if len(_mm_cache) > 3000:
                    _mm_cache.clear()
                _mm_cache[text] = t
                return t
    except Exception:
        pass
    return ''


def _chunks(text, n=440):
    out, buf, cur = [], [], 0
    for w in text.split(' '):
        if buf and cur + len(w) + 1 > n:
            out.append(' '.join(buf)); buf, cur = [], 0
        buf.append(w); cur += len(w) + 1
    if buf:
        out.append(' '.join(buf))
    return out


def free_translate_paras(paras):
    res = []
    for p in paras:
        if len(p) <= 480:
            res.append(mm_translate(p))
        else:
            res.append(' '.join(mm_translate(c) for c in _chunks(p)))
    return res


def slug_of(link):
    s = link.split('?')[0].rstrip('/')
    return s.rsplit('/', 1)[-1] or s


def norm_url(u):
    u = (u or '').strip().split('?', 1)[0].rstrip('/')
    u = re.sub(r'^https?://', '', u, flags=re.I)
    return re.sub(r'^www\.', '', u, flags=re.I)


def fetch_body(url):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        raw = r.text
    except Exception as e:
        print('  BODY_FAIL', url, type(e).__name__)
        return None
    text = trafilatura.extract(raw, include_comments=False, favor_precision=True,
                               include_tables=False)
    if not text:
        return None
    blocks = text.split('\n') if text.count('\n\n') == 0 else re.split(r'\n\s*\n', text)
    paras = []
    for b in blocks:
        p = re.sub(r'\s+', ' ', html.unescape(b)).strip()
        if len(p) < 40:
            continue
        if any(k in p.lower() for k in NOISE):
            continue
        paras.append(p)
    return paras or None


def main():
    data = {'articles': []}
    if os.path.exists(OUT):
        try:
            data = json.load(open(OUT, encoding='utf-8'))
            data.setdefault('articles', [])
        except Exception:
            data = {'articles': []}
    existing = {a.get('id') for a in data['articles']}
    existing_urls = {norm_url(a.get('url')) for a in data['articles'] if a.get('url')}
    added = 0
    for src in SOURCES:
        try:
            r = requests.get(src['feed'], headers=UA, timeout=25)
            r.raise_for_status()
            feed = feedparse(r.content)
        except Exception as e:
            print('FEED_FAIL', src['key'], type(e).__name__, flush=True)
            continue
        entries = list(feed.entries)[:30]
        entries.sort(key=lambda e: (e.get('published_parsed') or e.get('updated_parsed')
                                    or time.gmtime(0)), reverse=True)
        got = 0
        for e in entries:
            link = (e.get('link') or '').strip()
            if not link:
                continue
            sid = src['key'] + '-' + slug_of(link)
            if sid in existing or norm_url(link) in existing_urls:
                continue
            title = (e.get('title') or '').strip()
            if not title:
                continue
            paras = fetch_body(link)
            if not paras or len(paras) < 3:
                continue
            zh = free_translate_paras(paras)
            date_s = ''
            if e.get('published_parsed'):
                date_s = datetime.datetime(*e['published_parsed'][:6]).strftime('%Y-%m-%d')
            elif e.get('published'):
                try:
                    date_s = email.utils.parsedate_to_datetime(e['published']).strftime('%Y-%m-%d')
                except Exception:
                    date_s = (e.get('published') or '')[:10]
            art = {
                'id': sid, 'source': src['key'], 'source_name': src['name'],
                'cat': src['cat'], 'date': date_s, 'title': title, 'url': link,
                'author': (e.get('author') or '').strip(), 'category': src['name'],
                'paras': [{'en': en, 'zh': z} for en, z in zip(paras, zh)],
                'vocab': [],
                'ingested_at': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            }
            data['articles'].insert(0, art)
            existing.add(sid)
            existing_urls.add(norm_url(link))
            added += 1
            got += 1
            if got >= KEEP_PER_SRC:
                break
        print('SRC', src['key'], 'added', got, flush=True)
    data['articles'] = data['articles'][:200]
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('OK total_added', added, 'total', len(data['articles']), flush=True)


if __name__ == '__main__':
    main()
