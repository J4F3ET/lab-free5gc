#!/usr/bin/env python3
"""
envio.py — Mandar un archivo real (audio, video, imagen, texto) por la red 5G a través del
compresor, ida y vuelta, y ver qué pasó. Solo biblioteca estándar.

  pagina  (contenedor envio, en el netns de cmp-dn):  python3 envio.py pagina
  eco     (contenedor envio-eco, en el netns del UE): python3 envio.py eco

    navegador ──HTTP──► página (DN) ──TCP──► cmp0 10.99.0.2 ─[comprimir]─► UPF ══ 5G ══► UE
                                                                                         │
    navegador ◄── resultados ── página ◄── cmp0 ◄─[descomprimir]── 5G ◄─[comprimir]── eco (UE)

La página manda el archivo por TCP a 10.99.0.1 (el cmp0 del UE): la bajada pasa por el compresor
de cmp-dn, el núcleo y la radio simulada. El eco del UE lo devuelve por la subida, comprimida por
cmp-ue. Al final se compara lo que volvió con el original (SHA-256, H, I(O;R)) y se leen los
contadores de los dos compresores antes y después (shim.py los sirve en 127.0.0.1:7701), así se
sabe cuántos bytes viajaron de verdad por el túnel y qué códec eligió el bandido.

Seguridad: no tiene login. El puerto se publica en la LAN y el tailnet, nunca por Funnel.
Límite de 20 MB por archivo; el archivo vive solo en memoria y se descarta al terminar.
"""
from __future__ import annotations

import hashlib, json, math, os, re, socket, struct, sys, threading, time, urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

PUERTO_PAGINA = int(os.environ.get("PUERTO_PAGINA", "8088"))
PUERTO_ECO = int(os.environ.get("PUERTO_ECO", "7800"))
DESTINO = os.environ.get("DESTINO", "10.99.0.1")          # cmp0 del UE
PUERTO_CONTADORES = int(os.environ.get("PUERTO_CONTADORES", "7701"))
LIMITE = int(os.environ.get("LIMITE_MB", "20")) * 1024 * 1024
TIEMPO = 900                                               # s: 20 MB a ~1 Mbps son ~6 min por sentido
CABECERA_TUNEL = 28                                        # IP + UDP del túnel, no está en el marco


# ═══════════════════════════ comunes ═══════════════════════════
def contadores() -> dict | None:
    """Contadores del compresor de este netns (shim.py), o None si no está corriendo."""
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_CONTADORES), timeout=2) as s:
            return json.loads(_leer_todo(s))
    except (OSError, ValueError):
        return None


PUSHGATEWAY = os.environ.get("PUSHGATEWAY", "http://10.100.205.14:9091")
_METRICA = re.compile(r'^(lab5g_cmp_bytes|lab5g_cmp_errores|lab5g_cmp_paquetes|push_time_seconds)\{([^}]*)\} (\S+)$')


def contadores_pushgateway() -> dict | None:
    """Respaldo si shim.py no sirve contadores locales (un cmp-dn/cmp-ue arrancado antes de
    agregarlos: reiniciarlos en este lab es caro). Lo que shim.py empuja cada 10 s, por lado:
    {"dn": {"bytes": ..., "codec": ..., "push": epoch}, "ue": {...}}."""
    try:
        texto = urllib.request.urlopen(f"{PUSHGATEWAY}/metrics", timeout=5).read().decode()
    except OSError:
        return None
    lados: dict[str, dict] = {}
    for linea in texto.splitlines():
        m = _METRICA.match(linea)
        if not m:
            continue
        nombre, etiquetas, valor = m.group(1), dict(re.findall(r'(\w+)="([^"]*)"', m.group(2))), float(m.group(3))
        if etiquetas.get("job") != "cmp_shim" or "lado" not in etiquetas:
            continue
        l = lados.setdefault(etiquetas["lado"], {"bytes": {}, "codec": {}, "push": 0.0})
        if nombre == "lab5g_cmp_bytes":
            l["bytes"][f'{etiquetas["tipo"]}_{etiquetas["sentido"]}'] = int(valor)
        elif nombre == "lab5g_cmp_errores":
            l["bytes"]["errores"] = int(valor)
        elif nombre == "lab5g_cmp_paquetes":
            l["codec"][etiquetas["codec"]] = int(valor)
        else:
            l["push"] = valor
    return lados or None


