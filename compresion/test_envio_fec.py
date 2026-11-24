"""Pruebas del laboratorio de síndrome de envio.py con un shim FALSO (servidor de línea en un hilo)
y el servidor HTTP real de envio.py en un puerto efímero. Solo biblioteca estándar."""
import json, os, socket, socketserver, struct, sys, threading, unittest, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path  # noqa: E402
import envio  # noqa: E402

P = [[1, 1, 0], [0, 1, 1], [1, 1, 1], [1, 0, 1]]
H = [[1, 0, 0, 1, 0, 1, 1], [0, 1, 0, 1, 1, 1, 0], [0, 0, 1, 0, 1, 1, 1]]
CODIGO = {"codigo": "hamming74", "n": 7, "k": 4, "P": P, "H": H}


def paquete(i, estado="limpio", bits=()):
    return {"id": i, "secuencia": i, "lado": "dn", "t": 1.0, "codigo": "hamming74", "codec_id": 1, "codec": "zstd",
            "bytes_comprimido": 40, "bytes_fec": 71, "bytes_ip": 100, "bits_inyectados": list(bits),
            "palabras": 20, "sindromes_no_cero": 1 if bits else 0, "corregidas": 1 if estado == "corregido" else 0,
            "no_corregibles": 1 if estado == "no_corregible" else 0, "sindromes": {"011": 1} if bits else {},
            "posiciones_corregidas": [[0, 3]] if estado == "corregido" else [], "estado": estado,
            "integro": estado in ("limpio", "corregido")}


class ShimFalso(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, codigo=CODIGO):
        super().__init__(("127.0.0.1", 0), ShimFalso.Linea)
        self.codigo, self.recibidas, self.paquetes = codigo, [], [paquete(5), paquete(6)]
        self.al_inyectar = None
        self.extra = {k: 0 for k in envio.CLAVES_RESUMEN}   # lo que el anillo ya olvidó (acumulado)
        self.sin_contadores = False                          # simula un shim viejo sin el verbo

    class Linea(socketserver.StreamRequestHandler):
        def handle(self):
            s = self.server
            linea = self.rfile.readline().decode().strip()
            s.recibidas.append(linea)
            cmd, _, resto = linea.partition(" ")
            if cmd == "CODIGO":
                r = s.codigo or {"codigo": None}
            elif cmd == "PAQUETES":
                r = {"codigo": "hamming74", "n": 7, "k": 4, "lado": "dn", "armada": None,
                     "paquetes": [p for p in s.paquetes if p["id"] > int(resto)]}   # exclusivo, como el real
            elif cmd == "CONTADORES" and not s.sin_contadores:
                r = {k: v + s.extra[k] for k, v in envio.resumen_fec(s.paquetes).items()}
                r.update(ok=True, ultimo_id=max((p["id"] for p in s.paquetes), default=0))
            elif cmd == "INYECTAR":
                if s.al_inyectar:
                    s.al_inyectar()
                r = {"ok": True, "armada": json.loads(resto)}
            else:
                r = {"error": "?"}
            self.wfile.write(json.dumps(r).encode())


JSON = {"Content-Type": "application/json"}


