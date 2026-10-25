# scripts/archive_search_index.py
"""Index the daily archive for meaning-based search on the /archive page.

Run by .github/workflows/build-signals.yml right after
scripts/archive_snapshot.py. For every archived day without one, writes
    public/search/YYYY-MM-DD.json
holding the stories that first appeared that day (a story already indexed on
an earlier day, matched by its link, is not repeated) and one sentence
embedding per story, so each file is written once and never rewritten.

The embeddings come from Xenova/all-MiniLM-L6-v2, the same quantized model
the /archive page runs in the reader's browser (transformers.js) to embed
their query, so both sides share one vector space. To keep storage minimal
each 384-number vector is kept as its 384 signs: 48 bytes a story, about
6 KB a day. The page scores every story against the full query vector,
then re-embeds the best few dozen titles on the device to rank them exactly.

Needs onnxruntime and tokenizers (requirements-search.txt); the model files
are fetched from Hugging Face once and checked against known hashes.
SIGNAL_MODEL_DIR points at an existing copy instead.
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.archive_snapshot import ARCHIVE_DIR, archived_dates

SEARCH_DIR = ARCHIVE_DIR.parent / 'search'
MODEL_ID = 'Xenova/all-MiniLM-L6-v2'
# The exact files the browser loads; a changed file on the hub fails loudly
# rather than quietly drifting away from the page's model.
MODEL_FILES = {
    'onnx/model_quantized.onnx': 'afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1',
    'tokenizer.json': 'da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0',
}
MODEL_CACHE = Path(os.environ.get('SIGNAL_MODEL_CACHE', Path.home() / '.cache' / 'signal-search-model'))
DIMS = 384


def stories(day_data):
    """[title, where, url, extra] for every story the day's page shows, once each.

    where locates the story in that day's snapshot ("global:3" is the fourth
    Global Affairs article, "_movers:0" the first People Mover), so the index
    need not repeat its link or source: the page reads those from the
    snapshot for the few days a search actually shows.
    """
    out, seen = [], set()

    def add(title, url, where, extra=''):
        title = ' '.join(str(title or '').split())
        url = str(url or '').strip()
        if not title or not url.startswith(('http://', 'https://')) or url in seen:
            return
        seen.add(url)
        out.append([title, where, url, extra])

    for key, engine in day_data.items():
        if key.startswith('_') or not isinstance(engine, dict):
            continue
        for i, a in enumerate(engine.get('articles') or []):
            add(a.get('title'), a.get('url'), f'{key}:{i}')
    if day_data.get('_features'):
        for i, f in enumerate(day_data['_features']):
            add(f.get('title'), f.get('url'), f'_features:{i}', f.get('summary') or '')
    elif day_data.get('_featured'):
        f = day_data['_featured']
        add(f.get('title'), f.get('url'), '_featured:0', f.get('summary') or '')
    for key in ('_movers', '_pulse'):
        for i, m in enumerate(day_data.get(key) or []):
            add(m.get('title'), m.get('url'), f'{key}:{i}')
    for i, q in enumerate((day_data.get('_record') or {}).get('quotes') or []):
        who = ', '.join(x for x in (q.get('leader'), q.get('company')) if x)
        add(f'“{q.get("quote")}” — {who}' if q.get('quote') else '', q.get('url'),
            f'_record:{i}')
    return out


def embed_text(story):
    """What is embedded: the headline, plus the summary where the page shows one."""
    title, _, _, extra = story
    return f'{title}. {extra}' if extra else title


def pack_signs(vectors):
    """Each vector's signs as bits, 48 bytes a vector, base64 (first value = top bit)."""
    out = bytearray()
    for v in vectors:
        v = list(v)
        for i in range(0, DIMS, 8):
            out.append(sum(128 >> j for j in range(8) if v[i + j] > 0))
    return base64.b64encode(bytes(out)).decode('ascii')


def model_dir():
    """A directory holding the model files, fetched and verified if need be."""
    if os.environ.get('SIGNAL_MODEL_DIR'):
        return Path(os.environ['SIGNAL_MODEL_DIR'])
    for name, digest in MODEL_FILES.items():
        path = MODEL_CACHE / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f'https://huggingface.co/{MODEL_ID}/resolve/main/{name}'
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError(f'{name} from the hub does not match the expected model')
            path.write_bytes(data)
    return MODEL_CACHE


class Embedder:
    """Mean-pooled, normalized MiniLM sentence embeddings, as transformers.js makes them."""

    def __init__(self, directory):
        import onnxruntime
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tok.enable_truncation(256)
        self.tok.enable_padding()
        self.session = onnxruntime.InferenceSession(str(directory / 'onnx' / 'model_quantized.onnx'),
                                                    providers=['CPUExecutionProvider'])

    def __call__(self, texts):
        import numpy as np
        rows = []
        for i in range(0, len(texts), 64):
            enc = self.tok.encode_batch(texts[i:i + 64])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {'input_ids': ids, 'attention_mask': mask}
            if any(x.name == 'token_type_ids' for x in self.session.get_inputs()):
                feeds['token_type_ids'] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]
            pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
            rows.append(pooled / np.linalg.norm(pooled, axis=1, keepdims=True))
        return np.concatenate(rows) if rows else np.zeros((0, DIMS), dtype=np.float32)


def build(embed=None, archive_dir=ARCHIVE_DIR, search_dir=SEARCH_DIR):
    """Write the missing day files, oldest first. Returns the days written."""
    seen, written = set(), []
    for day in archived_dates(archive_dir):
        out = search_dir / f'{day}.json'
        data = json.loads((archive_dir / f'{day}.json').read_text(encoding='utf-8'))
        day_stories = stories(data)
        if out.exists():
            indexed = {s[1] for s in json.loads(out.read_text(encoding='utf-8'))['s']}
            seen.update(s[2] for s in day_stories if s[1] in indexed)
            continue
        new = [s for s in day_stories if s[2] not in seen]
        if embed is None:
            embed = Embedder(model_dir())
        vectors = embed([embed_text(s) for s in new])
        search_dir.mkdir(parents=True, exist_ok=True)
        # Headline and place only: the summary just shaped the vector.
        out.write_text(json.dumps({'m': MODEL_ID, 's': [s[:2] for s in new], 'v': pack_signs(vectors)},
                                  ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        seen.update(s[2] for s in new)
        written.append(day)
    return written


if __name__ == '__main__':
    days = build()
    print(f'Indexed {len(days)} day(s) for search.' if days else 'Search index up to date.')
