"""Pruebas de la capa FEC del shim (sin TUN ni red): registro, emisión, recepción e inyección."""
from __future__ import annotations

import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fec
from registro_fec import (CapaFec, RegistroFec, aplicar_canal, emitir_marco, info_codec, mtu_con_fec,
                          recibir_marco, tamano_en_cable, validar_armada, validar_codigo)

# byte 0: bit 7 = ROHC-lite, bits 0-3 = id del códec; el resto es "carga" cualquiera
COMPRIMIDO = bytes([0x80 | 3]) + bytes(range(40))
HAM, REP = fec.HAMMING_7_4, fec.REPETICION_5_1


def procesar_ok(comprimido: bytes) -> int:
    return len(comprimido) + 20


class TestRegistro(unittest.TestCase):
    def test_capacidad_circular_e_ids(self):
        r = RegistroFec(capacidad=3)
        for i in range(5):
            self.assertEqual(r.anotar(x=i)["id"], i + 1)
        self.assertEqual([e["id"] for e in r.listar()], [3, 4, 5])
        self.assertEqual([e["id"] for e in r.listar(desde_id=4)], [5])
        self.assertEqual(r.listar(desde_id=5), [])


class TestEmision(unittest.TestCase):
    def test_apagado_no_cambia_el_marco(self):
        # Sin FEC el shim llama a emitir_marco(None, marco): debe devolver el MISMO objeto, y por un
        # socket UDP real en loopback los bytes que llegan son exactamente los comprimidos.
        self.assertIs(emitir_marco(None, COMPRIMIDO), COMPRIMIDO)
        emisor, receptor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM), socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(emisor.close); self.addCleanup(receptor.close)
        receptor.bind(("127.0.0.1", 0)); receptor.settimeout(5)
        emisor.sendto(emitir_marco(None, COMPRIMIDO), receptor.getsockname())
        self.assertEqual(receptor.recvfrom(65535)[0], COMPRIMIDO)
        # y con FEC activo sí cambia (para que la prueba anterior discrimine)
        self.assertNotEqual(emitir_marco(CapaFec("hamming_7_4", "ue"), COMPRIMIDO), COMPRIMIDO)

    def test_shim_usa_las_funciones_puras_y_valida_antes_de_esperar_la_tun(self):
        # shim.py no se importa en Windows (fcntl): se comprueba el orden en el fuente de main().
        fuente = (Path(__file__).resolve().parent / "shim.py").read_text(encoding="utf-8")
        main = fuente[fuente.index("def main()"):]
        self.assertLess(main.index("validar_codigo("), main.index('esperar_interfaz("uesimtun0")'))
        self.assertIn("emitir_marco(capa, marco)", main)
        self.assertIn("recibir_marco(capa, rx, marco, escribir)", main)

    def test_validar_codigo(self):
        self.assertIs(validar_codigo("hamming_7_4"), HAM)
        for malo in ("hamming", "x", "7_4"):
            with self.assertRaises(ValueError) as c:
                validar_codigo(malo)
            self.assertIn("hamming_7_4", str(c.exception))   # el mensaje lista los válidos

    def test_emitir_secuencia_y_tamano(self):
        capa = CapaFec("hamming_7_4", "ue")
        m0, m1 = capa.emitir(COMPRIMIDO), capa.emitir(COMPRIMIDO)
        self.assertEqual(fec.decodificar(m0)[1].secuencia, 0)
        self.assertEqual(fec.decodificar(m1)[1].secuencia, 1)
        self.assertEqual(len(m0), tamano_en_cable(HAM, len(COMPRIMIDO)))
        self.assertEqual(capa.c_bytes["fec"], 2 * len(m0))
        capa.secuencia = 65535
        capa.emitir(COMPRIMIDO)
        self.assertEqual(capa.secuencia, 0)

    def test_mtu_cabe_en_el_cable(self):
        for c in (HAM, REP):
            mtu = mtu_con_fec(c)
            self.assertLessEqual(tamano_en_cable(c, mtu + 36), 1464 - 28)
            self.assertGreater(tamano_en_cable(c, mtu + 36 + 16), 1464 - 28)
        self.assertEqual((mtu_con_fec(HAM), mtu_con_fec(REP)), (780, 247))


