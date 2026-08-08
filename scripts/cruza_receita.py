import csv
import gzip
import io
import json
import re
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_BASE = 'https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/'
OFFICIAL_MONTH = '2026-01'
BRASILIO_URL = 'https://data.brasil.io/dataset/socios-brasil/socios.csv.gz'
BRASILIO_REFERENCE = '2020-09-20'
MAX_ITEMS_PER_NAME = 20
UA = 'Mozilla/5.0 (compatible; imoveisnyc-rfb-crossmatch/1.4)'


def norm_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def read_nyc_names():
    rows = []
    for i in range(1, 11):
        path = ROOT / f'data{i}.js'
        text = path.read_text(encoding='utf-8')
        m = re.search(r'push\(\.\.\.(\[.*\])\);\s*$', text, flags=re.S)
        if not m:
            raise RuntimeError(f'Não foi possível interpretar {path.name}')
        rows.extend(json.loads(m.group(1)))
    owners = defaultdict(set)
    for row in rows:
        if not row:
            continue
        original = str(row[0]).strip()
        key = norm_name(original)
        if key and len(key.split()) >= 2:
            owners[key].add(original)
    print('Registros NYC:', len(rows), '| nomes normalizados únicos:', len(owners), flush=True)
    return rows, owners


def curl_download(url, path, attempts=4, connect_timeout=20, max_time=2400):
    path = Path(path)
    for attempt in range(1, attempts + 1):
        resume = path.exists() and path.stat().st_size > 0
        print(f'Download tentativa {attempt}/{attempts}: {url}', flush=True)
        cmd = [
            'curl', '--fail', '--location', '--progress-bar',
            '--connect-timeout', str(connect_timeout),
            '--max-time', str(max_time),
            '--retry', '2', '--retry-delay', '8', '--retry-all-errors',
            '--user-agent', UA,
        ]
        if resume:
            cmd += ['--continue-at', '-']
        cmd += ['--output', str(path), url]
        proc = subprocess.run(cmd)
        if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
            print(f'Download concluído: {path.name} ({path.stat().st_size:,} bytes)', flush=True)
            return True
        if proc.returncode in (22, 33):
            path.unlink(missing_ok=True)
        if attempt < attempts:
            time.sleep(10 * attempt)
    return False


def official_host_usable():
    """Teste curto para não desperdiçar dezenas de minutos no runner."""
    url = f'{OFFICIAL_BASE}{OFFICIAL_MONTH}/Qualificacoes.zip'
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'q.zip'
        ok = curl_download(url, p, attempts=1, connect_timeout=15, max_time=45)
        return bool(ok and zipfile.is_zipfile(p))


def load_qualifications(tmpdir):
    out = {}
    url = f'{OFFICIAL_BASE}{OFFICIAL_MONTH}/Qualificacoes.zip'
    zp = Path(tmpdir) / 'Qualificacoes.zip'
    if not curl_download(url, zp, attempts=3):
        return out
    try:
        with zipfile.ZipFile(zp) as z:
            member = z.namelist()[0]
            with z.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                for row in reader:
                    if len(row) >= 2:
                        out[row[0].strip()] = row[1].strip()
    except Exception as exc:
        print('Não foi possível ler qualificações:', exc, flush=True)
    return out


def fmt_date(s):
    s = (s or '').strip()
    if re.fullmatch(r'\d{8}', s):
        return f'{s[6:8]}/{s[4:6]}/{s[0:4]}'
    return s


def process_official(owners):
    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)
    with tempfile.TemporaryDirectory() as td:
        qualifications = load_qualifications(td)
        for i in range(10):
            filename = f'Socios{i}.zip'
            url = f'{OFFICIAL_BASE}{OFFICIAL_MONTH}/{filename}'
            zp = Path(td) / filename
            print(f'[{i+1}/10] Baixando {filename}...', flush=True)
            if not curl_download(url, zp, attempts=4):
                raise RuntimeError(f'Falha ao baixar {filename} da Receita Federal')
            with zipfile.ZipFile(zp) as z:
                member = z.namelist()[0]
                with z.open(member) as raw:
                    reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                    for row in reader:
                        if len(row) < 6 or row[1].strip() != '2':
                            continue
                        name_key = norm_name(row[2])
                        if name_key not in owners:
                            continue
                        cnpj_basic = row[0].strip()
                        qual_code = row[4].strip()
                        qual = qualifications.get(qual_code, f'Código {qual_code}' if qual_code else '')
                        entered = fmt_date(row[5])
                        sig = (cnpj_basic, qual, entered)
                        totals[name_key] += 1
                        if sig not in seen[name_key] and len(items[name_key]) < MAX_ITEMS_PER_NAME:
                            seen[name_key].add(sig)
                            items[name_key].append(list(sig))
            zp.unlink(missing_ok=True)
            print(filename, 'processado; matches acumulados:', sum(totals.values()), flush=True)
    return totals, items, {
        'fonte': 'Receita Federal do Brasil - Dados Abertos CNPJ / QSA',
        'referencia': OFFICIAL_MONTH,
        'modo': 'oficial-direto',
    }


