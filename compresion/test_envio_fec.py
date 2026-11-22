"""Pruebas del laboratorio de síndrome de envio.py con un shim FALSO (servidor de línea en un hilo)
y el servidor HTTP real de envio.py en un puerto efímero. Solo biblioteca estándar."""
import json, os, socketserver, struct, sys, threading, unittest, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
                     "paquetes": [p for p in s.paquetes if p["id"] >= int(resto)]}
            elif cmd == "INYECTAR":
                if s.al_inyectar:
                    s.al_inyectar()
                r = {"ok": True, "armada": json.loads(resto)}
            else:
                r = {"error": "?"}
            self.wfile.write(json.dumps(r).encode())


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
        st, b = self.pedir("/fec/paquetes?desde=6")
        self.assertEqual(st, 200)
        self.assertEqual([p["id"] for p in json.loads(b)["paquetes"]], [6])
        self.assertIn("PAQUETES 6", self.shim.recibidas)

    def test_paquetes_desde_invalido(self):
        for malo in ("abc", "-1", "1;CODIGO"):
            st, _ = self.pedir("/fec/paquetes?desde=" + malo)
            self.assertEqual(st, 400, malo)

    def test_inyectar_bits_y_posiciones(self):
        st, b = self.pedir("/fec/inyectar", json.dumps({"bits": 2, "semilla": 7}).encode())
        self.assertEqual((st, json.loads(b)["ok"]), (200, True))
        self.assertEqual(self.shim.recibidas[-1], 'INYECTAR {"bits": 2, "semilla": 7}')
        st, b = self.pedir("/fec/inyectar", json.dumps({"posiciones": [8, 20]}).encode())
        self.assertEqual(st, 200)

    def test_inyectar_rechazos(self):
        n = len(self.shim.recibidas)
        malos = [{"bits": 0}, {"bits": 9}, {"bits": True}, {"bits": "2"}, {"bits": 1, "posiciones": [8]}, {},
                 {"posiciones": []}, {"posiciones": [7]}, {"posiciones": list(range(8, 17))},
                 {"posiciones": ["8"]}, {"posiciones": [8.5]}, {"bits": 1, "semilla": "x"}, [1], "x"]
        for m in malos:
            st, _ = self.pedir("/fec/inyectar", json.dumps(m).encode())
            self.assertEqual(st, 400, m)
        self.assertEqual(self.pedir("/fec/inyectar", b"no es json")[0], 400)
        self.assertEqual(self.pedir("/fec/inyectar", b"{" + b" " * 2000 + b"}")[0], 413)
        self.assertEqual(len(self.shim.recibidas), n, "nada inválido debe llegar al shim")

    def test_x_inyectar_invalido(self):
        for v in ("0", "9", "x", "-1"):
            st, _ = self.pedir("/enviar", b"hola", {"X-Inyectar": v})
            self.assertEqual(st, 400, v)

    def test_resumen(self):
        r = envio.resumen_fec([paquete(1), paquete(2, "corregido", [9]), paquete(3, "no_corregible", [9, 10]),
                               paquete(4, "invalido", [8])])
        self.assertEqual(r, {"paquetes": 4, "con_error_inyectado": 3, "sindromes_no_cero": 3, "corregidas": 1,
                             "no_corregibles": 1, "invalidos": 1})


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
                    urllib.request.urlopen(urllib.request.Request(base + ruta, data=cuerpo), timeout=10)
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
                  "d_min = 3", "no compara contra el original", "recepción del shim de cmp-dn"):
            self.assertIn(t, html)
        lab = html[html.index("Laboratorio de síndrome: todo texto"):]
        self.assertNotIn("innerHTML", lab.replace("(nunca innerHTML)", ""))
        self.assertIn("textContent", lab)


class EnviarConFec(Base):
    """/enviar completo con un eco falso: añade el bloque fec con los paquetes nuevos."""
    def test_enviar_con_x_inyectar(self):
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
        self.shim.paquetes = [paquete(5), paquete(6)]
        self.shim.al_inyectar = lambda: self.shim.paquetes.extend([paquete(7, "corregido", [9]),
                                                                   paquete(8, "no_corregible", [9, 10])])
        v = (envio.DESTINO, envio.PUERTO_ECO, envio.PUERTO_CONTADORES, envio.PUSHGATEWAY)
        envio.DESTINO, envio.PUERTO_ECO = "127.0.0.1", eco.server_address[1]
        envio.PUERTO_CONTADORES, envio.PUSHGATEWAY = 1, "http://127.0.0.1:1"
        try:
            st, b = self.pedir("/enviar", b"datos de prueba" * 10, {"X-Nombre": "t.txt", "X-Inyectar": "2"})
        finally:
            envio.DESTINO, envio.PUERTO_ECO, envio.PUERTO_CONTADORES, envio.PUSHGATEWAY = v
            self.shim.al_inyectar = None; eco.server_close()
        j = json.loads(b)
        self.assertEqual(st, 200, j)
        self.assertTrue(j["identico"])
        self.assertEqual([p["id"] for p in j["fec"]["paquetes"]], [7, 8])
        self.assertEqual(j["fec"]["resumen"], {"paquetes": 2, "con_error_inyectado": 2, "sindromes_no_cero": 2,
                                               "corregidas": 1, "no_corregibles": 1, "invalidos": 0})
        self.assertTrue(j["fec"]["sha_identico"])
        self.assertIn('INYECTAR {"bits": 2}', self.shim.recibidas)

    def test_fec_apagado_no_cambia_el_resultado(self):
        self.shim.codigo, viejo = None, self.shim.codigo
        try:
            self.assertFalse(envio.fec_consulta("CODIGO").get("codigo"))
            st, b = self.pedir("/fec/codigo")
            self.assertEqual((st, json.loads(b)["codigo"]), (200, None))
        finally:
            self.shim.codigo = viejo


if __name__ == "__main__":
    unittest.main()