class TestRecepcion(unittest.TestCase):
    def setUp(self):
        self.tx, self.rx = CapaFec("hamming_7_4", "ue"), CapaFec("hamming_7_4", "dn")
        self.marco = self.tx.emitir(COMPRIMIDO)

    def test_limpio(self):
        e, err = self.rx.recibir(self.marco, procesar_ok)
        self.assertIsNone(err)
        self.assertEqual((e["estado"], e["bits_inyectados"], e["sindromes_no_cero"]), ("limpio", [], 0))
        self.assertEqual(e["codec_id"], 3)
        self.assertEqual((e["bytes_comprimido"], e["bytes_fec"], e["bytes_ip"], e["integro"], e["lado"]),
                         (len(COMPRIMIDO), len(self.marco), len(COMPRIMIDO) + 20, True, "dn"))
        self.assertTrue(info_codec(COMPRIMIDO)[2])

    def test_un_bit_se_corrige(self):
        self.rx.armar({"bits": 1, "semilla": 7})
        e, err = self.rx.recibir(self.marco, procesar_ok)
        self.assertIsNone(err)
        self.assertEqual(len(e["bits_inyectados"]), 1)
        self.assertGreaterEqual(e["bits_inyectados"][0], 8)
        self.assertEqual((e["estado"], e["corregidas"], e["sindromes_no_cero"], e["integro"]),
                         ("corregido", 1, 1, True))
        self.assertEqual(len(e["posiciones_corregidas"]), 1)
        self.assertEqual(sum(e["sindromes"].values()), 1)
        self.assertIsNone(self.rx.armada)  # se consumió con un paquete

    def test_posiciones_explicitas_y_se_consume_una_vez(self):
        self.rx.armar({"posiciones": [9]})
        e1, _ = self.rx.recibir(self.marco, procesar_ok)
        e2, _ = self.rx.recibir(self.marco, procesar_ok)
        self.assertEqual(e1["bits_inyectados"], [9])
        self.assertEqual((e2["bits_inyectados"], e2["estado"]), ([], "limpio"))

    def test_dos_bits_en_la_misma_palabra(self):
        # bits 8 y 9 = dos primeras posiciones de la primera palabra de 7: Hamming no corrige 2
        self.rx.armar({"posiciones": [8, 9]})
        e, err = self.rx.recibir(self.marco, procesar_ok)
        self.assertEqual(e["bits_inyectados"], [8, 9])
        self.assertGreaterEqual(e["sindromes_no_cero"], 1)   # se ve s != 0, pero...
        self.assertEqual(e["estado"], "corregido")           # ...se "corrige" mal (d_min = 3)

    def test_marco_invalido_no_escribe(self):
        llamadas = []
        e, err = self.rx.recibir(b"\x09\x01\x02", lambda c: llamadas.append(c) or 0)
        self.assertIsInstance(err, ValueError)
        self.assertEqual((e["estado"], e["integro"], e["bytes_ip"], e["codec"]), ("invalido", False, None, None))
        self.assertEqual(llamadas, [])
        self.assertEqual(self.rx.c_paquetes[("invalido", "n/a")], 1)

    def test_falla_al_descomprimir(self):
        def malo(_c):
            raise RuntimeError("flujo desincronizado")
        e, err = self.rx.recibir(self.marco, malo)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual((e["estado"], e["integro"], e["bytes_ip"]), ("limpio", False, None))

    def test_repeticion_1_bit_corrige_y_2_bits_no(self):
        tx, rx = CapaFec("repeticion_5_1", "ue"), CapaFec("repeticion_5_1", "dn")
        rx.armar({"posiciones": [8]})
        e, _ = rx.recibir(tx.emitir(COMPRIMIDO), procesar_ok)
        self.assertEqual((e["estado"], e["corregidas"]), ("corregido", 1))
        rx.armar({"posiciones": [8, 9]})   # 2 errores en una palabra de 5: el decodificador de 1 bit no la corrige
        e, _ = rx.recibir(tx.emitir(COMPRIMIDO), procesar_ok)
        self.assertEqual((e["estado"], e["no_corregibles"]), ("no_corregible", 1))

    def test_metricas(self):
        self.rx.armar({"bits": 2, "semilla": 1})
        self.rx.recibir(self.marco, procesar_ok)
        t = self.rx.texto('lado="dn"')
        for nombre in ("fec_paquetes_total", "fec_bits_inyectados_total", "fec_palabras_total"):
            self.assertIn(f"lab5g_cmp_{nombre}", t)
        self.assertIn('lab5g_cmp_fec_bits_inyectados_total{lado="dn",codigo="hamming_7_4"} 2', t)
        self.assertIn('tipo="fec"', self.tx.texto('lado="ue"'))


