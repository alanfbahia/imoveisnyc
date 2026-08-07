import csv
import io
import json
import re
import shutil
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RFB_BASE = 'https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/'
FALLBACK_MONTH = '2026-01'
MAX_ITEMS_PER_NAME = 20
UA = 'Mozilla/5.0 (compatible; imoveisnyc-rfb-crossmatch/1.0)'


def norm_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def fetch_text(url, timeout=90):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'ignore')


def discover_latest_month():
    try:
        html = fetch_text(RFB_BASE)
        months = sorted(set(re.findall(r'href=["\'](20\d{2}-\d{2})/["\']', html)), reverse=True)
        for month in months[:12]:
            try:
                page = fetch_text(f'{RFB_BASE}{month}/')
                if 'Socios0.zip' in page or 'Socios1.zip' in page:
                    return month, page
            except Exception as exc:
                print('Ignorando', month, exc)
    except Exception as exc:
        print('Falha ao descobrir mês mais recente:', exc)
    page = fetch_text(f'{RFB_BASE}{FALLBACK_MONTH}/')
    return FALLBACK_MONTH, page


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
    print('Registros NYC:', len(rows), '| nomes normalizados únicos:', len(owners))
    return rows, owners


def download(url, path):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=240) as src, open(path, 'wb') as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def load_qualifications(month, tmpdir):
    out = {}
    url = f'{RFB_BASE}{month}/Qualificacoes.zip'
    zp = Path(tmpdir) / 'Qualificacoes.zip'
    try:
        download(url, zp)
        with zipfile.ZipFile(zp) as z:
            member = z.namelist()[0]
            with z.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                for row in reader:
                    if len(row) >= 2:
                        out[row[0].strip()] = row[1].strip()
    except Exception as exc:
        print('Não foi possível carregar qualificações:', exc)
    return out


def fmt_date(s):
    s = (s or '').strip()
    if re.fullmatch(r'\d{8}', s):
        return f'{s[6:8]}/{s[4:6]}/{s[0:4]}'
    return s


def process_socios(month, page_html, owners, qualifications):
    filenames = sorted(
        set(re.findall(r'(Socios\d+\.zip)', page_html)),
        key=lambda x: int(re.search(r'\d+', x).group())
    )
    if not filenames:
        filenames = [f'Socios{i}.zip' for i in range(10)]

    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)

    with tempfile.TemporaryDirectory() as td:
        for pos, filename in enumerate(filenames, 1):
            url = f'{RFB_BASE}{month}/{filename}'
            zp = Path(td) / filename
            print(f'[{pos}/{len(filenames)}] Baixando {filename}...')
            try:
                download(url, zp)
                with zipfile.ZipFile(zp) as z:
                    member = z.namelist()[0]
                    with z.open(member) as raw:
                        reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                        for row in reader:
                            # Layout público Sócios: 0 CNPJ básico; 1 identificador; 2 nome;
                            # 3 CPF/CNPJ do sócio; 4 qualificação; 5 data de entrada; ...
                            if len(row) < 6 or row[1].strip() != '2':
                                continue  # somente pessoa física
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
                print(filename, 'processado; matches acumulados:', sum(totals.values()))
            except Exception as exc:
                print('ERRO em', filename, exc)
                raise
            finally:
                try:
                    zp.unlink(missing_ok=True)
                except Exception:
                    pass
    return totals, items


def main():
    rows, owners = read_nyc_names()
    month, page_html = discover_latest_month()
    print('Referência RFB selecionada:', month)

    with tempfile.TemporaryDirectory() as td:
        qualifications = load_qualifications(month, td)

    totals, items = process_socios(month, page_html, owners, qualifications)
    matches = {}
    for key in sorted(totals):
        matches[key] = {
            'nome_nyc': sorted(owners[key])[0],
            'total': totals[key],
            'itens': items[key],
        }

    property_matches = sum(1 for row in rows if norm_name(row[0]) in matches)
    payload = {
        'meta': {
            'fonte': 'Receita Federal do Brasil - Dados Abertos CNPJ / Quadro de Sócios e Administradores (QSA)',
            'referencia': month,
            'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'criterio': 'nome completo normalizado em correspondência exata; somente sócio pessoa física',
            'nomes_nyc_unicos': len(owners),
            'nomes_com_correspondencia': len(matches),
            'imoveis_com_correspondencia': property_matches,
            'limite_detalhes_por_nome': MAX_ITEMS_PER_NAME,
            'aviso': 'Correspondência de nome não confirma identidade, nacionalidade ou que se trate da mesma pessoa. CPF não é publicado por este sistema.'
        },
        'matches': matches,
    }
    content = 'window.RFB_QSA=' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ';\n'
    (ROOT / 'receita_matches.js').write_text(content, encoding='utf-8')
    print('Gerado receita_matches.js | nomes:', len(matches), '| imóveis:', property_matches)


if __name__ == '__main__':
    main()
