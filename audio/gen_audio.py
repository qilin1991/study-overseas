# -*- coding: utf-8 -*-
"""GitHub Actions 上运行：把 news_overseas.json 的文章合成 TTS 音频 + 时间轴，
上传到腾讯 COS。支持增量（已存在的跳过）。
环境变量:
  TTS_VOICE  (默认 en-GB-RyanNeural)
  TTS_LIMIT  每次最多生成几篇（默认 12，控制 Actions 时长）
  COS_SECRET_ID / COS_SECRET_KEY / COS_REGION / COS_BUCKET
"""
import asyncio, json, os, sys, re, hashlib

try:
    import edge_tts
except ImportError:
    os.system("pip install edge-tts==6.1.12")
    import edge_tts

try:
    from qcloud_cos import CosConfig, CosS3Client
except ImportError:
    os.system("pip install cos-python-sdk-v5")
    from qcloud_cos import CosConfig, CosS3Client

VOICE = os.environ.get("TTS_VOICE", "en-GB-RyanNeural")
RATE = os.environ.get("TTS_RATE", "-8%")
LIMIT = int(os.environ.get("TTS_LIMIT", "12"))
PREFIX = "static/study_audio"
BUCKET = os.environ.get("COS_BUCKET", "")
REGION = os.environ.get("COS_REGION", "ap-nanjing")

cos = CosS3Client(CosConfig(Region=REGION,
                            SecretId=os.environ.get("COS_SECRET_ID", ""),
                            SecretKey=os.environ.get("COS_SECRET_KEY", "")))


def exists(key):
    try:
        cos.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


async def synth(text):
    c = edge_tts.Communicate(text, VOICE, rate=RATE)
    buf, bounds = bytearray(), []
    async for ch in c.stream():
        if ch["type"] == "audio":
            buf.extend(ch["data"])
        elif ch["type"] == "SentenceBoundary":
            bounds.append((ch["offset"] / 1e7, ch["duration"] / 1e7))
    return bytes(buf), bounds


async def gen(art):
    aid = art["id"]
    mp3_key = f"{PREFIX}/{aid}.mp3"
    if exists(mp3_key):
        return None  # 已生成，跳过
    paras = [p for p in art.get("paras", []) if (p.get("en") or "").strip()]
    if not paras:
        return None
    mp3, segs, off = bytearray(), [], 0.0
    for i, p in enumerate(paras):
        en = re.sub(r"\s+", " ", p["en"]).strip()
        try:
            data, bounds = await synth(en)
        except Exception as e:
            print(f"  [warn] {aid} p{i}: {e}", flush=True)
            continue
        dur = max((b[0] + b[1] for b in bounds), default=len(data) / 4000.0)
        mp3.extend(data)
        segs.append({"i": i, "en": en, "zh": p.get("zh") or "",
                     "start": round(off, 3), "end": round(off + dur, 3)})
        off += dur + 0.35
        await asyncio.sleep(0.12)
    if not segs:
        return None

    cos.put_object(Bucket=BUCKET, Key=mp3_key, Body=bytes(mp3), ContentType="audio/mpeg")
    meta = {"id": aid, "title": art.get("title"), "source_name": art.get("source_name"),
            "cat": art.get("cat"), "date": art.get("date"), "url": art.get("url"),
            "dur": round(off, 1), "segs": segs}
    cos.put_object(Bucket=BUCKET, Key=f"{PREFIX}/{aid}.json",
                   Body=json.dumps(meta, ensure_ascii=False).encode("utf-8"),
                   ContentType="application/json")
    return {"id": aid, "segs": len(segs), "dur": round(off, 1), "kb": round(len(mp3) / 1024)}


async def main():
    src = os.environ.get("NEWS_SRC", "news_overseas.json")
    arts = json.load(open(src, encoding="utf-8"))
    if isinstance(arts, dict):
        arts = arts.get("articles") or arts.get("items") or []
    # 优先中文全、长度适中的
    cand = []
    for a in arts:
        p = a.get("paras") or []
        if len(p) < 5 or len(p) > 45:
            continue
        zh = sum(1 for x in p if (x.get("zh") or "").strip())
        if zh < len(p) * 0.6:
            continue
        cand.append((len(p), a))
    cand.sort(key=lambda x: x[0])
    done = 0
    for _, a in cand:
        if done >= LIMIT:
            break
        r = await gen(a)
        if r:
            done += 1
            print(f"OK {r['id']} {r['segs']}段 {r['dur']}s {r['kb']}KB", flush=True)
    print(f"generated {done} new audio files", flush=True)


asyncio.run(main())
