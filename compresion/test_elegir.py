"""Elegir el algoritmo FEC en caliente (dinamico=True): dos CapaFec en bucle local con reloj simulado,
la trama de control DN→UE, los verbos ELEGIR/CODIGO y el endpoint POST /fec/elegir de envio.py contra un
shim real (un CapaFec tras un servidor de línea). Sin TUN, sin red salvo 127.0.0.1, sin sleeps."""
from __future__ import annotations

import json
import random
import socketserver
import sys
from http.server import ThreadingHTTPServer
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adaptativo  # noqa: E402
import envio  # noqa: E402
import fec  # noqa: E402
from registro_fec import (ELEGIR_CADA_S, MARCA_ELEGIR, MARCA_INFORME, MARCA_NACK, MTU_ENLACE, OPCIONES_ELEGIR,  # noqa: E402
                          CODIGOS_ELEGIBLES, TUNEL, CapaFec, codificar_eleccion, decodificar_eleccion,
                          es_dinamico, es_eleccion, mtu_con_fec)

PAYLOAD = 100


def carga(i: int, largo: int = PAYLOAD) -> bytes:
    """Carga determinista: el primer byte es un id de códec cualquiera."""
    r = random.Random(7000 + i)
    return bytes([0x80 | 3]) + bytes(r.randrange(256) for _ in range(largo - 1))


def entregar(rx: CapaFec, marco: bytes):
    """Recibe un marco FEC y devuelve (paquetes comprimidos que llegaron, entrada de registro, excepción)."""
    salida: list[bytes] = []

    def procesar(comprimido: bytes) -> int:
        salida.append(comprimido)
        return len(comprimido)

    entrada, error = rx.recibir(marco, procesar)
    return salida, entrada, error


def par(codigo_dn: str = "hamming_15_11", codigo_ue: str = "hamming_15_11"):
    """(dn, ue) dinámicos con reloj compartido `t` (lista de un float que mueve el test)."""
    t = [0.0]
    dn = CapaFec(codigo_dn, "dn", reloj=lambda: t[0], dinamico=True)
    ue = CapaFec(codigo_ue, "ue", reloj=lambda: t[0], dinamico=True)
    return dn, ue, t


class TestControlEnElCable(unittest.TestCase):
    def test_codificar_y_decodificar_cada_opcion(self):
        for nombre in OPCIONES_ELEGIR:
            trama = codificar_eleccion(nombre)
            self.assertEqual(len(trama), 3)
            self.assertTrue(es_eleccion(trama))
            self.assertEqual(decodificar_eleccion(trama), nombre)

    def test_decodificar_rechaza_largo_marca_crc_e_indice(self):
        buena = codificar_eleccion("secded_8_4")
        with self.assertRaisesRegex(ValueError, "3"):
            decodificar_eleccion(buena[:2])
        with self.assertRaisesRegex(ValueError, "no es una trama"):
            decodificar_eleccion(bytes([MARCA_NACK]) + buena[1:])
        with self.assertRaisesRegex(ValueError, "CRC8"):
            decodificar_eleccion(buena[:2] + bytes([buena[2] ^ 0x01]))
        fuera = bytes([MARCA_ELEGIR, len(OPCIONES_ELEGIR)])
        with self.assertRaisesRegex(ValueError, "fuera de rango"):
            decodificar_eleccion(fuera + bytes([adaptativo.crc8(fuera)]))   # CRC bien, índice malo
        with self.assertRaises(ValueError):
            codificar_eleccion("repeticion_5_1")

    def test_constantes_no_chocan_con_ids_ni_keepalive(self):
        self.assertEqual(MARCA_ELEGIR, 0xFD)
        self.assertNotIn(MARCA_ELEGIR, fec.CODIGOS)
        # El KEEPALIVE (1 B) es 0xFF = MARCA_INFORME a propósito: el shim lo filtra antes que los informes.
        # shim.py no se importa aquí (usa fcntl, solo Linux): se comprueba su fuente.
        src = Path(__file__).with_name("shim.py").read_text(encoding="utf-8")
        self.assertIn('KEEPALIVE = b"\\xff"', src)
        self.assertEqual(len({MARCA_ELEGIR, MARCA_NACK, MARCA_INFORME}), 3)
        self.assertFalse(es_eleccion(bytes([MARCA_INFORME])))
        self.assertFalse(es_eleccion(bytes([MARCA_NACK, 1])))

    def test_opciones_de_envio_son_las_del_shim(self):
        self.assertEqual(envio.OPCIONES_ELEGIR, OPCIONES_ELEGIR)
        self.assertEqual(CODIGOS_ELEGIBLES, OPCIONES_ELEGIR[1:])

    def test_es_dinamico_solo_para_opciones(self):
        for v in OPCIONES_ELEGIR:
            self.assertTrue(es_dinamico(v))
        self.assertTrue(es_dinamico(" AUTO "))
        self.assertFalse(es_dinamico("repeticion_5_1"))   # conserva su MTU de siempre
        self.assertFalse(es_dinamico(None))


