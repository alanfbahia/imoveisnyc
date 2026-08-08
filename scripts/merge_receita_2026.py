import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = '2026-01'
MAX_ITEMS_PER_NAME = 20


def norm_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def read_nyc_rows():
    rows = []
    for i in range(1, 11):
        text = (ROOT / f'data{i}.js').read_text(encoding='utf-8')
        m = re.search(r'push\(\.\.\.(\[.*\])\);\s*$', text, flags=re.S)
        if not m:
            raise RuntimeError(f'Não foi possível interpretar data{i}.js')
        rows.extend(json.loads(m.group(1)))
    owners = defaultdict(set)
    for row in rows:
        if not row:
            continue
        original = str(row[0]).strip()
        key = norm_name(original)
        if key and len(key.split()) >= 2:
            owners[key].add(original)
    return rows, owners


def merge_chunks():
    chunk_dir = ROOT / 'rfb_chunks'
    expected = [chunk_dir / f'part_{i}.json' for i in range(10)]
    missing = [p.name for p in expected if not p.exists()]
    if missing:
        raise RuntimeError('Faltam chunks oficiais de 2026: ' + ', '.join(missing))

    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)
    bases = set()

    for p in expected:
        obj = json.loads(p.read_text(encoding='utf-8'))
        if obj.get('referencia') != REFERENCE:
            raise RuntimeError(f'Referência inesperada em {p.name}: {obj.get("referencia")}')
        bases.add(obj.get('base_utilizada', ''))
        for name, info in (obj.get('matches') or {}).items():
            totals[name] += int(info.get('total', 0))
            for item in info.get('itens') or []:
                sig = tuple(item)
                if sig not in seen[name] and len(items[name]) < MAX_ITEMS_PER_NAME:
                    seen[name].add(sig)
                    items[name].append(list(item))

    return totals, items, sorted(x for x in bases if x)


def main():
    rows, owners = read_nyc_rows()
    totals, items, bases = merge_chunks()

    matches = {}
    for key in sorted(totals):
        matches[key] = {
            'nome_nyc': sorted(owners.get(key, {key}))[0],
            'total': totals[key],
            'itens': [
                [cnpj, (f'Código {qual}' if qual else ''), entered]
                for cnpj, qual, entered in items[key]
            ],
        }

    property_matches = sum(1 for row in rows if norm_name(row[0]) in matches)
    payload = {
        'meta': {
            'fonte': 'Receita Federal do Brasil - Dados Abertos CNPJ / Quadro de Sócios e Administradores (QSA)',
            'referencia': REFERENCE,
            'modo': 'oficial-2026-processado-em-partes',
            'rotas_oficiais_utilizadas': bases,
            'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'criterio': 'nome completo normalizado em correspondência exata; somente sócio pessoa física',
            'nomes_nyc_unicos': len(owners),
            'nomes_com_correspondencia': len(matches),
            'imoveis_com_correspondencia': property_matches,
            'limite_detalhes_por_nome': MAX_ITEMS_PER_NAME,
            'aviso': 'Correspondência nominal não confirma identidade, nacionalidade ou que se trate da mesma pessoa. CPF não é publicado por este sistema.',
        },
        'matches': matches,
    }

    out = ROOT / 'receita_matches.js'
    out.write_text('window.RFB_QSA=' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ';\n', encoding='utf-8')
    print('Resultado oficial 2026 gerado | nomes:', len(matches), '| imóveis:', property_matches)


if __name__ == '__main__':
    main()