def lanzar(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shim = lanzar(ShimFalso())
        cls.http = lanzar(envio.ThreadingHTTPServer(("127.0.0.1", 0), envio.Pagina))
        cls.url = f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.viejo = envio.PUERTO_FEC
        envio.PUERTO_FEC = cls.shim.server_address[1]

    @classmethod
    def tearDownClass(cls):
        envio.PUERTO_FEC = cls.viejo
        for s in (cls.shim, cls.http):
            s.shutdown(); s.server_close()

    def pedir(self, ruta, cuerpo=None, cabeceras=None):
        req = urllib.request.Request(self.url + ruta, data=cuerpo, headers=cabeceras or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class Endpoints(Base):
    def test_codigo(self):
        st, b = self.pedir("/fec/codigo")
        j = json.loads(b)
        self.assertEqual(st, 200)
        self.assertEqual((j["codigo"], j["n"], j["k"], j["H"]), ("hamming74", 7, 4, H))

    def test_paquetes_desde(self):
        # `desde` es EXCLUSIVO (id > desde), como el shim real: desde=5 devuelve solo el 6
        st, b = self.pedir("/fec/paquetes?desde=5")
        self.assertEqual(st, 200)
        self.assertEqual([p["id"] for p in json.loads(b)["paquetes"]], [6])
        self.assertIn("PAQUETES 5", self.shim.recibidas)
        self.assertEqual(json.loads(self.pedir("/fec/paquetes?desde=6")[1])["paquetes"], [])

    def test_paquetes_desde_invalido(self):
        for malo in ("abc", "-1", "1;CODIGO", "%C2%B2", "%D9%A3", "1" * 16):   # «²» y «٣» son isdigit() pero no ASCII
            st, _ = self.pedir("/fec/paquetes?desde=" + malo)
            self.assertEqual(st, 400, malo)

    def test_inyectar_bits_y_posiciones(self):
        st, b = self.pedir("/fec/inyectar", json.dumps({"bits": 2, "semilla": 7}).encode(), JSON)
        self.assertEqual((st, json.loads(b)["ok"]), (200, True))
        self.assertEqual(self.shim.recibidas[-1], 'INYECTAR {"bits": 2, "semilla": 7}')
        st, b = self.pedir("/fec/inyectar", json.dumps({"posiciones": [8, 20]}).encode(), JSON)
        self.assertEqual(st, 200)

    def test_inyectar_rechazos(self):
        n = len(self.shim.recibidas)
        malos = [{"bits": 0}, {"bits": 9}, {"bits": True}, {"bits": "2"}, {"bits": 1, "posiciones": [8]}, {},
                 {"posiciones": []}, {"posiciones": [7]}, {"posiciones": list(range(8, 17))},
                 {"posiciones": ["8"]}, {"posiciones": [8.5]}, {"bits": 1, "semilla": "x"}, [1], "x"]
        for m in malos:
            st, _ = self.pedir("/fec/inyectar", json.dumps(m).encode(), JSON)
            self.assertEqual(st, 400, m)
        self.assertEqual(self.pedir("/fec/inyectar", b"no es json", JSON)[0], 400)
        self.assertEqual(self.pedir("/fec/inyectar", b"{" + b" " * 2000 + b"}", JSON)[0], 413)
        self.assertEqual(len(self.shim.recibidas), n, "nada inválido debe llegar al shim")

    def test_csrf_exige_content_type_json(self):
        n = len(self.shim.recibidas)
        cuerpo = json.dumps({"bits": 1}).encode()
        for cab in ({}, {"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"},
                    {"Content-Type": "multipart/form-data; boundary=x"}):
            st, _ = self.pedir("/fec/inyectar", cuerpo, cab)
            self.assertEqual(st, 415, cab)
        self.assertEqual(len(self.shim.recibidas), n, "un POST de formulario no debe armar el canal")
        st, _ = self.pedir("/fec/inyectar", cuerpo, {"Content-Type": "Application/JSON; charset=utf-8"})
        self.assertEqual(st, 200)

    def test_cifras_no_ascii_rechazadas(self):
        for v in ("²", "³", "¹"):   # latin-1 (las cabeceras HTTP lo exigen), isdigit() True, no ASCII
            st, _ = self.pedir("/enviar", b"hola", {"X-Inyectar": v})
            self.assertEqual(st, 400, v)
        self.assertEqual(self.pedir("/enviar", b"hola", {"X-Inyectar": "1" * 5000})[0], 400)

    def test_content_length_no_ascii_no_revienta(self):
        sck = socket.create_connection(("127.0.0.1", self.http.server_address[1]), timeout=10)
        crlf = "\r\n"
        with sck:
            sck.sendall(crlf.join(["POST /fec/inyectar HTTP/1.1", "Host: x", "Content-Type: application/json",
                                   "Content-Length: ²", "", ""]).encode("utf-8"))
            self.assertIn(b" 413 ", sck.recv(4096).split(crlf.encode())[0])

    def test_x_inyectar_invalido(self):
        for v in ("0", "9", "x", "-1"):
            st, _ = self.pedir("/enviar", b"hola", {"X-Inyectar": v})
            self.assertEqual(st, 400, v)

    def test_resumen(self):
        r = envio.resumen_fec([paquete(1), paquete(2, "corregido", [9]), paquete(3, "no_corregible", [9, 10]),
                               paquete(4, "invalido", [8])])
        self.assertEqual(r, {"paquetes": 4, "con_error_inyectado": 3, "sindromes_no_cero": 3, "corregidas": 1,
                             "no_corregibles": 1, "invalidos": 1})

    def test_resumen_corregido_mal_es_opcional(self):
        mal = paquete(5, "corregido_mal", [9, 10])
        self.assertEqual(envio.resumen_fec([paquete(1), mal])["corregidos_mal"], 1)
        self.assertNotIn("corregidos_mal", envio.resumen_fec([paquete(1)]))       # compatibilidad: sin clave si no hubo
        a = {k: 0 for k in envio.CLAVES_RESUMEN}
        d = {k: 3 for k in envio.CLAVES_RESUMEN}
        self.assertNotIn("corregidos_mal", envio.resumen_diferencia(a, d))         # shim viejo sin la clave
        self.assertEqual(envio.resumen_diferencia({**a, "corregidos_mal": 1}, {**d, "corregidos_mal": 4})["corregidos_mal"], 3)

    def test_ui_tiene_el_estado_nuevo(self):
        src = Path(envio.__file__).read_text(encoding="utf-8")
        for t in ('value="corregido_mal"', "corregido_mal:'corrigió mal'", "--c-cmal", "#linea button.corregido_mal"):
            self.assertIn(t, src)


class SinShim(unittest.TestCase):
    def test_503_y_disponible_false(self):
        http = lanzar(envio.ThreadingHTTPServer(("127.0.0.1", 0), envio.Pagina))
        s = socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
        libre = s.server_address[1]; s.server_close()           # puerto sin nadie escuchando
        viejo, envio.PUERTO_FEC = envio.PUERTO_FEC, libre
        try:
            self.assertEqual(envio.fec_consulta("CODIGO"), {"disponible": False})
            base = f"http://127.0.0.1:{http.server_address[1]}"
            for ruta, cuerpo in (("/fec/codigo", None), ("/fec/paquetes?desde=0", None), ("/fec/inyectar", b'{"bits":1}')):
                with self.assertRaises(urllib.error.HTTPError) as c:
                    urllib.request.urlopen(urllib.request.Request(base + ruta, data=cuerpo, headers=JSON), timeout=10)
                self.assertEqual(c.exception.code, 503, ruta)
                self.assertFalse(json.loads(c.exception.read())["disponible"])
        finally:
            envio.PUERTO_FEC = viejo; http.shutdown(); http.server_close()


class Pagina(Base):
    def test_html_con_seccion_y_sin_innerhtml_en_el_laboratorio(self):
        st, b = self.pedir("/")
        html = b.decode()
        self.assertEqual(st, 200)
        for t in ("Laboratorio de síndrome", "Inyectar 1 bit", "Inyectar 2 bits", "bit exacto", "fec/paquetes",
                  "d_min = 3", "no compara contra el original", "recepción del shim de cmp-dn",
                  "se numeran desde 1", "columna ${j+1} de H", "r.fec.truncado", "'Content-Type':'application/json'"):
            self.assertIn(t, html)
        lab = html[html.index("Laboratorio de síndrome: todo texto"):]
        self.assertNotIn("innerHTML", lab.replace("(nunca innerHTML)", ""))
        self.assertIn("textContent", lab)


class EnviarConFec(Base):
    """/enviar completo con un eco falso: añade el bloque fec con los paquetes nuevos."""

    def enviar(self, cabeceras):
        eco = socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)

        def atender():
            c, _ = eco.get_request()
            with c:
                buf = b""
                while len(buf) < 8:
                    buf += c.recv(8 - len(buf))
                n = struct.unpack("!Q", buf)[0]; datos = b""
                while len(datos) < n:
                    datos += c.recv(n - len(datos))
                c.sendall(datos); c.recv(1)
                c.sendall(struct.pack("!I", 4) + b"null")
        threading.Thread(target=atender, daemon=True).start()
        v = (envio.DESTINO, envio.PUERTO_ECO, envio.PUERTO_CONTADORES, envio.PUSHGATEWAY)
        envio.DESTINO, envio.PUERTO_ECO = "127.0.0.1", eco.server_address[1]
        envio.PUERTO_CONTADORES, envio.PUSHGATEWAY = 1, "http://127.0.0.1:1"
        try:
            st, b = self.pedir("/enviar", b"datos de prueba" * 10, {"X-Nombre": "t.txt", **cabeceras})
        finally:
            envio.DESTINO, envio.PUERTO_ECO, envio.PUERTO_CONTADORES, envio.PUSHGATEWAY = v
            self.shim.al_inyectar = None; eco.server_close()
        return st, json.loads(b)

    def setUp(self):
        self.shim.paquetes = [paquete(5), paquete(6)]
        self.shim.extra = {k: 0 for k in envio.CLAVES_RESUMEN}
        self.shim.sin_contadores = False

    def test_enviar_con_x_inyectar(self):
        self.shim.al_inyectar = lambda: self.shim.paquetes.extend([paquete(7, "corregido", [9]),
                                                                   paquete(8, "no_corregible", [9, 10])])
        st, j = self.enviar({"X-Inyectar": "2"})
        self.assertEqual(st, 200, j)
        self.assertTrue(j["identico"])
        self.assertEqual([p["id"] for p in j["fec"]["paquetes"]], [7, 8])
        self.assertEqual(j["fec"]["resumen"], {"paquetes": 2, "con_error_inyectado": 2, "sindromes_no_cero": 2,
                                               "corregidas": 1, "no_corregibles": 1, "invalidos": 0})
        self.assertEqual((j["fec"]["resumen_fuente"], j["fec"]["truncado"]), ("contadores", False))
        self.assertTrue(j["fec"]["sha_identico"])
        self.assertIn('INYECTAR {"bits": 2}', self.shim.recibidas)

    def test_archivo_grande_el_resumen_sale_de_contadores_no_del_anillo(self):
        # ~28 000 paquetes: el anillo de 200 solo conserva los últimos; el único con error inyectado ya salió.
        def mucho():
            self.shim.paquetes = [paquete(i) for i in range(300, 500)]
            self.shim.extra.update(paquetes=27800, con_error_inyectado=1, sindromes_no_cero=1, corregidas=1)
        self.shim.al_inyectar = mucho
        st, j = self.enviar({"X-Inyectar": "1"})
        self.assertEqual(st, 200, j)
        f = j["fec"]
        self.assertEqual(f["resumen"], {"paquetes": 27998, "con_error_inyectado": 1, "sindromes_no_cero": 1,
                                        "corregidas": 1, "no_corregibles": 0, "invalidos": 0})
        self.assertTrue(f["truncado"])
        self.assertEqual(len(f["paquetes"]), 200)

    def test_shim_sin_contadores_cae_al_anillo_y_lo_dice(self):
        self.shim.sin_contadores = True
        self.shim.al_inyectar = lambda: self.shim.paquetes.extend([paquete(7, "corregido", [9])])
        st, j = self.enviar({"X-Inyectar": "1"})
        self.assertEqual(st, 200, j)
        self.assertEqual((j["fec"]["resumen_fuente"], j["fec"]["resumen"]["paquetes"], j["fec"]["truncado"]),
                         ("anillo", 1, False))

    def test_contadores_del_shim_falso_son_acumulados(self):
        c = envio._contadores_fec()
        self.assertEqual((c["paquetes"], c["ultimo_id"]), (2, 6))
        self.shim.sin_contadores = True
        self.assertIsNone(envio._contadores_fec())

    def test_fec_apagado_enviar_no_trae_bloque_fec(self):
        self.shim.codigo, viejo = None, self.shim.codigo
        n = len(self.shim.recibidas)
        try:
            st, j = self.enviar({})
        finally:
            self.shim.codigo = viejo
        self.assertEqual(st, 200, j)
        self.assertTrue(j["identico"])
        self.assertNotIn("fec", j)
        self.assertEqual([x.split()[0] for x in self.shim.recibidas[n:]], ["CODIGO"], "solo se consulta el código")

    def test_fec_apagado_codigo_null(self):
        self.shim.codigo, viejo = None, self.shim.codigo
        try:
            self.assertFalse(envio.fec_consulta("CODIGO").get("codigo"))
            st, b = self.pedir("/fec/codigo")
            self.assertEqual((st, json.loads(b)["codigo"]), (200, None))
        finally:
            self.shim.codigo = viejo


class CanalYSecded(Base):
    """Pestaña de síndrome: canal aleatorio visible, «doble detectado» con SECDED y la ayuda de cambio de código."""

    def test_codigo_devuelve_el_canal_activo(self):
        viejo = self.shim.codigo
        self.shim.codigo = {**CODIGO, "codigo": "secded_8_4", "canal": "bsc:0.001"}
        try:
            st, b = self.pedir("/fec/codigo")
        finally:
            self.shim.codigo = viejo
        j = json.loads(b)
        self.assertEqual((st, j["codigo"], j["canal"]), (200, "secded_8_4", "bsc:0.001"))

    def test_codigo_sin_canal_es_null(self):
        viejo = self.shim.codigo
        self.shim.codigo = {**CODIGO, "canal": None}           # el shim real: canal null = sin canal aleatorio
        try:
            st, b = self.pedir("/fec/codigo")
        finally:
            self.shim.codigo = viejo
        self.assertEqual((st, json.loads(b)["canal"]), (200, None))

    def test_pagina_tiene_canal_doble_detectado_y_ayuda(self):
        st, b = self.pedir("/")
        html = b.decode()
        self.assertEqual(st, 200)
        for t in ("doble detectado", "Canal aleatorio", "apagado (solo inyección manual)",
                  "SECDED detecta 2 errores y no los corrige mal", "lab5g.sh env-set", "secded_8_4",
                  "reiniciar cmp-ue y cmp-dn"):
            self.assertIn(t, html, t)
        self.assertIn("function etiqueta(est)", html)
        self.assertIn("'no corregible'", html)                  # el resto de códigos sigue diciendo «no corregible»

    def test_el_canal_y_el_codigo_se_insertan_como_texto(self):
        st, b = self.pedir("/")
        lab = b.decode()
        lab = lab[lab.index("Laboratorio de síndrome: todo texto"):]
        self.assertIn("'Canal aleatorio: '+String(j.canal)", lab)
        self.assertNotIn("j.canal)}", lab)                      # nada de plantillas HTML con datos del shim


class Automatico(Base):
    """FEC=auto: /fec/codigo pasa tal cual los campos del shim y la pestaña los muestra si existen."""

    AUTO = {**CODIGO, "codigo": "secded_8_4", "auto": True, "codigo_activo": "secded_8_4", "ber_estimado": 0.0012,
            "escalera": ["hamming_15_11", "secded_16_11", "secded_8_4"]}

    def con_codigo(self, codigo):
        viejo = self.shim.codigo
        self.shim.codigo = codigo
        try:
            return self.pedir("/fec/codigo")
        finally:
            self.shim.codigo = viejo

    def test_codigo_auto_pasa_los_campos_nuevos(self):
        st, b = self.con_codigo(self.AUTO)
        j = json.loads(b)
        self.assertEqual(st, 200)
        self.assertEqual((j["auto"], j["codigo_activo"], j["ber_estimado"], j["escalera"]),
                         (True, "secded_8_4", 0.0012, ["hamming_15_11", "secded_16_11", "secded_8_4"]))

    def test_codigo_fijo_no_trae_campos_auto(self):
        st, b = self.con_codigo(CODIGO)                  # auto=false: la respuesta es la de siempre
        j = json.loads(b)
        self.assertEqual((st, j["codigo"]), (200, "hamming74"))
        self.assertNotIn("auto", j)
        self.assertNotIn("escalera", j)

    def test_codigo_auto_sin_campos_nuevos_no_rompe_la_pagina(self):
        # shim intermedio que dice auto=true pero aún sin BER ni escalera: el JS usa .get-equivalentes (||)
        st, b = self.con_codigo({**CODIGO, "auto": True})
        self.assertEqual(st, 200)
        self.assertIsNone(json.loads(b).get("ber_estimado"))
        html = self.pedir("/")[1].decode()
        self.assertIn("(j.escalera||[]).join(' → ')||'—'", html)
        self.assertIn("typeof j.ber_estimado==='number'", html)

    def test_pagina_muestra_modo_automatico_y_nota(self):
        html = self.pedir("/")[1].decode()
        for t in ("Modo automático: código activo ", "BER estimado ", "(escalera: ",
                  "El receptor mide el BER con los síndromes y se lo informa al emisor cada ~2 s, con histéresis",
                  "j.auto?bloqueAuto(j):null", "lab.codigo.auto&&++lab.n%8===0"):
            self.assertIn(t, html, t)
        self.assertIn("const piezas=[", html)


if __name__ == "__main__":
    unittest.main()