class TestEleccionEnElBucle(unittest.TestCase):
    def test_elegir_secded_en_dn_y_el_ue_decodifica(self):
        dn, ue, _ = par("hamming_15_11", "hamming_15_11")
        dn.elegir("secded_8_4")
        marco = dn.emitir(carga(0))
        self.assertEqual(marco[0], fec.buscar("secded_8_4").id)
        salida, entrada, error = entregar(ue, marco)
        self.assertIsNone(error)
        self.assertEqual(salida, [carga(0)])
        self.assertEqual(entrada["codigo"], "secded_8_4")     # el marco se cuenta con su código real

    def test_trama_de_control_sale_de_dn_y_se_repite_cada_10_s(self):
        dn, ue, _ = par()
        dn.responder("ELEGIR secded_8_4")
        primera = dn.eleccion_pendiente(0.0)
        self.assertEqual(primera, codificar_eleccion("secded_8_4"))      # de inmediato
        self.assertIsNone(dn.eleccion_pendiente(ELEGIR_CADA_S / 2))       # no antes de 10 s
        self.assertEqual(dn.eleccion_pendiente(ELEGIR_CADA_S), primera)   # y se repite a los 10 s
        self.assertIsNone(ue.eleccion_pendiente(0.0))                     # solo sale del lado dn

    def test_ue_aplica_la_trama_y_entra_en_auto(self):
        dn, ue, _ = par()
        self.assertTrue(ue.recibir_eleccion(codificar_eleccion("auto")))
        self.assertTrue(ue.auto)
        self.assertEqual(ue.codigo.nombre, "secded_8_4")
        self.assertEqual(ue.eleccion, "auto")
        self.assertEqual(ue.elegir_stats["recibida"], 1)
        self.assertTrue(ue.recibir_eleccion(codificar_eleccion("hamming_7_4")))
        self.assertFalse(ue.auto)                                         # una elección fija saca de auto
        self.assertEqual(ue.codigo.nombre, "hamming_7_4")

    def test_reinicio_del_ue_recibe_la_eleccion_repetida(self):
        dn, _, t = par()
        dn.responder("ELEGIR hamming_7_4")
        dn.eleccion_pendiente(0.0)
        nuevo_ue = CapaFec("hamming_15_11", "ue", reloj=lambda: t[0], dinamico=True)   # el UE acaba de arrancar
        self.assertEqual(nuevo_ue.codigo.nombre, "hamming_15_11")
        t[0] = ELEGIR_CADA_S
        nuevo_ue.recibir_eleccion(dn.eleccion_pendiente(t[0]))
        self.assertEqual(nuevo_ue.codigo.nombre, "hamming_7_4")

    def test_trama_con_crc_malo_no_cambia_nada_y_cuenta_mala(self):
        dn, ue, _ = par("secded_8_4", "secded_8_4")
        mala = bytearray(codificar_eleccion("auto"))
        mala[2] ^= 0x01
        self.assertFalse(ue.recibir_eleccion(bytes(mala)))
        self.assertFalse(ue.auto)
        self.assertEqual(ue.codigo.nombre, "secded_8_4")
        self.assertEqual(ue.elegir_stats["mala"], 1)
        self.assertIn('lab5g_cmp_fec_elegir_total{lado="ue",tipo="mala"} 1', ue.texto('lado="ue"'))

    def test_elegir_auto_reinicia_la_politica(self):
        dn, _, _ = par()
        dn.elegir("auto")
        politica = dn.politica
        dn.elegir("hamming_7_4")
        dn.elegir("auto")
        self.assertIsNot(dn.politica, politica)
        self.assertTrue(dn.auto)
        self.assertEqual(dn.codigo.nombre, "secded_8_4")