def _esperar_empuje(despues_de: float, lados=("dn", "ue"), maximo: float = 30.0) -> dict | None:
    """Espera a que los dos shim empujen DESPUÉS de `despues_de` (epoch) y devuelve esa lectura."""
    fin = time.time() + maximo
    while time.time() < fin:
        pg = contadores_pushgateway()
        if pg and all(pg.get(l, {}).get("push", 0) > despues_de for l in lados):
            return pg
        time.sleep(1)
    return contadores_pushgateway()


def _leer_todo(s: socket.socket) -> bytes:
    partes = []
    while b := s.recv(65536):
        partes.append(b)
    return b"".join(partes)


def _leer(s: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        b = s.recv(min(1 << 20, n - len(buf)))
        if not b:
            raise ConnectionError(f"la conexión se cerró a los {len(buf)} de {n} bytes")
        buf += b
    return bytes(buf)


def _delta(antes: dict | None, despues: dict | None) -> dict | None:
    if not antes or not despues:
        return None
    return {grupo: {k: despues[grupo][k] - antes[grupo].get(k, 0) for k in despues[grupo]}
            for grupo in ("bytes", "codec")}


# ═══════════════════════════ eco (UE) ═══════════════════════════
def eco() -> None:
    """Recibe [largo 8 B][datos], devuelve los datos, espera el acuse (1 B) y manda
    [largo 4 B][JSON con los contadores de cmp-ue durante la devolución]."""
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PUERTO_ECO)); srv.listen(4)
    print(f"[envio-eco] escuchando en :{PUERTO_ECO}", flush=True)
    while True:
        c, origen = srv.accept()
        with c:
            try:
                c.settimeout(TIEMPO)
                (n,) = struct.unpack("!Q", _leer(c, 8))
                if n > LIMITE:
                    continue
                datos = _leer(c, n)
                antes = contadores()
                c.sendall(datos)
                _leer(c, 1)                       # el otro extremo ya recibió todo
                d = json.dumps(_delta(antes, contadores())).encode()
                c.sendall(struct.pack("!I", len(d)) + d)
                print(f"[envio-eco] {n} B devueltos a {origen[0]}", flush=True)
            except (OSError, ConnectionError, struct.error) as e:
                print(f"[envio-eco] {e!r}", flush=True)


# ═══════════════════════════ medición ═══════════════════════════
def _h(conteo: Counter, n: int) -> float:
    return -sum(c / n * math.log2(c / n) for c in conteo.values()) if n else 0.0


def informacion(o: bytes, r: bytes) -> dict:
    """H(O), H(R) e I(O;R) en bits por byte, byte a byte por posición (como informacion.py:
    lo que falta en R cuenta como borrado). Si R = O, I(O;R) = H(O) sin contar la conjunta."""
    hO = _h(Counter(o), len(o))
    if r == o:
        return {"H_O": hO, "H_R": hO, "I_OR": hO}
    y = list(r[:len(o)]) + [256] * max(0, len(o) - len(r))
    hR = _h(Counter(y), len(o))
    hOR = _h(Counter(zip(o, y)), len(o))
    return {"H_O": hO, "H_R": hR, "I_OR": max(0.0, hO + hR - hOR)}


