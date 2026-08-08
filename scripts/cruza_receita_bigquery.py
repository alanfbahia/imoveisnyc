import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
TABLE_ID = 'basedosdados.br_me_cnpj.socios'
MAX_ITEMS_PER_NAME = 20
MAX_BYTES_BILLED = 300 * 1024 ** 3  # trava de segurança: 300 GB por consulta


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

    print('Registros NYC:', len(rows), '| nomes únicos:', len(owners), flush=True)
    return rows, owners


def pick(fields, *candidates):
    for name in candidates:
        if name in fields:
            return name
    return None


def sql_norm(expr):
    # Replica a normalização usada no mapa: remove acentos, pontuação e espaços duplicados.
    return (
        "TRIM(REGEXP_REPLACE("
        "REGEXP_REPLACE("
        f"UPPER(REGEXP_REPLACE(NORMALIZE(CAST({expr} AS STRING), NFD), r'\\p{{M}}', '')), "
        "r'[^A-Z0-9 ]+', ' '), "
        "r'\\s+', ' '))"
    )


def as_ref_string(value):
    if value is None:
        return 'não informado'
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def main():
    rows, owners = read_nyc_rows()
    client = bigquery.Client()
    print('Projeto de cobrança BigQuery:', client.project, flush=True)

    table = client.get_table(TABLE_ID)
    fields = {f.name for f in table.schema}
    print('Tabela:', TABLE_ID, flush=True)
    print('Colunas disponíveis:', ', '.join(sorted(fields)), flush=True)

    name_col = pick(fields, 'nome_socio', 'nome_razao_social_socio', 'nome')
    cnpj_col = pick(fields, 'cnpj_basico', 'cnpj', 'id_cnpj')
    date_col = pick(fields, 'data', 'data_referencia', 'data_extracao')
    qual_col = pick(fields, 'qualificacao_socio', 'qualificacao', 'codigo_qualificacao_socio')
    entry_col = pick(fields, 'data_entrada_sociedade', 'data_entrada', 'data_entrada_socio')
    ident_col = pick(fields, 'identificador_socio', 'identificador_de_socio')
    type_col = pick(fields, 'tipo_socio', 'tipo')

    if not name_col or not cnpj_col:
        raise RuntimeError(
            'Schema inesperado na tabela de sócios. '
            f'nome={name_col!r}, cnpj={cnpj_col!r}'
        )

    latest_ref = None
    if date_col:
        q_latest = f'SELECT MAX(`{date_col}`) AS ref FROM `{TABLE_ID}`'
        cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
        latest_ref = next(iter(client.query(q_latest, job_config=cfg).result())).ref
        print('Snapshot mais recente encontrado:', as_ref_string(latest_ref), flush=True)

    name_expr = sql_norm(f't.`{name_col}`')
    where = [f'{name_expr} IN UNNEST(@names)']
    params = [bigquery.ArrayQueryParameter('names', 'STRING', sorted(owners))]

    if date_col and latest_ref is not None:
        where.append(f't.`{date_col}` = @snapshot')
        # O parâmetro recebe o tipo real retornado pelo BigQuery.
        if isinstance(latest_ref, datetime):
            ptype = 'TIMESTAMP'
        elif isinstance(latest_ref, date):
            ptype = 'DATE'
        else:
            ptype = 'STRING'
        params.append(bigquery.ScalarQueryParameter('snapshot', ptype, latest_ref))

    person_filter = 'não foi possível identificar coluna de tipo do sócio'
    if ident_col:
        where.append(f'SAFE_CAST(t.`{ident_col}` AS INT64) = 2')
        person_filter = f'{ident_col}=2 (pessoa física)'
    elif type_col:
        where.append(
            f"REGEXP_CONTAINS(UPPER(CAST(t.`{type_col}` AS STRING)), r'F[IÍ]SICA|PESSOA F')"
        )
        person_filter = f'filtro textual em {type_col} para pessoa física'

    qual_select = f'CAST(t.`{qual_col}` AS STRING)' if qual_col else "''"
    entry_select = f'CAST(t.`{entry_col}` AS STRING)' if entry_col else "''"

    query = f'''\
SELECT
  {name_expr} AS nome_norm,
  CAST(t.`{name_col}` AS STRING) AS nome_socio,
  CAST(t.`{cnpj_col}` AS STRING) AS cnpj,
  {qual_select} AS qualificacao,
  {entry_select} AS data_entrada
FROM `{TABLE_ID}` AS t
WHERE {' AND '.join(where)}
'''

    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        maximum_bytes_billed=MAX_BYTES_BILLED,
        use_legacy_sql=False,
    )
    print('Executando cruzamento no BigQuery...', flush=True)
    job = client.query(query, job_config=cfg)
    result = job.result()
    print('Bytes processados:', f'{job.total_bytes_processed:,}', flush=True)

    totals = defaultdict(int)
    items = defaultdict(list)
    seen = defaultdict(set)

    for rec in result:
        key = str(rec.nome_norm or '').strip()
        if key not in owners:
            continue
        digits = re.sub(r'\D', '', str(rec.cnpj or ''))
        cnpj_basic = digits[:8] if digits else str(rec.cnpj or '').strip()
        qual = str(rec.qualificacao or '').strip()
        entered = str(rec.data_entrada or '').strip()
        sig = (cnpj_basic, qual, entered)
        totals[key] += 1
        if sig not in seen[key] and len(items[key]) < MAX_ITEMS_PER_NAME:
            seen[key].add(sig)
            items[key].append(list(sig))

    matches = {}
    for key in sorted(totals):
        matches[key] = {
            'nome_nyc': sorted(owners[key])[0],
            'total': totals[key],
            'itens': items[key],
        }

    property_matches = sum(1 for row in rows if norm_name(row[0]) in matches)
    ref = as_ref_string(latest_ref)
    payload = {
        'meta': {
            'fonte': 'Base dos Dados / BigQuery - Quadros Societários CNPJ (fonte original Receita Federal/ME)',
            'referencia': ref,
            'modo': 'bigquery-basedosdados',
            'tabela': TABLE_ID,
            'projeto_cobranca': client.project,
            'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'criterio': 'nome completo normalizado em correspondência exata; ' + person_filter,
            'nomes_nyc_unicos': len(owners),
            'nomes_com_correspondencia': len(matches),
            'imoveis_com_correspondencia': property_matches,
            'limite_detalhes_por_nome': MAX_ITEMS_PER_NAME,
            'bytes_processados': int(job.total_bytes_processed or 0),
            'aviso': (
                'Correspondência de nome não confirma identidade, nacionalidade ou que se trate da mesma pessoa. '
                'CPF não é publicado por este sistema.'
            ),
        },
        'matches': matches,
    }

    out = ROOT / 'receita_matches.js'
    out.write_text(
        'window.RFB_QSA=' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + ';\n',
        encoding='utf-8',
    )
    print(
        'Gerado receita_matches.js | nomes:', len(matches),
        '| imóveis:', property_matches,
        '| referência:', ref,
        flush=True,
    )


if __name__ == '__main__':
    main()
