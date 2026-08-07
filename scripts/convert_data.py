import io, json, math, re, unicodedata, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.nyc.gov/assets/finance/downloads/tar/fy27_avroll1234.zip'

SURNAMES=set('''SILVA SANTOS OLIVEIRA SOUZA SOUSA PEREIRA COSTA RODRIGUES ALMEIDA LIMA FERREIRA GOMES RIBEIRO MARTINS CARVALHO ROCHA BARBOSA ALVES NUNES MENDES TEIXEIRA DIAS MOREIRA MONTEIRO CARDOSO CAMPOS PINTO FREITAS MORAES MORAIS MACHADO CORREIA BATISTA ANDRADE CUNHA MELO CASTRO ARAUJO AZEVEDO COELHO FONSECA TAVARES REIS RAMOS LOPES MARQUES GONCALVES PIRES VIEIRA FARIA NEVES AGUIAR MOURA PEIXOTO MACEDO QUEIROZ BORGES CABRAL ASSIS AMARAL BRAGA BEZERRA MENEZES GUIMARAES XAVIER LEITE DINIZ PAIVA MOTA MOTTA PRADO SALGADO SA ABREU FIGUEIREDO VARGAS'''.split())
GIVEN=set('''JOAO JOSE MARIA ANA PAULO CARLOS LUIZ LUIS ANTONIO FRANCISCO FERNANDO RICARDO ROBERTO EDUARDO GABRIEL RAFAEL BRUNO FELIPE RODRIGO RENATO MAURICIO ANDRE ALEXANDRE DANIEL MARCOS MARCELO JORGE SERGIO PEDRO MATEUS MATHEUS THIAGO TIAGO GUSTAVO LEONARDO DIEGO FABIO WAGNER WALTER CLAUDIO CESAR HELIO MARIO MARCIO ADRIANO ANDERSON EVERTON EMERSON EDSON AILTON MILTON NELSON REGINALDO ROGERIO VITOR VICTOR VINICIUS CAIO RENAN DOUGLAS DENIS DENNIS JAIR JAIRO GILBERTO OSVALDO ALBERTO ARTHUR ARMANDO LEANDRO LUCAS DAVI DANILO MURILO ADRIANA PATRICIA FERNANDA CAMILA JULIANA LUCIANA CRISTIANE CRISTINA RENATA RAQUEL CARLA PAULA DANIELA MARIANA MARCELA PRISCILA VANESSA LETICIA LARISSA BEATRIZ BRUNA GABRIELA RAFAELA TATIANA SIMONE SANDRA REGINA ROSE ROSA TERESA TEREZA HELENA ISABEL ISABELA ISABELLA LUCIA CLAUDIA ELIANA ELIANE MONICA SONIA SUELI VERA MARTA MARTHA CINTIA DEBORA FLAVIA ANDRESSA AMANDA ALINE NATALIA CAROLINA CAROLINE JESSICA'''.split())
PARTICLES={'DA','DAS','DE','DO','DOS'}
ORG=set('''LLC INC CORP CORPORATION COMPANY CO LTD LP LLP TRUST ASSOCIATES PARTNERS PARTNERSHIP HOLDINGS REALTY PROPERTIES PROPERTY ENTERPRISES GROUP DEVELOPMENT MANAGEMENT CHURCH SCHOOL HOSPITAL UNIVERSITY BANK AUTHORITY DEPARTMENT CITY STATE CONDOMINIUM HOUSING FOUNDATION SOCIETY ASSOCIATION'''.split())
BORO={'1':'Manhattan','2':'Bronx','3':'Brooklyn','4':'Queens','5':'Staten Island'}
COMPOUNDS=[('MARIA','APARECIDA'),('JOAO','BATISTA'),('JOSE','CARLOS'),('JOSE','ANTONIO'),('JOSE','MARIA'),('ANA','PAULA'),('ANA','MARIA'),('MARIA','JOSE'),('LUIZ','CARLOS'),('LUIS','CARLOS'),('CARLOS','EDUARDO'),('PAULO','ROBERTO'),('JOAO','PAULO'),('MARIA','HELENA'),('MARIA','LUCIA')]

def norm(s):
    s=unicodedata.normalize('NFKD',s)
    s=''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9' -]+",' ',s.upper()).strip()

def confidence(owner):
    toks=[t for t in norm(owner).split() if t]
    ts=set(toks)
    if not toks or ts & ORG: return None
    sur=ts & SURNAMES
    if not sur: return None
    giv=ts & GIVEN; par=ts & PARTICLES
    score=2
    if giv: score += 2
    if par: score += 1
    if len(sur)>=2: score += 1
    if len(giv)>=2: score += 1
    if any(a in ts and b in ts for a,b in COMPOUNDS): score += 1
    return 'Alta' if score>=5 else 'Média' if score>=3 else 'Baixa'

