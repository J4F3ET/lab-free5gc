"""Pruebas de los canales aleatorios (bsc, ráfaga) y del parseo de CANAL, sin red:  python -m unittest compresion/test_canal.py"""
from __future__ import annotations

import statistics
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canal import Canal, bsc, parse_canal, rafaga


def gaps(posiciones: list[int]) -> list[int]:
    return [b - a for a, b in zip(posiciones, posiciones[1:])]


class TestBsc(unittest.TestCase):
    def test_determinista_por_semilla(self):
        self.assertEqual(bsc(5000, 0.01, semilla=42), bsc(5000, 0.01, semilla=42))
        self.assertNotEqual(bsc(5000, 0.01, semilla=42), bsc(5000, 0.01, semilla=43))

    def test_p_cero_no_voltea_nada(self):
        self.assertEqual(bsc(800, 0, semilla=1), [])

    def test_p_uno_voltea_todos_desde_desde_bit(self):
        self.assertEqual(bsc(800, 1, semilla=1), list(range(8, 800)))
        self.assertEqual(bsc(800, 1, semilla=1, desde_bit=0), list(range(0, 800)))

    def test_frecuencia_observada(self):
        pos = bsc(200008, 0.01, semilla=7, desde_bit=8)   # 200000 bits protegibles
        tasa = len(pos) / 200000
        self.assertTrue(0.006 <= tasa <= 0.014, f"tasa observada {tasa:.5f}")

    def test_limites_y_orden(self):
        for semilla in range(20):
            pos = bsc(1000, 0.3, semilla=semilla, desde_bit=8)
            self.assertTrue(all(8 <= p < 1000 for p in pos))
            self.assertEqual(pos, sorted(set(pos)))   # ordenadas y sin repetir

    def test_eficiente_con_p_pequena(self):
        t0 = time.perf_counter()
        for _ in range(100):
            bsc(800, 1e-3, semilla=3)
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_pocos_bits_no_falla(self):
        self.assertEqual(bsc(8, 0.5, semilla=1), [])
        self.assertEqual(bsc(0, 0.5, semilla=1), [])

    def test_p_fuera_de_rango(self):
        for mala in (-0.1, 1.5, float("nan"), True, "0.1"):
            with self.assertRaises(ValueError):
                bsc(100, mala, semilla=1)


class TestRafaga(unittest.TestCase):
    # Estado malo: error 0.5 por bit; entra con 0.005 por bit y sale con 0.05 (ráfagas de ~20 bits).
    P_MALO, PBM, PMB = 0.5, 0.005, 0.05
    N = 400008   # 400000 bits desde el bit 8

    def test_determinista_por_semilla(self):
        a = rafaga(2000, 0.0, 0.3, 0.01, 0.2, semilla=5)
        self.assertEqual(a, rafaga(2000, 0.0, 0.3, 0.01, 0.2, semilla=5))
        self.assertNotEqual(a, rafaga(2000, 0.0, 0.3, 0.01, 0.2, semilla=6))

    def test_sin_transicion_y_p_bueno_cero_no_voltea(self):
        self.assertEqual(rafaga(800, 0.0, 0.5, 0.0, 0.0, semilla=1), [])   # empieza bueno y no sale

    def test_limites(self):
        pos = rafaga(2000, 0.0, 0.9, 0.1, 0.1, semilla=2, desde_bit=8)
        self.assertTrue(all(8 <= p < 2000 for p in pos))

    def test_tasa_observada_cerca_de_la_teorica(self):
        pos = rafaga(self.N, 0.0, self.P_MALO, self.PBM, self.PMB, semilla=11)
        pi_malo = self.PBM / (self.PBM + self.PMB)              # fracción estacionaria en estado malo
        teorica = pi_malo * self.P_MALO
        observada = len(pos) / 400000
        self.assertLess(abs(observada - teorica) / teorica, 0.2,
                        f"observada {observada:.5f} vs teórica {teorica:.5f}")

    def test_errores_agrupados_frente_a_bsc(self):
        # Mismo tasa global: un bsc con p = tasa teórica reparte los errores al azar. En la ráfaga
        # la mayoría de los errores tiene un vecino a pocos bits, así que la distancia típica
        # (mediana) entre errores consecutivos es mucho menor.
        # Nota: la MEDIA de las distancias es ≈ 1/tasa en ambos casos (es un promedio telescópico),
        # por eso se compara la mediana, que sí separa los dos modelos.
        pi_malo = self.PBM / (self.PBM + self.PMB)
        teorica = pi_malo * self.P_MALO
        g_rafaga = gaps(rafaga(self.N, 0.0, self.P_MALO, self.PBM, self.PMB, semilla=11))
        g_bsc = gaps(bsc(self.N, teorica, semilla=11))
        self.assertGreater(len(g_rafaga), 1000)
        self.assertLess(statistics.median(g_rafaga), statistics.median(g_bsc) / 2,
                        f"mediana ráfaga {statistics.median(g_rafaga)} vs bsc {statistics.median(g_bsc)}")
        # y el porcentaje de distancias cortas (<= 10 bits) es claramente mayor
        corta = lambda g: sum(1 for x in g if x <= 10) / len(g)
        self.assertGreater(corta(g_rafaga), corta(g_bsc) + 0.3)


class TestParseCanal(unittest.TestCase):
    def test_vacio_y_none_son_sin_canal(self):
        self.assertIsNone(parse_canal(None))
        self.assertIsNone(parse_canal(""))
        self.assertIsNone(parse_canal("   "))

    def test_acepta_bsc(self):
        c = parse_canal("bsc:1e-3")
        self.assertIsInstance(c, Canal)
        self.assertEqual(c.nombre, "bsc:1e-3")
        self.assertEqual(c.posiciones(800, 1), bsc(800, 1e-3, 1, 8))

    def test_acepta_rafaga(self):
        c = parse_canal("rafaga:0.3,0.01,0.2")
        self.assertEqual(c.nombre, "rafaga:0.3,0.01,0.2")
        self.assertEqual(c.posiciones(800, 4), rafaga(800, 0.0, 0.3, 0.01, 0.2, semilla=4, desde_bit=8))

    def test_rechaza_lo_malo(self):
        malos = ["bsc", "bsc:", "bsc:abc", "bsc:2", "bsc:-0.1", "bsc:nan", "bsc:inf",
                 "bsc:0.1,0.2", "rafaga:0.3,0.01", "rafaga:0.3,0.01,0.2,0.1",
                 "rafaga:0.3,x,0.2", "rafaga:0.3,0.01,1.5", "gauss:0.1", "1e-3", "rafaga"]
        for m in malos:
            with self.subTest(valor=m), self.assertRaises(ValueError):
                parse_canal(m)

    def test_no_texto_es_error(self):
        with self.assertRaises(ValueError):
            parse_canal(0.001)


if __name__ == "__main__":
    unittest.main()
