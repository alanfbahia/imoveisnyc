import base64, gzip, json, math, re
from pathlib import Path

# trigger-v2: força execução do workflow após sua criação
ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT/'data2'/f'c_{i:02d}.txt' for i in range(4)] + [ROOT/'data2'/f'r_{i:02d}.txt' for i in range(6)]

def decode_one(path):
    txt = ''.join(path.read_text(encoding='utf-8').split())
    raw = base64.b64decode(txt)
    dec = gzip.decompress(raw)
    return json.loads(dec.decode('utf-8'))

rows = []
errors = []
for p in FILES:
    try:
        part = decode_one(p)
        if not isinstance(part, list):
            raise ValueError('conteudo nao e lista')
        rows.extend(part)
        print(f'{p.name}: {len(part)} registros')
    except Exception as e:
        errors.append((p, e))
        print(f'{p.name}: falhou individualmente: {e}')

if errors or len(rows) != 5086:
    print(f'Leitura individual resultou em {len(rows)} registros; tentando formatos alternativos...')
    candidates = [FILES, FILES[:4], FILES[4:]]
    recovered = []
    for group in candidates:
        try:
            txt = ''.join(''.join(p.read_text(encoding='utf-8').split()) for p in group)
            raw = base64.b64decode(txt)
            dec = gzip.decompress(raw)
            obj = json.loads(dec.decode('utf-8'))
            if isinstance(obj, list):
                recovered.extend(obj)
                print('grupo recuperado:', [p.name for p in group], len(obj))
        except Exception as e:
            print('grupo nao aplicavel:', [p.name for p in group], e)
    if len(recovered) == 5086:
        rows = recovered

if len(rows) != 5086:
    raise SystemExit(f'ERRO: esperados 5086 registros, obtidos {len(rows)}')

seen = set(); clean = []
for r in rows:
    k = json.dumps(r, ensure_ascii=False, separators=(',', ':'))
    if k not in seen:
        seen.add(k); clean.append(r)
rows = clean
if len(rows) != 5086:
    raise SystemExit(f'ERRO apos deduplicacao: esperados 5086, obtidos {len(rows)}')

chunk = math.ceil(len(rows)/10)
for i in range(10):
    part = rows[i*chunk:(i+1)*chunk]
    content = 'window.IMOVEIS_NYC=window.IMOVEIS_NYC||[];\nwindow.IMOVEIS_NYC.push(...' + json.dumps(part, ensure_ascii=False, separators=(',', ':')) + ');\n'
    (ROOT/f'data{i+1}.js').write_text(content, encoding='utf-8')
    print(f'data{i+1}.js: {len(part)} registros')

idx = ROOT/'index.html'
s = idx.read_text(encoding='utf-8')
script_tags = '\n'.join(f'<script src="./data{i}.js?v=7"></script>' for i in range(1,11))
s = re.sub(r'<script src="https://cdn\.jsdelivr\.net/npm/pako@[^\"]+"></script>', script_tags, s)
s = re.sub(r'\nconst FILES=\[[^\n]+\];', '', s)
new_load = '''async function load(){\n const st=el('loadStatus');\n try{\n  if(!window.IMOVEIS_NYC || !Array.isArray(window.IMOVEIS_NYC)) throw new Error('arquivos data*.js nao foram carregados');\n  DATA=window.IMOVEIS_NYC;\n  if(DATA.length!==5086) throw new Error('esperados 5.086 registros; carregados '+DATA.length);\n  el('total').textContent=DATA.length.toLocaleString('pt-BR');\n  el('alta').textContent=DATA.filter(r=>r[1]=='Alta').length.toLocaleString('pt-BR');\n  el('media').textContent=DATA.filter(r=>r[1]=='Média').length.toLocaleString('pt-BR');\n  for(const r of DATA){const g=cacheGet(r);if(g?.lat)addMarker(r,g)}\n  el('geo').textContent=markers.size.toLocaleString('pt-BR');\n  el('bar').style.width=(markers.size/DATA.length*100)+'%';\n  st.innerHTML='<span class="ok">Base carregada com sucesso: '+DATA.length.toLocaleString('pt-BR')+' registros.</span>';\n  el('status').textContent='Pronto. Clique em Iniciar / continuar para posicionar os endereços ainda não geocodificados.';\n  el('start').disabled=false;\n }catch(e){\n  console.error('Falha no carregamento da base',e);\n  st.innerHTML='<span class="err">Erro ao carregar os dados: '+esc(e && e.message ? e.message : String(e))+'</span>';\n  el('status').textContent='A base não pôde ser carregada.';\n }\n}'''
s = re.sub(r'async function load\(\)\{.*?\n\}\nasync function geocode', new_load+'\nasync function geocode', s, flags=re.S)
idx.write_text(s, encoding='utf-8')
print('index.html atualizado para carregamento JS direto')
