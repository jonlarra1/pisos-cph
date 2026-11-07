---
name: boligportal-mensajes
description: Lee la bandeja de mensajes de Boligportal (la cuenta compartida, sesión ya iniciada en el Chrome de Jon) para saber con qué pisos hay conversación, si el casero ha contestado, cuáles están alquilados, y cruzarlo con el mapa (base de la Pi). Úsala cuando Jon pida «revisa Boligportal», «¿nos han contestado?», «qué pisos hemos contactado», o antes de marcar pisos como contactados.
---

# Leer los mensajes de Boligportal (solo lectura)

Boligportal no tiene API pública. Se lee la bandeja con la extensión Claude in Chrome usando la sesión que Jon ya tiene
iniciada. Tarda ~2 min para ~30 conversaciones.

## Reglas (no negociables)
- **Solo leer.** Nunca escribir en el chat, ni pulsar «Arkivér samtale», «Send», ni nada que cambie la cuenta.
- **Nunca** pedir, escribir ni guardar la contraseña, ni copiar cookies o tokens a ningún sitio (ni a la Pi, ni a
  archivos, ni al chat). Si la sesión no está iniciada, pedir a Jon que la inicie él.
- No cambiar estados en el mapa sin enseñarle antes la lista a Jon (lo pidió así, 8-nov).
- Cerrar la pestaña al acabar.

## Pasos

1. Cargar las herramientas de Chrome en UNA llamada (ToolSearch `select:` con tabs_context_mcp, tabs_create_mcp,
   navigate, computer, javascript_tool, tabs_close_mcp) y llamar a `tabs_context_mcp` (createIfEmpty).
2. `navigate` a `https://www.boligportal.dk/indbakke/`. Captura a escala 0.5: si sale «Log ind», parar y pedir a Jon
   que inicie sesión.
3. La lista vive en un iframe (`/inbox-iframe/indbakke/`). Cargarla entera (se carga al bajar) y recorrer cada
   conversación sacando el id del anuncio de su enlace «Se bolig» (está en el iframe anidado de la conversación).
   Hacerlo en tandas de 10 para no pasar el tiempo máximo de la llamada:

```js
const d=document.querySelector('iframe').contentDocument;
const sc=[...d.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+50&&getComputedStyle(e).overflowY!='visible')
  .sort((a,b)=>b.scrollHeight-a.scrollHeight)[0];
let prev=-1; for(let i=0;i<40;i++){const n=d.querySelectorAll('a[href*="/indbakke/t/"]').length; if(n===prev&&i>3)break;
  prev=n; sc.scrollTop=sc.scrollHeight; await new Promise(r=>setTimeout(r,900));}
const items=[...d.querySelectorAll('a[href*="/indbakke/t/"]')];
const docs=()=>{const all=[];const w=dd=>{all.push(dd);dd.querySelectorAll('iframe').forEach(f=>{try{f.contentDocument&&w(f.contentDocument)}catch(e){}})};w(document);return all};
window.__bp=window.__bp||[]; let tanda=0;
for(const a of items){ const t=a.getAttribute('href').match(/t\/(\d+)/)[1]; if(window.__bp.some(z=>z.t===t))continue;
  if(++tanda>10) break;   // tanda de 10: volver a lanzar el mismo código hasta que n === total
  const prev=a.innerText.replace(/\s+/g,' ').trim(); a.click(); let ad=null;
  for(let k=0;k<12&&!ad;k++){ await new Promise(r=>setTimeout(r,400));
    const l=docs().flatMap(dd=>[...dd.querySelectorAll('a')]).find(x=>/se bolig/i.test(x.innerText)&&/id-\d+/.test(x.href));
    if(l&&!window.__bp.some(z=>z.ad===l.href.match(/id-(\d+)/)[1])) ad=l.href.match(/id-(\d+)/)[1]; }
  window.__bp.push({t,ad,prev:prev.slice(0,220)}); }
({n:window.__bp.length, total:items.length})
```

   (`ad` se compara con los ya vistos porque el enlace «Se bolig» de la conversación anterior tarda en cambiar.)