class TestValidacionYNoDinamico(unittest.TestCase):
    def test_nombre_invalido_lista_las_validas(self):
        dn, _, _ = par()
        with self.assertRaises(ValueError) as cm:
            dn.elegir("cosa")
        for nombre in OPCIONES_ELEGIR:
            self.assertIn(nombre, str(cm.exception))

    def test_sin_dinamico_elegir_falla_y_no_informa(self):
        fijo = CapaFec("hamming_7_4", "dn")
        with self.assertRaisesRegex(ValueError, "no es dinámico"):
            fijo.elegir("secded_8_4")
        self.assertIsNone(fijo.estimador)
        self.assertIsNone(fijo.informe_pendiente(10.0))
        self.assertFalse(fijo.recibir_informe(bytes(12)))
        self.assertFalse(fijo.recibir_eleccion(codificar_eleccion("auto")))
        self.assertEqual(fijo.codigo.nombre, "hamming_7_4")
        r = fijo.responder("ELEGIR secded_8_4")
        self.assertEqual((r["ok"], r["dinamico"]), (False, False))
        self.assertIsNone(fijo.eleccion_pendiente(0.0))

    def test_mtu_con_dinamico_cabe_cualquier_opcion(self):
        self.assertEqual(CapaFec("hamming_7_4", "ue", dinamico=True).mtu(), 677)
        self.assertEqual(CapaFec("hamming_7_4", "ue").mtu(), 780)
        self.assertEqual(mtu_con_fec("hamming_7_4"), 780)

    def test_marco_hamming_7_4_de_mtu_cabe_en_el_enlace(self):
        tx = CapaFec("auto", "ue", dinamico=True)
        tx.elegir("hamming_7_4")
        marco = tx.emitir(bytes(tx.mtu()))
        self.assertLessEqual(len(marco), MTU_ENLACE - TUNEL)

    def test_informes_salen_de_un_receptor_que_no_esta_en_auto(self):
        dn, ue, t = par("hamming_15_11", "hamming_15_11")
        self.assertFalse(dn.auto)
        for i in range(3):
            entregar(dn, ue.emitir(carga(i)))
        inf = dn.informe_pendiente(10.0)
        self.assertIsNotNone(inf)
        self.assertEqual(inf[0], MARCA_INFORME)
        self.assertEqual(dn.informes["enviados"], 1)

    def test_informe_recibido_en_no_auto_no_cambia_el_codigo(self):
        dn, ue, _ = par("hamming_15_11", "hamming_15_11")
        self.assertFalse(dn.recibir_informe(bytes(12)))
        self.assertEqual(dn.codigo.nombre, "hamming_15_11")
        self.assertEqual(dn.informes["malos"], 0)     # ignorado, no contado como malo
        self.assertEqual(dn.informes["recibidos"], 0)

    def test_informe_recibido_en_auto_si_cambia(self):
        dn, ue, _ = par("hamming_15_11", "hamming_15_11")
        for i in range(3):                       # ue recibe de dn y mide; su informe va hacia dn
            entregar(ue, dn.emitir(carga(i)))
        inf = ue.informe_pendiente(10.0)
        self.assertIsNotNone(inf)
        self.assertFalse(dn.recibir_informe(inf[1:]))   # dn no está en auto: lo ignora
        self.assertEqual(dn.informes["recibidos"], 0)
        dn.elegir("auto")
        self.assertTrue(dn.recibir_informe(inf[1:]))    # en auto sí lo aplica
        self.assertEqual(dn.informes["recibidos"], 1)


