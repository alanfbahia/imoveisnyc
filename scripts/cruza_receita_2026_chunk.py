import argparse
import csv
import io
import json
import re
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = '2026-01'
MAX_ITEMS_PER_NAME = 20
UA = 'Mozilla/5.0 (compatible; imoveisnyc-rfb-crossmatch/2.0)'
OFFICIAL_BASES = [
    'https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/',
    'https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/',
    'https://arquivos.receitafederal.gov.br/cnpj/dados_abertos_cnpj/',
]


def norm_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def read_nyc_names():
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
    print('Registros NYC:', len(rows), '| nomes únicos:', len(owners), flush=True)
    return owners


def curl_download(url, path, attempts=4):
    path = Path(path)
    for attempt in range(1, attempts + 1):
        path.unlink(missing_ok=True)
        print(f'Tentativa {attempt}/{attempts}: {url}', flush=True)
        cmd = [
            'curl', '--fail', '--location', '--progress-bar',
            '--connect-timeout', '25', '--max-time', '2700',
            '--retry', '3', '--retry-delay', '12', '--retry-all-errors',
            '--speed-time', '240', '--speed-limit', '512',
            '--user-agent', UA,
            '--output', str(path), url,
        ]
        proc = subprocess.run(cmd)
        if proc.returncode == 0 and path.exists() and zipfile.is_zipfile(path):
            print('Download válido:', path.name, f'{path.stat().st_size:,} bytes', flush=True)
            return True
        print('Falha/ZIP inválido; código curl:', proc.returncode, flush=True)
        path.unlink(missing_ok=True)
        if attempt < attempts:
            time.sleep(min(20 * attempt, 60))
    return False


def download_from_any_base(filename, path):
    errors = []
    for base in OFFICIAL_BASES:
        url = f'{base}{REFERENCE}/{filename}'
        print('Testando fonte oficial:', base, flush=True)
        if curl_download(url, path):
            return base
        errors.append(url)
    raise RuntimeError('Não foi possível baixar o arquivo oficial por nenhuma rota: ' + ' | '.join(errors))


def fmt_date(value):
    s = str(value or '').strip()
    if re.fullmatch(r'\d{8}', s):
        return f'{s[6:8]}/{s[4:6]}/{s[0:4]}'
    return s


def process_part(part, owners):
    filename = f'Socios{part}.zip'
    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)

    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / filename
        base = download_from_any_base(filename, zp)
        with zipfile.ZipFile(zp) as z:
            member = z.namelist()[0]
            print('Lendo', member, 'de', filename, flush=True)
            with z.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                for n, row in enumerate(reader, 1):
                    if len(row) < 6 or row[1].strip() != '2':
                        continue
                    name_key = norm_name(row[2])
                    if name_key not in owners:
                        continue
                    cnpj_basic = row[0].strip()
                    qual_code = row[4].strip()
                    entered = fmt_date(row[5])
                    sig = (cnpj_basic, qual_code, entered)
                    totals[name_key] += 1
                    if sig not in seen[name_key] and len(items[name_key]) < MAX_ITEMS_PER_NAME:
                        seen[name_key].add(sig)
                        items[name_key].append([cnpj_basic, qual_code, entered])
                    if n % 5000000 == 0:
                        print(f'{n:,} linhas; matches acumulados: {sum(totals.values()):,}', flush=True)

    result = {
        'part': part,
        'referencia': REFERENCE,
        'fonte': 'Receita Federal do Brasil - Dados Abertos CNPJ / QSA',
        'base_utilizada': base,
        'matches': {
            key: {'total': totals[key], 'itens': items[key]}
            for key in sorted(totals)
        },
    }
    outdir = ROOT / 'rfb_chunks'
    outdir.mkdir(exist_ok=True)
    out = outdir / f'part_{part}.json'
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print('Chunk gerado:', out, '| nomes:', len(totals), '| ocorrências:', sum(totals.values()), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--part', type=int, required=True, choices=range(10))
    args = ap.parse_args()
    owners = read_nyc_names()
    process_part(args.part, owners)


if __name__ == '__main__':
    main()