def _tunel(d: dict | None) -> dict | None:
    """Bytes de un sentido según los contadores de UN compresor (el que comprime ese sentido)."""
    if d is None:
        return None
    b = d["bytes"]
    paquetes = sum(d["codec"].values())
    ip, marco = b["orig_tx"], b["marco_tx"]
    return {"bytes_ip": ip, "bytes_marco": marco, "paquetes": paquetes,
            "bytes_tunel": marco + CABECERA_TUNEL * paquetes,
            "ahorro_pct": round(100 * (1 - marco / ip), 2) if ip else None,
            "codec": {k: v for k, v in d["codec"].items() if v}}


class Envios:
    def __init__(self):
        self.ocupado = threading.Lock()
        self.historial: list[dict] = []

    def medir(self, nombre: str, datos: bytes) -> dict:
        antes = contadores()
        pg_antes = contadores_pushgateway() if antes is None else None
        t0 = time.perf_counter()
        with socket.create_connection((DESTINO, PUERTO_ECO), timeout=20) as s:
            s.settimeout(TIEMPO)
            s.sendall(struct.pack("!Q", len(datos)) + datos)
            t_ida = time.perf_counter() - t0      # aproximado: hasta que el kernel aceptó todo
            vuelta = _leer(s, len(datos))
            t_total = time.perf_counter() - t0
            s.sendall(b"\x01")
            (n,) = struct.unpack("!I", _leer(s, 4))
            ue = json.loads(_leer(s, n))
        if antes is not None:
            dn, medicion = _delta(antes, contadores()), "contadores locales de shim.py (exactos)"
        elif pg_antes:
            # Los contadores viejos del pushgateway: ventana = del último empuje antes de empezar al
            # primero después de terminar (hasta ~10 s de más por cada lado).
            pg = _esperar_empuje(time.time())
            dn = _delta(pg_antes.get("dn"), pg and pg.get("dn"))
            ue = ue or _delta(pg_antes.get("ue"), pg and pg.get("ue"))
            medicion = "pushgateway (ventana de ±10 s: incluye otro tráfico del túnel en ese lapso)"
        else:
            dn, medicion = None, "sin contadores"
        info = informacion(datos, vuelta)
        r = {"nombre": nombre, "bytes": len(datos), "hora": time.strftime("%H:%M:%S"),
             "sha_original": hashlib.sha256(datos).hexdigest(), "sha_vuelta": hashlib.sha256(vuelta).hexdigest(),
             "segundos": round(t_total, 2), "segundos_envio": round(t_ida, 2),
             "mbps": round(2 * 8 * len(datos) / t_total / 1e6, 3) if t_total else None,
             **{k: round(v, 4) for k, v in info.items()},
             "bajada": _tunel(dn),      # la comprime cmp-dn
             "subida": _tunel(ue)}      # la comprime cmp-ue
        r["medicion"] = medicion
        r["identico"] = r["sha_original"] == r["sha_vuelta"]
        r["info_entregada_pct"] = round(100 * info["I_OR"] / info["H_O"], 2) if info["H_O"] else 100.0
        if dn:
            r["errores_dn"] = dn["bytes"]["errores"]
        if ue:
            r["errores_ue"] = ue["bytes"]["errores"]
        self.historial = [r] + self.historial[:19]
        return r


ENVIOS = Envios()


