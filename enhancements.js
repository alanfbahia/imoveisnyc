(function(){
'use strict';

function rfbNorm(value){
  return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase().replace(/[^A-Z0-9 ]+/g,' ').replace(/\s+/g,' ').trim();
}

function init(){
  if(typeof DATA==='undefined'||typeof markers==='undefined'||typeof map==='undefined') return;
  const db=window.RFB_QSA||{meta:null,matches:{}};
  const matches=db.matches||{};
  const infoFor=r=>matches[rfbNorm(r[0])]||null;

  const controls=[...document.querySelectorAll('#side .control')];
  const searchControl=controls.find(c=>c.querySelector('#q'))||controls[controls.length-1];
  const box=document.createElement('div');
  box.className='control';
  box.innerHTML='<b>Receita Federal — CNPJ/QSA</b>'+
    '<div id="rfbStatus" class="sub">Carregando cruzamento…</div>'+
    '<label class="chk"><input id="rfbOnly" type="checkbox"> Somente nomes com possível correspondência no QSA</label>'+
    '<label class="chk"><input id="rfbHighlight" type="checkbox" checked> Destacar possíveis correspondências</label>'+
    '<div class="note" style="margin-top:7px">O cruzamento usa apenas dados abertos do CNPJ/QSA e correspondência exata do nome normalizado. <b>Não confirma identidade nem nacionalidade.</b> O sistema não publica CPF.</div>';
  searchControl.parentNode.insertBefore(box,searchControl);

  const meta=db.meta;
  const matchedNames=Object.keys(matches).length;
  const propertyCount=DATA.filter(r=>!!infoFor(r)).length;
  const status=document.getElementById('rfbStatus');
  if(meta){
    status.innerHTML='<span class="ok"><b>'+propertyCount.toLocaleString('pt-BR')+'</b> imóveis / <b>'+matchedNames.toLocaleString('pt-BR')+'</b> nomes com possível correspondência. Base RFB: '+String(meta.referencia||'').replace('-', '/')+'.</span>';
  }else{
    status.innerHTML='<span class="sub">Cruzamento ainda não processado. O workflow da Receita Federal está sendo preparado.</span>';
  }

  const originalShouldShow=shouldShow;
  shouldShow=function(r){
    if(!originalShouldShow(r)) return false;
    const only=document.getElementById('rfbOnly');
    return !(only&&only.checked&&!infoFor(r));
  };

  function popupRfb(r){
    const info=infoFor(r);
    const ref=meta&&meta.referencia?String(meta.referencia).replace('-', '/'):'—';
    if(!info){
      return '<hr><b>Receita Federal — CNPJ/QSA</b><br><span style="font-size:11px">Nenhuma correspondência exata de nome na base processada ('+esc(ref)+').</span>';
    }
    const rows=(info.itens||[]).map(x=>'<div style="margin-top:4px">• Raiz CNPJ: <b>'+esc(x[0])+'</b>'+(x[1]?' — '+esc(x[1]):'')+(x[2]?' — entrada '+esc(x[2]):'')+'</div>').join('');
    const more=Number(info.total||0)>(info.itens||[]).length?'<div style="margin-top:4px">+ '+(Number(info.total)-(info.itens||[]).length).toLocaleString('pt-BR')+' outro(s) registro(s) com o mesmo nome.</div>':'';
    return '<hr><b>Receita Federal — CNPJ/QSA</b><br><b>Possível correspondência por nome:</b> '+Number(info.total||0).toLocaleString('pt-BR')+' registro(s).'+rows+more+'<div style="font-size:10px;margin-top:6px;color:#687386">Correspondência nominal não confirma que seja a mesma pessoa. CPF não é exibido.</div>';
  }

  function buildPopup(r){
    return '<div style="min-width:270px"><b>'+esc(r[0])+'</b><br>'+esc(r[3])+', '+esc(r[2])+' NY '+esc(r[4])+'<hr><b>Compatibilidade:</b> '+esc(r[1])+'<br><b>Camadas:</b> '+esc(groupsFor(r).join(', '))+'<br><b>BBL:</b> '+esc(r[5])+popupRfb(r)+'</div>';
  }

  function markerStyle(r){
    const col=r[1]=='Alta'?'#1b7f3a':r[1]=='Média'?'#d18b00':'#6b7280';
    const highlight=document.getElementById('rfbHighlight');
    const hit=!!infoFor(r)&&(!highlight||highlight.checked);
    return {radius:hit?12:9,color:'#fff',fillColor:col,weight:hit?3.5:2.5,fillOpacity:.92};
  }

  addMarker=function(r,g){
    if(!g?.lat||!g?.lon||markers.has(key(r))) return;
    const st=markerStyle(r);
    const m=L.circleMarker([g.lat,g.lon],{radius:st.radius,color:st.color,fillColor:st.fillColor,weight:st.weight,fillOpacity:st.fillOpacity});
    m._r=r;
    m.bindPopup(buildPopup(r));
    markers.set(key(r),m);
    if(shouldShow(r)) m.addTo(map);
    if(el('labels').checked&&shouldShow(r)) m.bindTooltip(r[0],{permanent:true,direction:'top',className:'label-name'});
  };

  function restyle(){
    for(const m of markers.values()){
      const st=markerStyle(m._r);
      m.setStyle({color:st.color,fillColor:st.fillColor,weight:st.weight,fillOpacity:st.fillOpacity});
      m.setRadius(st.radius);
      m.setPopupContent(buildPopup(m._r));
    }
    applyFilters();
  }

  document.getElementById('rfbOnly').onchange=applyFilters;
  document.getElementById('rfbHighlight').onchange=restyle;

  const originalSearch=doSearch;
  doSearch=function(){
    const x=norm(el('q').value.trim()),b=el('boro').value;
    const a=DATA.filter(r=>(!b||r[2]===b)&&(!x||norm(r[0]+' '+r[3]+' '+r[4]+' '+r[5]).includes(x))).slice(0,80);
    el('results').innerHTML=a.map(r=>{
      const info=infoFor(r);
      const badge=info?' <span style="font-weight:700;color:#147a36">RFB '+Number(info.total||0).toLocaleString('pt-BR')+'</span>':'';
      return '<div class="result" data-k="'+esc(key(r))+'"><b>'+esc(r[0])+'</b>'+badge+'<br>'+esc(r[3])+', '+esc(r[2])+' '+esc(r[4])+' — '+esc(groupsFor(r).join(', '))+'</div>';
    }).join('');
    el('results').querySelectorAll('.result').forEach(n=>n.onclick=()=>{const m=markers.get(n.dataset.k);m?(map.setView(m.getLatLng(),17),m.openPopup()):alert('Este endereço ainda não foi geocodificado. Clique em Iniciar / continuar.')});
  };
  el('q').oninput=doSearch;
  el('boro').onchange=doSearch;

  restyle();
}

if(document.readyState==='complete') setTimeout(init,0);
else window.addEventListener('load',init);
})();
