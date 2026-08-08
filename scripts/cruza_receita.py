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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RFB_BASE = 'https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/'
FALLBACK_MONTH = '2026-01'
MAX_ITEMS_PER_NAME = 20
UA = 'Mozilla/5.0 (compatible; imoveisnyc-rfb-crossmatch/1.2)'


def norm_name(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def fetch_text(url, attempts=4):
    """Busca páginas pequenas com curl e novas tentativas.

    O host da Receita às vezes demora para aceitar conexão a partir do
    GitHub Actions. Não usamos urllib aqui porque um timeout isolado encerrava
    todo o workflow antes mesmo de começar a baixar os arquivos de sócios.
    """
    for attempt in range(1, attempts + 1):
        print(f'Consultando {url} ({attempt}/{attempts})', flush=True)
        cmd = [
            'curl', '--fail', '--location', '--silent', '--show-error',
            '--connect-timeout', '30', '--max-time', '120',
            '--retry', '2', '--retry-delay', '8', '--retry-all-errors',
            '--user-agent', UA, url,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode('utf-8', 'ignore')
        print(
            f'Falha ao consultar página (curl {proc.returncode}): '
            + proc.stderr.decode('utf-8', 'ignore')[-500:],
            flush=True,
        )
        if attempt < attempts:
            time.sleep(15 * attempt)
    raise RuntimeError(f'Falha ao consultar {url}')


def discover_latest_month():
    """Tenta descobrir a publicação mais nova, mas nunca bloqueia o cruzamento.

    Se a listagem de diretórios da Receita estiver lenta/indisponível,
    seguimos diretamente com uma referência conhecida e existente. O
    processamento dos Socios*.zip não depende da página HTML.
    """
    try:
        html = fetch_text(RFB_BASE, attempts=2)
        months = sorted(set(re.findall(r'href=["\'](20\d{2}-\d{2})/["\']', html)), reverse=True)
        for month in months[:12]:
            try:
                page = fetch_text(f'{RFB_BASE}{month}/', attempts=2)
                if 'Socios0.zip' in page or 'Socios1.zip' in page:
                    return month, page
            except Exception as exc:
                print('Ignorando', month, exc, flush=True)
    except Exception as exc:
        print('Não foi possível descobrir o mês mais recente:', exc, flush=True)

    print(
        'Usando referência de contingência', FALLBACK_MONTH,
        'sem consultar a página do diretório.', flush=True,
    )
    return FALLBACK_MONTH, ''


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


def download(url, path, attempts=6):
    """Download grande com retomada e novas tentativas."""
    path = Path(path)
    for attempt in range(1, attempts + 1):
        resume = path.exists() and path.stat().st_size > 0
        print(
            f'Download tentativa {attempt}/{attempts}: {url}' +
            (f' | retomando de {path.stat().st_size:,} bytes' if resume else ''),
            flush=True,
        )
        cmd = [
            'curl', '--fail', '--location', '--progress-bar',
            '--connect-timeout', '30',
            '--max-time', '2400',
            '--speed-time', '180', '--speed-limit', '1024',
            '--retry', '3', '--retry-delay', '10', '--retry-all-errors',
            '--user-agent', UA,
        ]
        if resume:
            cmd += ['--continue-at', '-']
        cmd += ['--output', str(path), url]

        proc = subprocess.run(cmd)
        if proc.returncode == 0:
            if path.suffix.lower() != '.zip' or zipfile.is_zipfile(path):
                print(f'Download concluído: {path.name} ({path.stat().st_size:,} bytes)', flush=True)
                return
            print(f'Arquivo inválido após download: {path.name}; reiniciando.', flush=True)
            path.unlink(missing_ok=True)
        else:
            print(f'curl retornou código {proc.returncode}.', flush=True)
            if proc.returncode == 33:
                path.unlink(missing_ok=True)

        if attempt < attempts:
            wait = min(20 * attempt, 90)
            print(f'Aguardando {wait}s antes de nova tentativa...', flush=True)
            time.sleep(wait)

    raise RuntimeError(f'Falha ao baixar {url} após {attempts} tentativas')


def load_qualifications(month, tmpdir):
    out = {}
    url = f'{RFB_BASE}{month}/Qualificacoes.zip'
    zp = Path(tmpdir) / 'Qualificacoes.zip'
    try:
        download(url, zp, attempts=4)
        with zipfile.ZipFile(zp) as z:
            member = z.namelist()[0]
            with z.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding='latin1', errors='ignore'), delimiter=';')
                for row in reader:
                    if len(row) >= 2:
                        out[row[0].strip()] = row[1].strip()
    except Exception as exc:
        print('Não foi possível carregar qualificações:', exc, flush=True)
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
        print('Usando lista padrão Socios0.zip ... Socios9.zip', flush=True)

    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)

    with tempfile.TemporaryDirectory() as td:
        for pos, filename in enumerate(filenames, 1):
            url = f'{RFB_BASE}{month}/{filename}'
            zp = Path(td) / filename
            print(f'[{pos}/{len(filenames)}] Baixando {filename}...', flush=True)
            try:
                download(url, zp)
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
                print(filename, 'processado; matches acumulados:', sum(totals.values()), flush=True)
            except Exception as exc:
                print('ERRO em', filename, exc, flush=True)
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
    print('Referência RFB selecionada:', month, flush=True)

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
    print('Gerado receita_matches.js | nomes:', len(matches), '| imóveis:', property_matches, flush=True)


if __name__ == '__main__':
    main()