# ═══════════════════════════ página (DN) ═══════════════════════════
class Pagina(BaseHTTPRequestHandler):
    server_version = "lab5g-envio"

    def _responder(self, codigo: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo: int, obj) -> None:
        self._responder(codigo, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path == "/":
            self._responder(200, HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/historial":
            self._json(200, ENVIOS.historial)
        elif self.path == "/estado":
            self._json(200, {"contadores_dn": contadores() is not None, "destino": DESTINO, "limite": LIMITE})
        else:
            self._json(404, {"error": "no existe"})

    def do_POST(self):
        if self.path != "/enviar":
            return self._json(404, {"error": "no existe"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > LIMITE:
            return self._json(413, {"error": f"el archivo debe tener entre 1 B y {LIMITE // 2**20} MB"})
        if not ENVIOS.ocupado.acquire(blocking=False):
            return self._json(409, {"error": "ya hay un envío en curso; espera a que termine"})
        try:
            datos = self.rfile.read(n)
            if len(datos) != n:
                raise ConnectionError("la subida desde el navegador se cortó")
            nombre = unquote(self.headers.get("X-Nombre", "archivo"))[:120]
            self._json(200, ENVIOS.medir(nombre, datos))
        except (OSError, ConnectionError) as e:
            self._json(502, {"error": f"no se pudo hacer la ida y vuelta por {DESTINO}:{PUERTO_ECO}: {e}. "
                                      "¿Están arriba el perfil 'compresion', la sesión PDU del UE y envio-eco?"})
        finally:
            datos = None
            ENVIOS.ocupado.release()

    def log_message(self, fmt, *args):
        print(f"[envio] {self.address_string()} {fmt % args}", flush=True)


def pagina() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PUERTO_PAGINA), Pagina)
    print(f"[envio] página en :{PUERTO_PAGINA}, eco en {DESTINO}:{PUERTO_ECO}, contadores "
          f"{'sí' if contadores() else 'no (¿cmp-dn arriba?)'}", flush=True)
    srv.serve_forever()


HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Envío por 5G</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mute:#6b6b66;--card:#fff;--line:#e3e2dc;--ok:#1f7a4d;--bad:#b3261e;--acc:#2f5bd3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mute:#9a9992;--card:#1f1f1d;--line:#33332f;--ok:#5cc28d;--bad:#ff8a80;--acc:#8fa8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 60px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mute);margin:0 0 22px}
.zona{display:block;border:1.5px dashed var(--line);border-radius:12px;background:var(--card);padding:28px;text-align:center;cursor:pointer}
.zona.sobre{border-color:var(--acc)}.zona b{color:var(--acc)}input[type=file]{display:none}
#estado{margin:14px 0;min-height:22px;color:var(--mute)}
.res{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}
.res h2{font-size:16px;margin:0 0 10px;overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.k{color:var(--mute);font-size:12px}.v{font-size:18px;font-variant-numeric:tabular-nums}.ok{color:var(--ok)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mute);font-weight:500}.tabla{overflow-x:auto}
code{font-size:12px;overflow-wrap:anywhere}details{margin-top:10px;color:var(--mute)}
</style></head><body><main>
<h1>Enviar un archivo por la red 5G</h1>
<p class="sub">El archivo baja comprimido por el núcleo y la radio simulada hasta el UE, vuelve por la subida y se compara con el original. Máximo 20 MB.</p>
<label class="zona" id="zona"><input type="file" id="archivo">Arrastra un archivo aquí o <b>elige uno</b><br><span class="k">audio, video, imagen o texto</span></label>
<div id="estado"></div>
<div id="ultimo"></div>
<h2 style="font-size:16px;margin-top:28px">Envíos anteriores</h2>
<div class="tabla"><table><thead><tr><th>Archivo</th><th>Tamaño</th><th>Idéntico</th><th>I(O;R)/H(O)</th><th>Ahorro bajada</th><th>Ahorro subida</th><th>Tiempo</th></tr></thead><tbody id="hist"></tbody></table></div>
<details><summary>Cómo leer esto</summary><p>
<b>Ahorro</b>: 1 − bytes del marco comprimido / bytes de los paquetes IP que entraron al compresor (incluye cabeceras TCP/IP y los ACK). <b>Bytes en el túnel</b> suma además los 28 B de IP+UDP del túnel por paquete, que en PDCP no existirían.
<b>I(O;R)</b>: información mutua entre el original y lo que volvió, byte a byte; si el SHA-256 coincide vale H(O): no se perdió nada.
Los contadores son de todo el túnel durante el envío: si a la vez corre el generador de tráfico, se mezcla.
Un archivo ya comprimido (MP3, JPG, MP4) casi no ahorra: el bandido aprende a mandarlo con «ninguno».</p></details>
</main><script>
const $=s=>document.querySelector(s), MB=b=>(b/1048576).toFixed(b<1048576?3:2)+' MB';
const pct=v=>v==null?'—':v.toFixed(1)+' %';
function sentido(t,n){ if(!t) return `<div><div class="k">${n}</div><div class="v">sin contadores</div></div>`;
  const cod=Object.entries(t.codec).sort((a,b)=>b[1]-a[1]).map(([c,v])=>`${c} ${v}`).join(', ');
  return `<div><div class="k">${n}: ahorro</div><div class="v">${pct(t.ahorro_pct)}</div><div class="k">${MB(t.bytes_ip)} IP → ${MB(t.bytes_tunel)} en el túnel · ${t.paquetes} paq.<br>códec: ${cod||'—'}</div></div>`;}
function mostrar(r){ const ok=r.identico;
  $('#ultimo').innerHTML=`<div class="res"><h2>${esc(r.nombre)}</h2><div class="grid">
  <div><div class="k">Resultado</div><div class="v ${ok?'ok':'bad'}">${ok?'Idéntico (SHA-256)':'Distinto'}</div></div>
  <div><div class="k">Tamaño</div><div class="v">${MB(r.bytes)}</div></div>
  <div><div class="k">Ida y vuelta</div><div class="v">${r.segundos} s</div><div class="k">${r.mbps} Mbps efectivos</div></div>
  <div><div class="k">H(O) · H(R)</div><div class="v">${r.H_O.toFixed(3)} · ${r.H_R.toFixed(3)}</div><div class="k">bits por byte</div></div>
  <div><div class="k">I(O;R)</div><div class="v">${r.I_OR.toFixed(3)}</div><div class="k">${pct(r.info_entregada_pct)} de H(O)</div></div>
  ${sentido(r.bajada,'Bajada (cmp-dn)')}${sentido(r.subida,'Subida (cmp-ue)')}
  </div><details><summary>SHA-256</summary><code>original ${r.sha_original}<br>vuelta&nbsp;&nbsp; ${r.sha_vuelta}</code></details></div>`;}
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function historial(){ const h=await (await fetch('historial')).json();
  $('#hist').innerHTML=h.map(r=>`<tr><td>${esc(r.nombre)}</td><td>${MB(r.bytes)}</td><td class="${r.identico?'ok':'bad'}">${r.identico?'sí':'no'}</td><td>${pct(r.info_entregada_pct)}</td><td>${pct(r.bajada&&r.bajada.ahorro_pct)}</td><td>${pct(r.subida&&r.subida.ahorro_pct)}</td><td>${r.segundos} s</td></tr>`).join('')||'<tr><td colspan="7">Todavía nada.</td></tr>';}
async function enviar(f){ if(!f) return; if(f.size>20*1048576){$('#estado').textContent='Pasa de 20 MB.';return;}
  $('#estado').textContent=`Enviando ${f.name} (${MB(f.size)}) por la red 5G… puede tardar: la radio simulada va a pocos Mbps.`;
  const t0=performance.now();
  try{ const r=await fetch('enviar',{method:'POST',headers:{'X-Nombre':encodeURIComponent(f.name)},body:f});
    const j=await r.json(); if(!r.ok) throw new Error(j.error);
    $('#estado').textContent=`Listo en ${((performance.now()-t0)/1000).toFixed(1)} s.`; mostrar(j); historial();
  }catch(e){ $('#estado').innerHTML=`<span class="bad">${esc(String(e.message||e))}</span>`; } }
$('#archivo').onchange=e=>enviar(e.target.files[0]);
const z=$('#zona'); z.ondragover=e=>{e.preventDefault();z.classList.add('sobre')}; z.ondragleave=()=>z.classList.remove('sobre');
z.ondrop=e=>{e.preventDefault();z.classList.remove('sobre');enviar(e.dataTransfer.files[0])};
historial();
</script></body></html>"""


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "eco":
        eco()
    elif modo == "pagina":
        pagina()
    else:
        sys.exit("uso: envio.py pagina|eco")
