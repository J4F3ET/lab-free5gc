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
from urllib.parse import parse_qs, unquote

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


PUERTO_FEC = int(os.environ.get("PUERTO_FEC", "7702"))     # servidor de línea del laboratorio de síndrome (shim.py)
MAX_PAQUETES_FEC = 200                                     # paquetes del shim que se copian al resultado de /enviar


def fec_consulta(linea: str, timeout: float = 2.0) -> dict:
    """Una línea al shim (127.0.0.1:PUERTO_FEC), lee hasta el cierre y decodifica JSON.
    Si no responde o responde algo que no es un objeto JSON: {"disponible": False}."""
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_FEC), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(linea.encode() + b"\n")
            obj = json.loads(_leer_todo(s))
    except (OSError, ValueError):
        return {"disponible": False}
    if not isinstance(obj, dict):
        return {"disponible": False}
    obj.setdefault("disponible", True)
    return obj


def _entero(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validar_inyeccion(obj) -> tuple[bool, dict | str]:
    """Valida el cuerpo de POST /fec/inyectar. Devuelve (True, pedido limpio) o (False, motivo).
    bits: entero 1..8 (+ semilla entera opcional)  |  posiciones: lista de 1..8 enteros >= 8
    (los bits 0..7 son el byte de id del código y no se tocan)."""
    if not isinstance(obj, dict):
        return False, "el cuerpo debe ser un objeto JSON"
    if ("bits" in obj) == ("posiciones" in obj):
        return False, "manda exactamente uno: «bits» o «posiciones»"
    if "bits" in obj:
        if not _entero(obj["bits"]) or not 1 <= obj["bits"] <= 8:
            return False, "«bits» debe ser un entero entre 1 y 8"
        pedido = {"bits": obj["bits"]}
        if "semilla" in obj:
            if not _entero(obj["semilla"]):
                return False, "«semilla» debe ser un entero"
            pedido["semilla"] = obj["semilla"]
        return True, pedido
    p = obj["posiciones"]
    if not isinstance(p, list) or not 1 <= len(p) <= 8:
        return False, "«posiciones» debe ser una lista de 1 a 8 enteros"
    if not all(_entero(x) and 8 <= x < 2**31 for x in p):
        return False, "cada posición debe ser un entero >= 8 (los bits 0..7 son el id del código)"
    return True, {"posiciones": p}


def resumen_fec(paquetes: list[dict]) -> dict:
    """Resumen de los paquetes del shim vistos durante un envío."""
    mal = sum(1 for p in paquetes if p.get("estado") == "corregido_mal")
    extra = {"corregidos_mal": mal} if mal else {}   # clave opcional: solo si hubo, no rompe a los clientes viejos
    return {**extra, "paquetes": len(paquetes),
            "con_error_inyectado": sum(1 for p in paquetes if p.get("bits_inyectados")),
            "sindromes_no_cero": sum(int(p.get("sindromes_no_cero") or 0) for p in paquetes),
            "corregidas": sum(int(p.get("corregidas") or 0) for p in paquetes),
            "no_corregibles": sum(int(p.get("no_corregibles") or 0) for p in paquetes),
            "invalidos": sum(1 for p in paquetes if p.get("estado") == "invalido")}


CLAVES_RESUMEN = ("paquetes", "con_error_inyectado", "sindromes_no_cero", "corregidas", "no_corregibles", "invalidos")


def _contadores_fec() -> dict | None:
    """Contadores acumulados del shim (verbo CONTADORES) o None si no responde o es un shim viejo
    (arrancado antes de que existiera el verbo: contesta «comando desconocido»)."""
    r = fec_consulta("CONTADORES")
    if not r.get("ok") or not all(_entero(r.get(k)) for k in (*CLAVES_RESUMEN, "ultimo_id")):
        return None
    return r


def resumen_diferencia(antes: dict, despues: dict) -> dict:
    """Resumen de un envío = contadores después − antes. No depende del anillo de 200 paquetes."""
    r = {k: max(0, despues[k] - antes[k]) for k in CLAVES_RESUMEN}
    if _entero(antes.get("corregidos_mal")) and _entero(despues.get("corregidos_mal")):
        r["corregidos_mal"] = max(0, despues["corregidos_mal"] - antes["corregidos_mal"])   # shim nuevo; opcional
    return r


def _ultimo_id_fec() -> int:
    r = fec_consulta("PAQUETES 0")
    ids = [p["id"] for p in r.get("paquetes", []) if isinstance(p, dict) and _entero(p.get("id"))]
    return max(ids, default=0)


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

    def medir(self, nombre: str, datos: bytes, inyectar: int = 0) -> dict:
        cod = fec_consulta("CODIGO")
        fec_activo = bool(cod.get("disponible") and cod.get("codigo"))
        armada, ultimo, c_antes = None, 0, None
        if fec_activo:
            c_antes = _contadores_fec()
            ultimo = c_antes["ultimo_id"] if c_antes else _ultimo_id_fec()
            if inyectar:
                armada = fec_consulta(f"INYECTAR {json.dumps({'bits': inyectar})}")
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
        if fec_activo:
            c_despues = _contadores_fec() if c_antes else None
            nuevos = fec_consulta(f"PAQUETES {ultimo}").get("paquetes", [])
            nuevos = [p for p in nuevos if isinstance(p, dict) and _entero(p.get("id")) and p["id"] > ultimo]
            if c_antes and c_despues:      # exacto: diferencia de contadores acumulados
                resumen, fuente = resumen_diferencia(c_antes, c_despues), "contadores"
                esperados = resumen["paquetes"]
            else:                          # shim sin CONTADORES: solo se ve lo que queda en el anillo
                resumen, fuente, esperados = resumen_fec(nuevos), "anillo", 0
            # El anillo guarda 200: si los devueltos no cubren todos los nuevos, la lista está incompleta.
            truncado = bool(nuevos and nuevos[0]["id"] > ultimo + 1) or len(nuevos) < esperados
            r["fec"] = {"codigo": cod.get("codigo"), "n": cod.get("n"), "k": cod.get("k"),
                        "inyectado_pedido": inyectar, "armada": armada,
                        "resumen": resumen, "resumen_fuente": fuente, "truncado": truncado,
                        "paquetes": nuevos[-MAX_PAQUETES_FEC:],
                        "sha_identico": r["identico"]}
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

    def _largo(self) -> int:
        """Content-Length como entero >= 0; lo que no sean dígitos ASCII (vacío, «²», «-5») cuenta 0."""
        raw = (self.headers.get("Content-Length") or "0").strip()
        return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 12 else 0

    def _fec(self, linea: str) -> None:
        r = fec_consulta(linea)
        if not r.get("disponible"):
            return self._json(503, {"disponible": False,
                                    "error": "el shim no responde en el puerto FEC: ¿cmp-dn arriba y con FEC activado?"})
        self._json(200, r)

    def do_GET(self):
        ruta, _, consulta = self.path.partition("?")
        if self.path == "/":
            self._responder(200, HTML.encode(), "text/html; charset=utf-8")
        elif self.path == "/historial":
            self._json(200, ENVIOS.historial)
        elif self.path == "/estado":
            self._json(200, {"contadores_dn": contadores() is not None, "destino": DESTINO, "limite": LIMITE})
        elif ruta == "/fec/codigo":
            self._fec("CODIGO")
        elif ruta == "/fec/paquetes":
            desde = parse_qs(consulta).get("desde", ["0"])[0]
            if not (desde.isascii() and desde.isdigit()) or len(desde) > 15:
                return self._json(400, {"error": "«desde» debe ser un entero >= 0"})
            self._fec(f"PAQUETES {int(desde)}")
        else:
            self._json(404, {"error": "no existe"})

    def _post_inyectar(self) -> None:
        # CSRF: un <form> de otro sitio no puede mandar application/json sin pasar por CORS (y aquí no hay).
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._json(415, {"ok": False, "error": "Content-Type debe ser application/json"})
        n = self._largo()
        if n <= 0 or n > 1024:
            return self._json(413, {"error": "el cuerpo debe tener entre 1 B y 1 KB"})
        try:
            obj = json.loads(self.rfile.read(n))
        except ValueError:
            return self._json(400, {"error": "el cuerpo no es JSON válido"})
        ok, pedido = validar_inyeccion(obj)
        if not ok:
            return self._json(400, {"ok": False, "error": pedido})
        r = fec_consulta(f"INYECTAR {json.dumps(pedido)}")
        if not r.get("disponible"):
            return self._json(503, {"ok": False, "disponible": False, "error": "el shim no responde en el puerto FEC"})
        self._json(200 if r.get("ok") else 400, r)

    def do_POST(self):
        if self.path == "/fec/inyectar":
            return self._post_inyectar()
        if self.path != "/enviar":
            return self._json(404, {"error": "no existe"})
        raw = (self.headers.get("X-Inyectar") or "").strip()
        inyectar = 0
        if raw:
            if not (raw.isascii() and raw.isdigit() and len(raw) <= 3) or not 1 <= int(raw) <= 8:
                return self._json(400, {"error": "X-Inyectar debe ser un entero entre 1 y 8"})
            inyectar = int(raw)
        n = self._largo()
        if n <= 0 or n > LIMITE:
            return self._json(413, {"error": f"el archivo debe tener entre 1 B y {LIMITE // 2**20} MB"})
        if not ENVIOS.ocupado.acquire(blocking=False):
            return self._json(409, {"error": "ya hay un envío en curso; espera a que termine"})
        try:
            datos = self.rfile.read(n)
            if len(datos) != n:
                raise ConnectionError("la subida desde el navegador se cortó")
            nombre = unquote(self.headers.get("X-Nombre", "archivo"))[:120]
            self._json(200, ENVIOS.medir(nombre, datos, inyectar))
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
:root{--warn:#9a6700;--c-limpio:#9a9992;--c-corr:#1f7a4d;--c-mal:#b3261e;--c-cmal:#d9480f;--par:#e7efff}
@media (prefers-color-scheme:dark){:root{--warn:#e3b341;--c-limpio:#6b6b66;--c-corr:#5cc28d;--c-mal:#ff8a80;--c-cmal:#ff922b;--par:#26324f}}
nav.tabs{display:flex;gap:6px;margin:0 0 18px;flex-wrap:wrap}nav.tabs button,.btn{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
nav.tabs button.sel{border-color:var(--acc);color:var(--acc)}.btn:hover{border-color:var(--acc)}
.aviso{border:1px solid var(--warn);color:var(--warn);border-radius:8px;padding:8px 12px;margin:10px 0;font-size:13px}
.mat{border-collapse:collapse;display:inline-table;margin:4px 14px 8px 0}.mat td{border:1px solid var(--line);padding:2px 7px;text-align:center;font:13px ui-monospace,monospace}
.mat td.par{background:var(--par)}.ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}.ctl input{font:inherit;width:90px;padding:5px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
#linea{display:flex;flex-wrap:wrap;gap:3px;margin:10px 0}#linea button{width:26px;height:26px;padding:0;border-radius:5px;border:2px solid transparent;cursor:pointer;color:#fff;font-size:10px}
#linea button.limpio{background:var(--c-limpio)}#linea button.corregido{background:var(--c-corr);border-color:var(--fg)}#linea button.corregido_mal{background:var(--c-cmal);border-color:var(--fg)}#linea button.no_corregible,#linea button.invalido{background:var(--c-mal)}
#linea button.iny{outline:2px dashed var(--warn);outline-offset:1px}#linea button.act{box-shadow:0 0 0 2px var(--acc)}
#detalle ol{padding-left:20px}#detalle li{margin:6px 0}.st-limpio{color:var(--c-limpio)}.st-corregido{color:var(--c-corr)}.st-corregido_mal{color:var(--c-cmal)}.st-no_corregible,.st-invalido{color:var(--c-mal)}
tr.fila{cursor:pointer}tr.fila:hover{background:var(--bg)}
</style></head><body><main>
<h1>Enviar un archivo por la red 5G</h1>
<p class="sub">El archivo baja comprimido por el núcleo y la radio simulada hasta el UE, vuelve por la subida y se compara con el original. Máximo 20 MB.</p>
<nav class="tabs"><button id="t-envio" class="sel" type="button">Envío</button><button id="t-lab" type="button">Laboratorio de síndrome</button></nav>
<div id="vista-envio">
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
</div>
<div id="vista-lab" hidden>
<h2 style="font-size:18px;margin:0 0 4px">Laboratorio de síndrome</h2>
<p class="sub" style="margin-bottom:8px">Detección y corrección de errores con códigos de bloque lineales: recibido r = c + e, síndrome s = r·Hᵀ; s = 0 sin error, s ≠ 0 error, y si coincide con la columna i de H se voltea el bit i.</p>
<div class="aviso">Para cambiar de código o canal hay que fijar FEC (repeticion_5_1, hamming_7_4, secded_8_4) y CANAL (bsc:P o rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO) con «lab5g.sh env-set» y reiniciar cmp-ue y cmp-dn.</div>
<div class="aviso">Nota: el canal se modela en la recepción del shim de cmp-dn, es decir sobre el tráfico que RECIBE el DN (p. ej. el viaje de vuelta del eco). El error se inyecta ahí, no en la bajada.</div>
<div class="aviso">Honestidad: con 2 errores Hamming (7,4) corrige mal (d_min = 3: detecta 2, corrige 1) y, si el paquete no se reconstruye, queda como «corrigió mal» (si sí se reconstruye, sigue contado como «corregido»); el receptor no compara contra el original, así que la columna «íntegro» es lo que dice el shim, no una prueba.</div>
<div id="lab-codigo" class="res"></div>
<div class="res"><div class="ctl">
<button class="btn" id="b-uno" type="button">Inyectar 1 bit</button><button class="btn" id="b-dos" type="button">Inyectar 2 bits</button>
<label class="k">bit exacto <input id="bit-exacto" type="number" min="8" step="1" placeholder="&ge; 8"></label><button class="btn" id="b-exacto" type="button">Inyectar ahí</button>
</div><div id="armada" class="k"></div></div>
<h3 style="font-size:15px;margin:18px 0 0">Línea de tiempo de paquetes</h3>
<div class="k">gris: limpio · verde con borde: corregido · naranja con borde: corrigió mal (el síndrome corrigió, el paquete no se reconstruyó) · rojo: no corregible o inválido (con SECDED, «doble detectado») · borde punteado: con error inyectado. Clic para ver el detalle.</div>
<div id="linea"></div>
<div class="ctl"><label class="k">estado <select id="filtro"><option value="">todos</option><option value="limpio">limpio</option><option value="corregido">corregido</option><option value="corregido_mal">corrigió mal</option><option value="no_corregible">no corregible</option><option value="invalido">inválido</option></select></label><span id="cuenta" class="k"></span></div>
<div class="tabla"><table><thead><tr><th>id</th><th>lado</th><th>sec.</th><th>códec</th><th>estado</th><th>s≠0</th><th>corr.</th><th>no corr.</th><th>bits volteados</th></tr></thead><tbody id="lab-tabla"></tbody></table></div>
<div id="detalle" class="res" hidden></div>
</div>
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
  ${r.fec?`<div><div class="k">FEC ${esc(String(r.fec.codigo))}</div><div class="v">${Number(r.fec.resumen.paquetes)} paq.</div><div class="k">con error ${Number(r.fec.resumen.con_error_inyectado)} · s≠0 ${Number(r.fec.resumen.sindromes_no_cero)} · corregidos ${Number(r.fec.resumen.corregidas)} · corrigió mal ${Number(r.fec.resumen.corregidos_mal||0)} · no corregibles ${Number(r.fec.resumen.no_corregibles)} · inválidos ${Number(r.fec.resumen.invalidos)}${r.fec.truncado?' · <b class="bad">lista de paquetes truncada ('+(r.fec.resumen_fuente==='contadores'?'el resumen sí es exacto':'resumen incompleto: el shim no tiene CONTADORES')+')</b>':''}</div></div>`:''}
  </div><details><summary>SHA-256</summary><code>original ${r.sha_original}<br>vuelta&nbsp;&nbsp; ${r.sha_vuelta}</code></details></div>`;}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
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

/* ── Laboratorio de síndrome: todo texto del shim entra por textContent (nunca innerHTML) ── */
function h(tag,attrs,...hijos){ const e=document.createElement(tag);
  for(const [k,v] of Object.entries(attrs||{})){ if(k==='class') e.className=v; else e.setAttribute(k,String(v)); }
  for(const c of hijos.flat()){ if(c==null||c===false) continue; e.append(c instanceof Node?c:document.createTextNode(String(c))); }
  return e; }
const ETQ={limpio:'limpio',corregido:'corregido',corregido_mal:'corrigió mal',no_corregible:'no corregible',invalido:'inválido'};
let secded=false;   // código activo = SECDED (8,4): su «no_corregible» es «doble detectado» (el valor interno no cambia)
function etiqueta(est){ return secded&&est==='no_corregible'?'doble detectado':ETQ[est]; }
function marcarSecded(){ const o=document.querySelector('#filtro option[value="no_corregible"]'); if(o) o.textContent=secded?'doble detectado':'no corregible'; }
const lab={paquetes:[],ultimo:0,codigo:null,sel:null,timer:null,n:0};
function vista(cual){ const l=cual==='lab';
  $('#vista-envio').hidden=l; $('#vista-lab').hidden=!l;
  $('#t-envio').classList.toggle('sel',!l); $('#t-lab').classList.toggle('sel',l);
  if(l){ labCodigo(); labSondear(); if(!lab.timer) lab.timer=setInterval(labSondear,1500); }
  else if(lab.timer){ clearInterval(lab.timer); lab.timer=null; } }
$('#t-envio').onclick=()=>vista('envio'); $('#t-lab').onclick=()=>vista('lab');
function matriz(titulo,filas,paridad){ return h('div',{style:'display:inline-block;vertical-align:top'},h('div',{class:'k'},titulo),
  h('table',{class:'mat'},filas.map(f=>h('tr',{},f.map((v,j)=>h('td',{class:paridad(j)?'par':''},v)))))); }
async function labCodigo(){ const cont=$('#lab-codigo'); cont.replaceChildren(); secded=false; marcarSecded();
  let j; try{ const r=await fetch('fec/codigo'); j=await r.json(); if(!r.ok) throw new Error(j.error||r.status); }
  catch(e){ cont.append(h('b',{},'FEC desactivado'),h('div',{class:'k'},' (sin respuesta del shim: '+String(e.message||e)+')')); lab.codigo=null; return; }
  if(!j.codigo){ cont.append(h('b',{},'FEC desactivado'),h('div',{class:'k'},'El shim no tiene código de canal activo; la página de envío funciona igual.')); lab.codigo=null; return; }
  lab.codigo=j; const n=j.n,k=j.k,r=n-k;
  secded=String(j.codigo).startsWith('secded'); marcarSecded();
  const canal=j.canal?'Canal aleatorio: '+String(j.canal):'Canal aleatorio: apagado (solo inyección manual)';
  const G=j.P.map((fila,i)=>fila.concat(Array.from({length:k},(_,c)=>c===i?1:0)));
  const piezas=[h('div',{},h('b',{},'Código activo: '+j.codigo),' ',h('span',{class:'k'},`(n=${n}, k=${k}, ${r} bits de paridad) · ${canal}`)),
    matriz('G = [P : I_k]  (k×n)',G,c=>c<r), matriz('H = [I : Pᵀ]  ((n−k)×n)',j.H,c=>c>=r),
    h('div',{class:'k'},'Celdas sombreadas: la parte de paridad (en G es P; en H es Pᵀ). Palabra código c = m·G, s = r·Hᵀ. Posiciones dentro de una palabra y columnas de H se numeran desde 1, como en clase (el bit 5 de la palabra es la columna 5 de H).'),
    secded?h('div',{class:'aviso'},'SECDED detecta 2 errores y no los corrige mal: con 2 errores en una palabra el estado es «doble detectado» (no_corregible).'):null,j.auto?bloqueAuto(j):null]; cont.append(...piezas.filter(Boolean)); }
/* FEC=auto: el shim informa código activo, BER estimado (síndromes) y la escalera; campos ausentes = null */
function bloqueAuto(j){ const ber=typeof j.ber_estimado==='number'?j.ber_estimado.toExponential(2):'—';
  const esc=(j.escalera||[]).join(' → ')||'—';
  return h('div',{class:'aviso'},`Modo automático: código activo ${j.codigo_activo||j.codigo}, BER estimado ${ber} (escalera: ${esc}).`,
    h('br'),'El receptor mide el BER con los síndromes y se lo informa al emisor cada ~2 s, con histéresis: el código sube al instante y baja solo tras 3 informes con BER bajo.'); }
function columnasH(){ const H=lab.codigo&&lab.codigo.H; if(!H) return {}; const m={};
  for(let j=0;j<H[0].length;j++){ m[H.map(f=>f[j]).join('')]=j; } return m; }
function estadoPaq(p){ return ETQ[p.estado]?p.estado:'invalido'; }
function labRender(){ const f=$('#filtro').value, L=$('#linea'); L.replaceChildren();
  for(const p of lab.paquetes.slice(-400)){ const est=estadoPaq(p);
    const b=h('button',{type:'button',class:est+((p.bits_inyectados||[]).length?' iny':'')+(lab.sel===p.id?' act':''),
      title:`#${p.id} ${etiqueta(est)} · ${p.codec||'?'} · sec ${p.secuencia}`},String(p.codec||'?').slice(0,2));
    b.onclick=()=>labDetalle(p.id); if(f&&est!==f) b.style.opacity='.25'; L.append(b); }
  const filas=lab.paquetes.filter(p=>!f||estadoPaq(p)===f).slice(-100).reverse();
  $('#cuenta').textContent=`${filas.length} de ${lab.paquetes.length} paquetes (tabla: últimos 100)`;
  $('#lab-tabla').replaceChildren(...filas.map(p=>{ const est=estadoPaq(p);
    const tr=h('tr',{class:'fila'},h('td',{},p.id),h('td',{},p.lado),h('td',{},p.secuencia),h('td',{},p.codec),
      h('td',{class:'st-'+est},etiqueta(est)),h('td',{},p.sindromes_no_cero),h('td',{},p.corregidas),h('td',{},p.no_corregibles),
      h('td',{},(p.bits_inyectados||[]).join(', ')||'—')); tr.onclick=()=>labDetalle(p.id); return tr; })); }
async function labSondear(){ let j;
  try{ const r=await fetch('fec/paquetes?desde='+lab.ultimo); j=await r.json(); if(!r.ok) throw new Error(j.error||r.status); }
  catch(e){ $('#cuenta').textContent='sin datos del shim: '+String(e.message||e); return; }
  const nuevos=(j.paquetes||[]).filter(p=>p.id>lab.ultimo);
  if(nuevos.length){ lab.paquetes=lab.paquetes.concat(nuevos).slice(-1000); lab.ultimo=Math.max(...nuevos.map(p=>p.id)); }
  const a=$('#armada'); a.textContent=j.armada?('Armada: '+JSON.stringify(j.armada)):'Sin error armado: el tráfico pasa limpio.';
  if(lab.codigo&&lab.codigo.auto&&++lab.n%8===0) labCodigo();   // en auto el código activo cambia: refresca cada ~12 s
  labRender(); }
async function labInyectar(cuerpo){ const a=$('#armada');
  try{ const r=await fetch('fec/inyectar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cuerpo)});
    const j=await r.json(); a.textContent=j.ok?('Armada: '+JSON.stringify(j.armada)):('No se pudo armar: '+(j.error||r.status)); }
  catch(e){ a.textContent='No se pudo armar: '+String(e.message||e); } }
$('#b-uno').onclick=()=>labInyectar({bits:1}); $('#b-dos').onclick=()=>labInyectar({bits:2});
$('#b-exacto').onclick=()=>{ const v=Number($('#bit-exacto').value);
  if(!Number.isInteger(v)||v<8){ $('#armada').textContent='El bit exacto debe ser un entero ≥ 8 (0..7 es el id del código).'; return; }
  labInyectar({posiciones:[v]}); };
$('#filtro').onchange=labRender;
function labDetalle(id){ lab.sel=id; const p=lab.paquetes.find(x=>x.id===id), d=$('#detalle'); if(!p) return;
  d.hidden=false; d.replaceChildren(); const est=estadoPaq(p), cols=columnasH();
  const sobre=p.bytes_comprimido?((p.bytes_fec/p.bytes_comprimido-1)*100).toFixed(1)+' %':'—';
  const sind=Object.entries(p.sindromes||{});
  const pasos=[
    h('li',{},'Códec elegido por el bandido: ',h('b',{},p.codec||'?'),` (id ${p.codec_id}, lado ${p.lado}, secuencia ${p.secuencia}, código ${p.codigo})`),
    h('li',{},`Bytes comprimidos: ${p.bytes_comprimido} (de ${p.bytes_ip} B de paquete IP)`),
    h('li',{},`Bytes con paridad: ${p.bytes_fec} (sobrecoste ${sobre}); ${p.palabras} palabras código`),
    h('li',{},'Bits volteados en el canal: ',(p.bits_inyectados||[]).length?(p.bits_inyectados.join(', ')+' (posición en el marco; 0..7 es el id del código)'):'ninguno'),
    h('li',{},'Síndromes observados (s ≠ 0): ',sind.length?h('ul',{},sind.map(([s,c])=>{ const j=cols[s];
        const corr=(p.posiciones_corregidas||[]).filter(x=>x[1]===j).length;
        return h('li',{},h('code',{},s),` × ${c} → `,j==null?'no coincide con ninguna columna de H: no corregible con 1 bit':`coincide con la columna ${j+1} de H (bit ${j+1} de la palabra, contando desde 1); corregida ${corr} vez/veces`); })):'ninguno'),
    (p.posiciones_corregidas||[]).length?h('li',{},'Correcciones (palabra desde 0, posición en la palabra desde 1): ',p.posiciones_corregidas.map(x=>`(${x[0]}, ${x[1]+1})`).join(' ')):null,
    h('li',{},'Resultado: ',h('b',{class:'st-'+est},etiqueta(est)),' · íntegro: ',h('b',{class:p.integro?'ok':'bad'},p.integro?'sí':'no'))];
  d.append(h('h3',{style:'font-size:15px;margin:0 0 6px'},`Paquete #${p.id} paso a paso`),h('ol',{},pasos),
    h('div',{class:'k'},secded?'SECDED detecta 2 errores y no los corrige mal (queda como «doble detectado»).':'Con 2 errores Hamming (7,4) puede «corregir» mal y entregar otra palabra código; el receptor no compara contra el original.'));
  labRender(); }
</script></body></html>"""


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "eco":
        eco()
    elif modo == "pagina":
        pagina()
    else:
        sys.exit("uso: envio.py pagina|eco")