class Desempaquetador:
    """rx falso: «descomprimir» = quitar el byte 0 (lo que importa aquí es el marco FEC)."""
    def desempaquetar(self, comprimido: bytes) -> bytes:
        if comprimido[1:2] == b"\xee":
            raise RuntimeError("flujo desincronizado")
        return comprimido[1:]


class TestBucleLoopback(unittest.TestCase):
    """Emisión y recepción FEC con las funciones puras del shim sobre UDP en loopback (sin TUN)."""
    def setUp(self):
        self.tx, self.rx = CapaFec("hamming_7_4", "ue"), CapaFec("hamming_7_4", "dn")
        self.a, self.b = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2))
        self.addCleanup(self.a.close); self.addCleanup(self.b.close)
        self.b.bind(("127.0.0.1", 0)); self.b.settimeout(5)
        self.tun = []

    def viaje(self, comprimido=COMPRIMIDO):
        self.a.sendto(emitir_marco(self.tx, comprimido), self.b.getsockname())
        marco, _ = self.b.recvfrom(65535)
        return recibir_marco(self.rx, Desempaquetador(), marco, self.tun.append)

    def test_limpio(self):
        pkt, e, err = self.viaje()
        self.assertIsNone(err)
        self.assertEqual((pkt, self.tun, e["estado"], e["integro"]), (COMPRIMIDO[1:], [COMPRIMIDO[1:]], "limpio", True))

    def test_un_error_se_corrige_y_dos_se_cuentan(self):
        self.rx.armar({"posiciones": [9]})
        pkt, e, _ = self.viaje()
        self.assertEqual((pkt, e["estado"], e["bits_inyectados"]), (COMPRIMIDO[1:], "corregido", [9]))
        self.rx.armar({"posiciones": [8, 9]})
        pkt, e, _ = self.viaje()
        self.assertGreaterEqual(e["sindromes_no_cero"], 1)

    def test_falla_al_descomprimir_no_escribe(self):
        pkt, e, err = self.viaje(bytes([3, 0xEE]) + bytes(10))
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual((pkt, self.tun, e["integro"]), (None, [], False))

    def test_marco_invalido_no_escribe(self):
        pkt, e, err = recibir_marco(self.rx, Desempaquetador(), b"\x09\x01\x02", self.tun.append)
        self.assertEqual((pkt, self.tun, e["estado"]), (None, [], "invalido"))
        self.assertIsInstance(err, ValueError)

    def test_contadores_acumulados_tras_el_bucle(self):
        self.rx.armar({"posiciones": [9]}); self.viaje()
        self.viaje()
        recibir_marco(self.rx, Desempaquetador(), b"\x09\x01", None)
        c = self.rx.registro.contadores()
        self.assertEqual((c["paquetes"], c["con_error_inyectado"], c["corregidas"], c["invalidos"],
                          c["bits_inyectados"], c["ultimo_id"]), (3, 1, 1, 1, 1, 3))