def process_brasilio(owners):
    """Fallback quando o host oficial não aceita conexão do GitHub Actions."""
    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)
    with tempfile.TemporaryDirectory() as td:
        gz = Path(td) / 'socios.csv.gz'
        print('Host oficial indisponível no runner. Usando espelho Brasil.IO da base QSA.', flush=True)
        if not curl_download(BRASILIO_URL, gz, attempts=5, connect_timeout=30, max_time=5400):
            raise RuntimeError('Falha também no download do espelho Brasil.IO')
        with gzip.open(gz, 'rt', encoding='utf-8', errors='ignore', newline='') as f:
            reader = csv.DictReader(f)
            print('Colunas Brasil.IO:', reader.fieldnames, flush=True)
            for n, row in enumerate(reader, 1):
                nome = row.get('nome_socio') or row.get('nome_razao_social_socio') or ''
                name_key = norm_name(nome)
                if name_key not in owners:
                    continue
                tipo = (row.get('tipo_socio') or '').strip()
                if tipo and 'FISICA' not in norm_name(tipo):
                    continue
                cnpj = (row.get('cnpj') or '').strip()
                cnpj_basic = re.sub(r'\D', '', cnpj)[:8]
                qual = (row.get('qualificacao_socio') or '').strip()
                sig = (cnpj_basic, qual, '')
                totals[name_key] += 1
                if sig not in seen[name_key] and len(items[name_key]) < MAX_ITEMS_PER_NAME:
                    seen[name_key].add(sig)
                    items[name_key].append(list(sig))
                if n % 5000000 == 0:
                    print(f'{n:,} linhas processadas; matches: {sum(totals.values()):,}', flush=True)
    return totals, items, {
        'fonte': 'Brasil.IO - espelho de dados da Receita Federal do Brasil / QSA',
        'referencia': BRASILIO_REFERENCE,
        'modo': 'espelho-contingencia',
    }


def write_result(rows, owners, totals, items, source):
    matches = {}
    for key in sorted(totals):
        matches[key] = {
            'nome_nyc': sorted(owners[key])[0],
            'total': totals[key],
            'itens': items[key],
        }
    property_matches = sum(1 for row in rows if norm_name(row[0]) in matches)
    aviso = 'Correspondência de nome não confirma identidade, nacionalidade ou que se trate da mesma pessoa. CPF não é publicado por este sistema.'
    if source.get('modo') == 'espelho-contingencia':
        aviso += ' O host oficial da Receita estava inacessível ao GitHub Actions; foi usado um snapshot histórico do Brasil.IO, explicitamente identificado pela data de referência.'
    payload = {
        'meta': {
            **source,
            'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'criterio': 'nome completo normalizado em correspondência exata; somente sócio pessoa física',
            'nomes_nyc_unicos': len(owners),
            'nomes_com_correspondencia': len(matches),
            'imoveis_com_correspondencia': property_matches,
            'limite_detalhes_por_nome': MAX_ITEMS_PER_NAME,
            'aviso': aviso,
        },
        'matches': matches,
    }
    content = 'window.RFB_QSA=' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ';\n'
    (ROOT / 'receita_matches.js').write_text(content, encoding='utf-8')
    print('Gerado receita_matches.js | nomes:', len(matches), '| imóveis:', property_matches, flush=True)


def main():
    rows, owners = read_nyc_names()
    if official_host_usable():
        print('Host oficial acessível. Processando snapshot oficial', OFFICIAL_MONTH, flush=True)
        totals, items, source = process_official(owners)
    else:
        print('Host oficial da Receita não responde ao GitHub Actions. Ativando fallback.', flush=True)
        totals, items, source = process_brasilio(owners)
    write_result(rows, owners, totals, items, source)


if __name__ == '__main__':
    main()