class TestMetricasYVerbos(unittest.TestCase):
    def test_texto_solo_con_dinamico_tiene_las_series_nuevas(self):
        dinamico = CapaFec("hamming_7_4", "dn", dinamico=True).texto('lado="dn"')
        for c in CODIGOS_ELEGIBLES:
            self.assertIn(f'lab5g_cmp_fec_codigo_activo{{lado="dn",codigo="{c}"}}', dinamico)
        self.assertIn('lab5g_cmp_fec_codigo_activo{lado="dn",codigo="hamming_7_4"} 1', dinamico)
        self.assertIn('lab5g_cmp_fec_codigo_activo{lado="dn",codigo="secded_8_4"} 0', dinamico)
        for tipo in ("elegida_local", "recibida", "mala"):
            self.assertIn(f'lab5g_cmp_fec_elegir_total{{lado="dn",tipo="{tipo}"}} 0', dinamico)
        fijo = CapaFec("hamming_7_4", "dn").texto('lado="dn"')
        self.assertNotIn("elegir_total", fijo)
        self.assertNotIn("codigo_activo", fijo)

    def test_dinamico_precarga_series_de_los_cuatro_codigos(self):
        lineas = CapaFec("auto", "dn", dinamico=True).texto('lado="dn"').splitlines()
        for nombre, n_sind in (("hamming_7_4", 7), ("hamming_15_11", 15), ("secded_16_11", 31), ("secded_8_4", 15)):
            sind = [x for x in lineas if x.startswith("lab5g_cmp_fec_sindrome_total") and f'codigo="{nombre}"' in x]
            self.assertEqual(len(sind), n_sind, nombre)
            self.assertTrue(all(x.endswith(" 0") for x in sind))

    def test_responder_elegir_y_codigo(self):
        dn, ue, _ = par()
        r = dn.responder("ELEGIR hamming_7_4")
        self.assertEqual(r, {"ok": True, "codigo_activo": "hamming_7_4", "auto": False, "eleccion": "hamming_7_4"})
        self.assertEqual(dn.eleccion_pendiente(0.0), codificar_eleccion("hamming_7_4"))
        self.assertEqual(dn.elegir_stats["elegida_local"], 1)
        ue_r = ue.responder("ELEGIR secded_8_4")                          # el UE no reenvía nada
        self.assertTrue(ue_r["ok"])
        self.assertIsNone(ue.eleccion_pendiente(0.0))
        bad = dn.responder("ELEGIR cosa")
        self.assertEqual((bad["ok"], bad["dinamico"]), (False, True))
        self.assertIn("hamming_15_11", bad["error"])
        c = dn.responder("CODIGO")
        self.assertEqual((c["dinamico"], c["opciones"], c["eleccion"]), (True, list(OPCIONES_ELEGIR), "hamming_7_4"))
        c_fijo = CapaFec("repeticion_5_1", "dn").responder("CODIGO")
        self.assertEqual((c_fijo["dinamico"], c_fijo["opciones"], c_fijo["eleccion"]), (False, None, None))
        self.assertEqual(c_fijo["codigo"], "repeticion_5_1")


