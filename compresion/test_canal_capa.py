"""Canal aleatorio dentro de CapaFec (sin TUN ni red):  python -m unittest compresion/test_canal_capa.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fec
from canal import bsc, parse_canal
from registro_fec import CapaFec

# byte 0: bit 7 = ROHC-lite, bits 0-3 = id del códec; el resto es "carga" cualquiera
COMPRIMIDO = bytes([0x80 | 3]) + bytes(range(40))
HAM = fec.HAMMING_7_4


def recibir(capa: CapaFec, marco: bytes, salida: list[bytes]):
    """Recibe un marco -> (entrada, error). `salida` acumula los paquetes descomprimidos."""
    def procesar(comprimido: bytes) -> int:
        salida.append(comprimido)
        return len(comprimido) + 20
    return capa.recibir(marco, procesar)


def marcos(n: int) -> list[bytes]:
    emisor = CapaFec(HAM, "ue")
    return [emisor.emitir(COMPRIMIDO[:1] + bytes((i + j) % 256 for j in range(40))) for i in range(n)]


class TestCanalAleatorioEnCapa(unittest.TestCase):
    def test_bits_volteados_y_estado_no_limpio(self):
        marco = marcos(1)[0]
        rx = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:0.05"), semilla_canal=3)
        salida: list[bytes] = []
        entrada, _ = recibir(rx, marco, salida)
        bits = entrada["bits_inyectados"]
        self.assertGreater(len(bits), 0)
        self.assertTrue(all(8 <= b < 8 * len(marco) for b in bits))
        self.assertNotEqual(entrada["estado"], "limpio")
        self.assertEqual(rx.registro.contadores()["bits_inyectados"], len(bits))
        self.assertEqual(rx.c_bits, len(bits))

    def test_semilla_por_marco_es_semilla_base_mas_indice(self):
        # El marco k (0-based) usa la semilla semilla_canal + k: el segundo marco debe volcar
        # exactamente los bits de bsc(..., semilla=8).
        m1, m2 = marcos(2)
        rx = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:0.05"), semilla_canal=7)
        recibir(rx, m1, [])
        entrada, _ = recibir(rx, m2, [])
        self.assertEqual(entrada["bits_inyectados"], bsc(8 * len(m2), 0.05, semilla=8, desde_bit=8))

    def test_misma_semilla_mismos_bits_en_dos_capas(self):
        ms = marcos(4)
        rx1 = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:0.02"), semilla_canal=7)
        rx2 = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:0.02"), semilla_canal=7)
        rx3 = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:0.02"), semilla_canal=8)
        b1 = [recibir(rx1, m, [])[0]["bits_inyectados"] for m in ms]
        b2 = [recibir(rx2, m, [])[0]["bits_inyectados"] for m in ms]
        b3 = [recibir(rx3, m, [])[0]["bits_inyectados"] for m in ms]
        self.assertEqual(b1, b2)
        self.assertNotEqual(b1, b3)

    def test_manual_y_canal_se_combinan_con_xor(self):
        # Canal bsc con p=1 vuelca TODOS los bits >= 8; la armada manual vuelve a voltear 9 y 10,
        # así que esos dos quedan sin cambio (e + e = 0) y el resto sale en la lista.
        marco = marcos(1)[0]
        total = 8 * len(marco)
        rx = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=parse_canal("bsc:1"))
        rx.armar({"posiciones": [9, 10]})
        entrada, _ = recibir(rx, marco, [])
        self.assertEqual(entrada["bits_inyectados"], [b for b in range(8, total) if b not in (9, 10)])

    def test_manual_sin_canal_queda_igual(self):
        marco = marcos(1)[0]
        rx = CapaFec(HAM, "dn", reloj=lambda: 0.0)
        rx.armar({"posiciones": [9, 10]})
        entrada, _ = recibir(rx, marco, [])
        self.assertEqual(entrada["bits_inyectados"], [9, 10])


class TestSinCanalEsIdentico(unittest.TestCase):
    def test_canal_none_igual_que_omitirlo(self):
        ms = marcos(5)
        rx_omitido = CapaFec(HAM, "dn", reloj=lambda: 0.0)
        rx_none = CapaFec(HAM, "dn", reloj=lambda: 0.0, canal=None, semilla_canal=99)
        for rx in (rx_omitido, rx_none):
            rx.armar({"bits": 2, "semilla": 4})
        sal1, sal2 = [], []
        e1 = [recibir(rx_omitido, m, sal1) for m in ms]
        e2 = [recibir(rx_none, m, sal2) for m in ms]
        self.assertEqual(sal1, sal2)
        self.assertEqual([x[0] for x in e1], [x[0] for x in e2])
        self.assertEqual([x[1] for x in e1], [x[1] for x in e2])
        self.assertEqual(rx_omitido.registro.contadores(), rx_none.registro.contadores())

    def test_describir_codigo_incluye_canal(self):
        self.assertIsNone(CapaFec(HAM, "dn").describir_codigo()["canal"])
        self.assertIsNone(CapaFec(HAM, "dn", canal=None).describir_codigo()["canal"])
        capa = CapaFec(HAM, "dn", canal=parse_canal("bsc:1e-3"))
        self.assertEqual(capa.describir_codigo()["canal"], "bsc:1e-3")
        self.assertEqual(capa.responder("CODIGO")["canal"], "bsc:1e-3")


if __name__ == "__main__":
    unittest.main()