class TestComandos(unittest.TestCase):
    def test_contadores_no_dependen_del_anillo(self):
        capa = CapaFec("hamming_7_4", "dn", capacidad=3)
        capa.armar({"posiciones": [9]})
        marco = capa.emitir(COMPRIMIDO)
        capa.recibir(marco, procesar_ok)            # el único con error: será el primero en salir del anillo
        for _ in range(10):
            capa.recibir(marco, procesar_ok)
        self.assertEqual(len(capa.registro.listar()), 3)
        r = capa.responder("CONTADORES")
        self.assertEqual(r["ok"], True)
        self.assertEqual((r["paquetes"], r["con_error_inyectado"], r["corregidas"], r["sindromes_no_cero"],
                          r["bits_inyectados"], r["ultimo_id"]), (11, 1, 1, 1, 1, 11))
        self.assertEqual(set(r), {"ok", "codigo", "lado", "paquetes", "con_error_inyectado", "sindromes_no_cero",
                                  "corregidas", "no_corregibles", "invalidos", "bits_inyectados", "palabras",
                                  "ultimo_id"})
        self.assertEqual(r["palabras"], 11 * fec.decodificar(marco)[1].palabras)
        json.dumps(r)

    def test_json_anidado_no_mata_el_hilo(self):
        capa = CapaFec("hamming_7_4", "dn")
        for nido in ("[" * 4000, "[" * 4000 + "]" * 4000, '{"a":' * 1000):
            r = capa.responder("INYECTAR " + nido)       # RecursionError o JSONDecodeError: ambos deben contestarse
            self.assertFalse(r["ok"])
            self.assertTrue(r["error"])
        self.assertEqual(capa.responder("CODIGO")["codigo"], "hamming_7_4")   # sigue vivo

    def test_validar(self):
        self.assertEqual(validar_armada({"bits": 2}), {"bits": 2, "semilla": None})
        for malo in ({"bits": 0}, {"bits": "2"}, {"posiciones": []}, {"posiciones": [3]},
                     {"posiciones": [True]}, {"semilla": 1}, [1]):
            with self.assertRaises(ValueError, msg=malo):
                validar_armada(malo)

    def test_aplicar_canal_recorta_y_ignora_fuera_de_rango(self):
        m = bytes(4)
        self.assertEqual(aplicar_canal(m, None), (m, []))
        self.assertEqual(len(aplicar_canal(m, {"bits": 200, "semilla": 1})[1]), 24)
        self.assertEqual(aplicar_canal(m, {"posiciones": [9, 500]})[1], [9])

    def test_responder(self):
        capa = CapaFec("hamming_7_4", "dn")
        capa.recibir(capa.emitir(COMPRIMIDO), procesar_ok)
        r = capa.responder("INYECTAR " + json.dumps({"bits": 1, "semilla": 5}))
        self.assertEqual(r, {"ok": True, "armada": {"bits": 1, "semilla": 5}})
        p = capa.responder("PAQUETES 0")
        self.assertEqual((p["codigo"], p["n"], p["k"], p["lado"], p["armada"]),
                         ("hamming_7_4", 7, 4, "dn", {"bits": 1, "semilla": 5}))
        self.assertEqual([e["id"] for e in p["paquetes"]], [1])
        self.assertEqual(capa.responder("PAQUETES 1")["paquetes"], [])
        c = capa.responder("CODIGO")
        self.assertEqual((len(c["P"]), len(c["P"][0]), len(c["H"]), len(c["H"][0])), (4, 3, 3, 7))
        for linea in ("INYECTAR {mal", 'INYECTAR {"bits": 0}', "NADA", "PAQUETES x", ""):
            self.assertFalse(capa.responder(linea)["ok"], linea)


if __name__ == "__main__":
    unittest.main()
