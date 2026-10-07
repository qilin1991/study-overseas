# -*- coding: utf-8 -*-
"""境外采集器（在 GitHub Actions 上运行，出口在境外，可访问被墙站）。

- 源：被墙的欧美主流媒体（NYT/BBC/卫报/WaPo/CNN/路透/彭博/经济学人/WSJ/Fox 等）
- 抓取：RSS(requests) 取标题/链接/时间；trafilatura 取正文段落
- 翻译：多引擎免费翻译（Google 非官方端点 → mymemory），零 LLM 成本
- 输出：news_overseas.json（与 VPS news.json 同结构，供 VPS 合并）

不依赖 LLM、不依赖 VPS 环境，纯 pip 依赖。
"""
import os
import re
import json
import html
import time
import random
import datetime
import email.utils
import requests
import trafilatura
from feedparser import parse as feedparse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'news_overseas.json')
KEEP_PER_SRC = 3
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0 Safari/537.36'}

# ── 被墙源（境外机器可达）；RSS 地址均已核对 ──
SOURCES = [
    {'key': 'bbc',      'name': 'BBC News', 'cat': '时政',
     'feed': 'https://feeds.bbci.co.uk/news/world/rss.xml'},
    {'key': 'guardian', 'name': 'The Guardian', 'cat': '时政',
     'feed': 'https://www.theguardian.com/world/rss'},
    {'key': 'fox',      'name': 'Fox News', 'cat': '时政',
     'feed': 'https://moxie.foxnews.com/google-publisher/latest.xml'},
    {'key': 'cnn',      'name': 'CNN', 'cat': '时政',
     'feed': 'http://rss.cnn.com/rss/edition_world.rss'},
    {'key': 'npr',      'name': 'NPR', 'cat': '时政',
     'feed': 'https://feeds.npr.org/1004/rss.xml'},
    {'key': 'aljazeera', 'name': 'Al Jazeera', 'cat': '国际',
     'feed': 'https://www.aljazeera.com/xml/rss/all.xml'},
    {'key': 'dw',       'name': 'DW', 'cat': '国际',
     'feed': 'https://rss.dw.com/rdf/rss-en-world'},
    {'key': 'france24', 'name': 'France 24', 'cat': '国际',
     'feed': 'https://www.france24.com/en/rss'},
    {'key': 'skynews',  'name': 'Sky News', 'cat': '时政',
     'feed': 'https://feeds.skynews.com/feeds/rss/world.xml'},
    {'key': 'cbc',      'name': 'CBC', 'cat': '时政',
     'feed': 'https://www.cbc.ca/webfeed/rss/rss-world'},
    {'key': 'abcau',    'name': 'ABC News (AU)', 'cat': '时政',
     'feed': 'https://www.abc.net.au/news/feed/51120/rss.xml'},
    {'key': 'nbcnews',  'name': 'NBC News', 'cat': '时政',
     'feed': 'https://feeds.nbcnews.com/nbcnews/public/world'},
    {'key': 'espn',     'name': 'ESPN', 'cat': '体育',
     'feed': 'https://www.espn.com/espn/rss/news'},
    {'key': 'skysports', 'name': 'Sky Sports', 'cat': '体育',
     'feed': 'https://www.skysports.com/rss/12040'},
    {'key': 'espnformula1', 'name': 'ESPN F1', 'cat': '体育',
     'feed': 'https://www.espn.com/espn/rss/f1/news'},
    {'key': 'unnews',   'name': 'UN News', 'cat': '国际',
     'feed': 'https://news.un.org/feed/subscribe/en/news/all/rss.xml'},
    {'key': 'nasa',     'name': 'NASA', 'cat': '航天',
     'feed': 'https://www.nasa.gov/rss/dyn/breaking_news.rss'},
    {'key': 'spacenews', 'name': 'SpaceNews', 'cat': '航天',
     'feed': 'https://spacenews.com/feed/'},
    {'key': 'spacedotcom', 'name': 'Space.com', 'cat': '航天',
     'feed': 'https://www.space.com/feeds/all'},
    {'key': 'sciencedaily', 'name': 'ScienceDaily', 'cat': '科学',
     'feed': 'https://www.sciencedaily.com/rss/all.xml'},
    {'key': 'sciencealert', 'name': 'ScienceAlert', 'cat': '科学',
     'feed': 'https://www.sciencealert.com/feed'},
    {'key': 'sciencenews', 'name': 'Science News', 'cat': '科学',
     'feed': 'https://www.sciencenews.org/feed'},
    {'key': 'scientificamerican', 'name': 'Scientific American', 'cat': '科学',
     'feed': 'http://rss.sciam.com/ScientificAmerican-Global'},
    {'key': 'thetimes', 'name': 'The Times', 'cat': '时政',
     'feed': 'https://www.thetimes.co.uk/rss'},
    {'key': 'economist', 'name': 'The Economist', 'cat': '财经',
     'feed': 'https://www.economist.com/finance-and-economics/rss.xml'},
    {'key': 'cnbc',     'name': 'CNBC', 'cat': '财经',
     'feed': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'},
    {'key': 'ft',       'name': 'Financial Times', 'cat': '财经',
     'feed': 'https://www.ft.com/rss/home'},
    {'key': 'marketwatch', 'name': 'MarketWatch', 'cat': '财经',
     'feed': 'https://feeds.content.dowjones.io/public/rss/mw_topstories'},
    {'key': 'hollywoodreporter', 'name': 'Hollywood Reporter', 'cat': '娱乐',
     'feed': 'https://www.hollywoodreporter.com/feed/'},
    {'key': 'billboard', 'name': 'Billboard', 'cat': '娱乐',
     'feed': 'https://www.billboard.com/feed/'},
    {'key': 'medicalnewstoday', 'name': 'Medical News Today', 'cat': '健康',
     'feed': 'https://www.medicalnewstoday.com/newsfeeds/rss/medical.xml'},
    {'key': 'nyt', 'name': 'The New York Times', 'cat': '时政',
     'feed': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'},
    {'key': 'reuters', 'name': 'Reuters', 'cat': '时政',
     'feed': 'https://www.reuters.com/arc/outboundfeeds/rss/?outputType=xml'},
    {'key': 'theatlantic', 'name': 'The Atlantic', 'cat': '时政',
     'feed': 'https://www.theatlantic.com/feed/all/'},
    {'key': 'politico', 'name': 'Politico', 'cat': '时政',
     'feed': 'https://www.wired.com/feed/rss'},
    {'key': 'smithsonian', 'name': 'Smithsonian', 'cat': '科学',
     'feed': 'https://www.smithsonianmag.com/rss/smart-news/'},
    {'key': 'natgeo', 'name': 'National Geographic', 'cat': '科学',
     'feed': 'https://www.livescience.com/feeds/all'},
]

NOISE = ['sign up', 'subscribe', 'newsletter', 'cookie', 'privacy policy',
         'terms of service', '© 20', '(c) 20', 'all rights reserved',
         'advertisement', 'read more', 'follow us', 'share this']

# ── 免费翻译：多引擎，境外（Actions）优先 Google，失败退 mymemory ──
GT_EP = 'https://translate.googleapis.com/translate_a/single'
MM_EP = 'https://api.mymemory.translated.net/get'
_cache = {}


def _google(text):
    try:
        r = requests.get(GT_EP, params={'client': 'gtx', 'sl': 'en', 'tl': 'zh-CN',
                                        'dt': 't', 'q': text},
                         headers=UA, timeout=12)
        if r.status_code == 200:
            data = r.json()
            out = ''.join(seg[0] for seg in data[0] if seg and seg[0])
            return out.strip()
    except Exception:
        pass
    return ''


def _mymemory(text):
    try:
        r = requests.get(MM_EP, params={'q': text, 'langpair': 'en|zh-CN'},
                         headers=UA, timeout=10)
        if r.status_code == 200:
            d = r.json() or {}
            t = ((d.get('responseData') or {}).get('translatedText') or '').strip()
            if t and 'MYMEMORY WARNING' not in t and 'QUOTA' not in t.upper():
                return t
    except Exception:
        pass
    return ''


def translate(text):
    text = (text or '').strip()
    if not text:
        return ''
    if text in _cache:
        return _cache[text]
    for fn in (_google, _mymemory):
        t = fn(text)
        if t:
            if len(_cache) > 5000:
                _cache.clear()
            _cache[text] = t
            return t
    return ''


def _chunks(text, n=1200):
    """Google 单次可译较长文本；>1200 按词切分。"""
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
    for i, p in enumerate(paras):
        if len(p) <= 1400:
            res.append(translate(p))
        else:
            res.append(' '.join(translate(c) for c in _chunks(p)))
        time.sleep(0.15)      # 轻微限速，降低被限流概率
    return res


def slug_of(link):
    s = link.split('?')[0].rstrip('/')
    return s.rsplit('/', 1)[-1] or s


def norm_url(u):
    u = (u or '').strip().split('?', 1)[0].rstrip('/')
    u = re.sub(r'^https?://', '', u, flags=re.I)
    return re.sub(r'^www\.', '', u, flags=re.I)


def fetch_body(url):
    for attempt in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 200:
                text = trafilatura.extract(r.text, include_comments=False,
                                           favor_precision=True, include_tables=False)
                if text:
                    blocks = (text.split('\n') if text.count('\n\n') == 0
                              else re.split(r'\n\s*\n', text))
                    paras = []
                    for b in blocks:
                        p = re.sub(r'\s+', ' ', html.unescape(b)).strip()
                        if len(p) < 40:
                            continue
                        if any(k in p.lower() for k in NOISE):
                            continue
                        paras.append(p)
                    if paras:
                        return paras
            return None
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
                continue
            print('  BODY_FAIL', url, type(e).__name__)
            return None
    return None


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
        tr_ok = sum(1 for a in data['articles'][:10] for p in a['paras'] if p.get('zh'))
        print('SRC', src['key'], 'added', got, flush=True)
    data['articles'] = data['articles'][:300]
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tot = sum(len(a['paras']) for a in data['articles'])
    tr = sum(1 for a in data['articles'] for p in a['paras'] if p.get('zh'))
    print('OK total_added', added, 'articles', len(data['articles']),
          'translated %d/%d' % (tr, tot), flush=True)


if __name__ == '__main__':
    main()