4. Clasificar por el último mensaje (lo que enseña la lista) y devolver poco texto cada vez:

```js
window.__fmt=x=>{const p=x.prev; const m=p.match(/^(.*?), (\d{4}) [^,]+, ([^-]+?)(?: - [^ ]+ sal)? ((?:man|tir|ons|tor|fre|lør|søn)\.|\d{1,2} [a-z]{3}\.) (.*)$/i);
 const msg=m?m[5]:p; const tipo=/Kære udlejer,? ?Vi er tre gode venner/i.test(msg)?'SIN RESP'
  :/Lejemålet er udlejet|samtalen er deaktiveret/i.test(msg)?'ALQUILADO':/hentede telefonnummer/i.test(msg)?'TELÉFONO':'¿RESP?';
 return `${x.ad}|${tipo}|${m?m[1]+', '+m[3].trim():p.slice(0,40)}|${m?m[4]:''}`+(tipo==='¿RESP?'?'|'+msg.slice(0,60):'')};
window.__bp.slice(0,10).map(window.__fmt).join('\n')   // luego slice(10,20), slice(20)…
```

   - «Kære udlejer, Vi er tre gode venner…» = nuestra plantilla → sin respuesta.
   - «Hej igen, Jeg skriver blot for at følge op…» y «Vi vil meget gerne se den…» también son NUESTROS (seguimiento):
     contarlos como sin respuesta.
   - «Lejemålet er udlejet» → alquilado. «hentede telefonnummer» → solo pidieron el teléfono.
   - Lo demás suele ser el casero (fremvisning, «Tak for din henvendelse», «Tak for besøget»): destacar visitas con fecha.
   - Si el último mensaje no tiene texto (adjunto), decir que hay que abrirla a mano.
5. Sacar los ids en una línea (`window.__bp.map(x=>x.ad).join(',')`) y cruzarlos con la Pi (lecturas públicas):

```bash
A=https://raspberrypi.tail9f1069.ts.net:8443; IDS="<ids separados por comas>"
curl -s "$A/api/v2/anuncios" > /tmp/base.json; curl -s "$A/api/estados" > /tmp/est.json
python3 - "$IDS" <<'EOF'
import json,sys
base={a["pid"]:a for a in json.load(open("/tmp/base.json"))["anuncios"]}; est=json.load(open("/tmp/est.json"))
for i in sys.argv[1].split(","):
    b=base.get(i); print(i,"| base:",b["estado"] if b else "-","| web:",(est.get(i) or {}).get("st","-"))
EOF
```
   (Usar la carpeta scratchpad de la sesión en vez de /tmp.)
6. Cerrar la pestaña (`tabs_close_mcp`).

## Qué contar a Jon
Primero lo urgente (visitas con fecha, pisos de **Vanløse** ⭐ que no están en el mapa, respuestas nuevas). Luego:
han contestado / sin respuesta / alquilados, y el cruce: en 🟢 en el mapa, en la base con otro estado, no están en la
base. Proponer (sin hacerlo) añadir los que faltan por su enlace y ponerlos en «contactado»; los alquilados, «descartado».
Para cambiar estados: `POST /api/v2/enlaces` + estado por la API v2 (como la barra de la web), nunca a mano en la base.

## Problemas conocidos
- La extensión tapa con `[BLOCKED: …]` las respuestas en bruto de `/api/messaging/…` (llevan datos de sesión): no usar
  la API interna; leer el DOM.
- La salida de `javascript_tool` se corta (~1 500 caracteres): devolver como mucho 10 líneas por llamada.
- No se pueden leer las burbujas (quién escribió cada mensaje): solo el último mensaje que muestra la lista.
- Alguna conversación no tiene «Se bolig» (anuncio borrado o de otra web): sale `ad=null`.
- Enlace de cada conversación para Jon: `https://www.boligportal.dk/indbakke/t/<t>`.