class _ShimLinea(socketserver.ThreadingTCPServer):
    """Shim de mentira: responde cada línea con `self.capa.responder` (un CapaFec real)."""
    allow_reuse_address = True
    daemon_threads = True

    class Linea(socketserver.StreamRequestHandler):
        def handle(self):
            linea = self.rfile.readline().decode().strip()
            self.wfile.write(json.dumps(self.server.capa.responder(linea)).encode())

    def __init__(self, capa: CapaFec):
        super().__init__(("127.0.0.1", 0), _ShimLinea.Linea)
        self.capa = capa


class EndpointElegir(unittest.TestCase):
    """POST /fec/elegir de envio.py (servidor HTTP real en un puerto efímero) contra un shim de mentira."""

    @classmethod
    def setUpClass(cls):
        cls.capa = CapaFec("hamming_15_11", "dn", dinamico=True)
        cls.shim = _ShimLinea(cls.capa)
        threading.Thread(target=cls.shim.serve_forever, daemon=True).start()
        cls.pagina = ThreadingHTTPServer(("127.0.0.1", 0), envio.Pagina)
        threading.Thread(target=cls.pagina.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.pagina.server_address[1]}"
        cls.puerto_viejo = envio.PUERTO_FEC
        envio.PUERTO_FEC = cls.shim.server_address[1]

    @classmethod
    def tearDownClass(cls):
        envio.PUERTO_FEC = cls.puerto_viejo
        cls.pagina.shutdown()
        cls.pagina.server_close()
        cls.shim.shutdown()
        cls.shim.server_close()

    def post(self, cuerpo: bytes, tipo="application/json"):
        req = urllib.request.Request(f"{self.url}/fec/elegir", data=cuerpo, method="POST",
                                     headers={"Content-Type": tipo})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_200_aplica_y_el_shim_lo_registra(self):
        st, j = self.post(json.dumps({"codigo": "secded_8_4"}).encode())
        self.assertEqual((st, j["ok"], j["codigo_activo"]), (200, True, "secded_8_4"))
        self.assertEqual(self.capa.codigo.nombre, "secded_8_4")

    def test_400_nombre_invalido_o_cuerpo_malo(self):
        self.assertEqual(self.post(json.dumps({"codigo": "repeticion_5_1"}).encode())[0], 400)
        self.assertEqual(self.post(json.dumps({"codigo": 3}).encode())[0], 400)
        self.assertEqual(self.post(b"no json")[0], 400)
        self.assertEqual(self.post(b'["secded_8_4"]')[0], 400)

    def test_415_exige_json(self):
        self.assertEqual(self.post(json.dumps({"codigo": "auto"}).encode(), tipo="text/plain")[0], 415)

    def test_409_shim_no_dinamico(self):
        viejo, self.shim.capa = self.shim.capa, CapaFec("repeticion_5_1", "dn")
        try:
            st, j = self.post(json.dumps({"codigo": "auto"}).encode())
        finally:
            self.shim.capa = viejo
        self.assertEqual((st, j["ok"], j["dinamico"]), (409, False, False))

    def test_503_si_el_shim_no_responde(self):
        viejo = envio.PUERTO_FEC
        envio.PUERTO_FEC = 1                     # nadie escucha en el puerto 1
        try:
            st, j = self.post(json.dumps({"codigo": "auto"}).encode())
        finally:
            envio.PUERTO_FEC = viejo
        self.assertEqual((st, j["disponible"]), (503, False))

    def test_pagina_tiene_el_selector_y_el_aviso_de_no_dinamico(self):
        with urllib.request.urlopen(f"{self.url}/", timeout=5) as r:
            html = r.read().decode()
        for t in ("Algoritmo FEC", "Hamming (15,11)", "SECDED (16,11)", "SECDED (8,4)", "Hamming (7,4)",
                  "Automático", "fec/elegir", "este despliegue no es dinámico",
                  "el sentido DN→UE cambia ya"):
            self.assertIn(t, html, t)


if __name__ == "__main__":
    unittest.main()