print('Baixando base oficial NYC FY27...')
req=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0'})
with urllib.request.urlopen(req,timeout=180) as resp:
    rawzip=resp.read()
print('Download concluido:',len(rawzip),'bytes')

rows=[]
with zipfile.ZipFile(io.BytesIO(rawzip)) as z:
    name=z.namelist()[0]
    with z.open(name) as f:
        for raw in f:
            p=raw.decode('latin1','ignore').rstrip('\r\n').split('\t')
            if len(p)<139: continue
            owner=p[72].strip(); conf=confidence(owner)
            if not conf: continue
            b=p[1].strip(); h1=p[74].strip(); h2=p[75].strip(); street=p[76].strip(); zipcode=p[77].strip()
            house=h1 if not h2 or h2==h1 else f'{h1}-{h2}'
            address=' '.join(x for x in (house,street) if x).strip()
            if not address: continue
            bbl=f'{b}{p[2].strip().zfill(5)}{p[3].strip().zfill(4)}'
            rows.append([owner,conf,BORO.get(b,b),address,zipcode,bbl])

print('Candidatos:',len(rows))
if len(rows)!=5086:
    raise SystemExit(f'ERRO: esperados 5086 registros, obtidos {len(rows)}')

chunk=math.ceil(len(rows)/10)
for i in range(10):
    part=rows[i*chunk:(i+1)*chunk]
    content='window.IMOVEIS_NYC=window.IMOVEIS_NYC||[];\nwindow.IMOVEIS_NYC.push(...'+json.dumps(part,ensure_ascii=False,separators=(',',':'))+');\n'
    (ROOT/f'data{i+1}.js').write_text(content,encoding='utf-8')
    print(f'data{i+1}.js:',len(part),'registros')

idx=ROOT/'index.html'
s=idx.read_text(encoding='utf-8')
# Remove bibliotecas/tags antigas e injeta a base JS direta e os módulos adicionais.
s=re.sub(r'\n?<script src="https://cdn\.jsdelivr\.net/npm/pako@[^\"]+"></script>','',s)
s=re.sub(r'\n?<script src="\./data\d+\.js\?v=\d+"></script>','',s)
s=re.sub(r'\n?<script src="\./receita_matches\.js\?v=\d+"></script>','',s)
s=re.sub(r'\n?<script src="\./enhancements\.js\?v=\d+"></script>','',s)
tags='\n'.join(f'<script src="./data{i}.js?v=9"></script>' for i in range(1,11))
tags += '\n<script src="./receita_matches.js?v=9"></script>\n<script src="./enhancements.js?v=9"></script>'
leaf='<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
s=s.replace(leaf,leaf+'\n'+tags)
s=re.sub(r'\nconst FILES=\[[^\n]+\];','',s)
# Marcadores maiores e com contorno branco para melhor leitura em satélite.
s=s.replace("const m=L.circleMarker([g.lat,g.lon],{radius:5,color:col,weight:1,fillOpacity:.72});","const m=L.circleMarker([g.lat,g.lon],{radius:9,color:'#fff',fillColor:col,weight:2.5,fillOpacity:.9});")
new_load="""async function load(){
 const st=el('loadStatus');
 try{
  if(!window.IMOVEIS_NYC || !Array.isArray(window.IMOVEIS_NYC)) throw new Error('arquivos data*.js nao foram carregados');
  DATA=window.IMOVEIS_NYC;
  if(DATA.length!==5086) throw new Error('esperados 5.086 registros; carregados '+DATA.length);
  el('total').textContent=DATA.length.toLocaleString('pt-BR');
  el('alta').textContent=DATA.filter(r=>r[1]=='Alta').length.toLocaleString('pt-BR');
  el('media').textContent=DATA.filter(r=>r[1]=='Média').length.toLocaleString('pt-BR');
  for(const r of DATA){const g=cacheGet(r);if(g?.lat)addMarker(r,g)}
  el('geo').textContent=markers.size.toLocaleString('pt-BR');
  el('bar').style.width=(markers.size/DATA.length*100)+'%';
  st.innerHTML='<span class=\"ok\">Base carregada com sucesso: '+DATA.length.toLocaleString('pt-BR')+' registros.</span>';
  el('status').textContent='Pronto. Clique em Iniciar / continuar para posicionar os endereços ainda não geocodificados.';
  el('start').disabled=false;
 }catch(e){console.error(e);st.innerHTML='<span class=\"err\">Erro ao carregar os dados: '+esc(e&&e.message?e.message:String(e))+'</span>';el('status').textContent='A base não pôde ser carregada.'}
}"""
s=re.sub(r'async function load\(\)\{.*?\n\}\nasync function geocode',new_load+'\nasync function geocode',s,flags=re.S)
idx.write_text(s,encoding='utf-8')
print('index.html atualizado: JS direto, marcadores maiores e módulo RFB')
