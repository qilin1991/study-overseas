# -*- coding: utf-8 -*-
"""可行性探测：用 GitHub 海外 runner 实测能否抓到「国内 VPS 抓不到」的源站头图。
只读取 pending_imgs.json（VPS 导出的待抓队列），把结果写到 pending_imgs_test.json，
由 workflow 负责 git push 回仓库。不接触任何 COS 密钥，public repo 安全。
"""
import json, os, time, requests, re
from urllib.parse import urljoin

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120 Safari/537.36'}


def og_imgs(html, base):
    imgs = []
    pats = [
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
        r'<meta[^>]+name="twitter:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+property="twitter:image"[^>]+content="([^"]+)"',
    ]
    for pat in pats:
        m = re.search(pat, html, re.I)
        if m:
            imgs.append(m.group(1))
            break
    # 兜底取首个正文 <img>
    for m in re.finditer(r'<img\b[^>]*>', html, re.I):
        sm = re.search(r'src="([^"]+)"', m.group(0), re.I)
        if sm and not sm.group(1).startswith('data:'):
            imgs.append(sm.group(1))
            break
    out, seen = [], set()
    for u in imgs:
        a = urljoin(base, u)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:4]


def main():
    data = json.load(open(os.path.join(HERE, 'pending_imgs.json'), encoding='utf-8'))
    items = data.get('items', [])
    results = []
    for it in items:
        url, aid = it.get('url'), it.get('id')
        r = {'id': aid, 'src_url': url}
        try:
            resp = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
            r['page_status'] = resp.status_code
            cands = og_imgs(resp.text, url)
            r['og_candidates'] = cands
            if cands:
                try:
                    ir = requests.get(cands[0], headers=UA, timeout=25, allow_redirects=True)
                    r['img_status'] = ir.status_code
                    r['img_type'] = ir.headers.get('content-type')
                    r['img_bytes'] = len(ir.content)
                    r['img_ok'] = (ir.status_code == 200
                                   and (ir.headers.get('content-type') or '').startswith('image')
                                   and len(ir.content) > 2000)
                except Exception as e:
                    r['img_ok'] = False
                    r['img_err'] = str(e)[:120]
            else:
                r['img_ok'] = False
                r['img_err'] = 'no og/img found'
        except Exception as e:
            r['page_status'] = None
            r['img_ok'] = False
            r['img_err'] = str(e)[:120]
        results.append(r)
        print('  %s -> page=%s img_ok=%s bytes=%s' %
              (aid, r.get('page_status'), r.get('img_ok'), r.get('img_bytes')), flush=True)
    out = {'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'results': results}
    json.dump(out, open(os.path.join(HERE, 'pending_imgs_test.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('WROTE pending_imgs_test.json with %d results' % len(results))


if __name__ == '__main__':
    main()
