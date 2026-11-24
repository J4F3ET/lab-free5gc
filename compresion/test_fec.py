"""Pruebas de codificación de canal:  python -m unittest compresion/test_fec.py"""
from __future__ import annotations

import itertools, random, sys, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canal
import fec
from fec import HAMMING_7_4 as HAM, REPETICION_5_1 as REP, SECDED_8_4 as SEC

AMBOS = (REP, HAM)


def bits(v: int, n: int) -> list[int]:
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def volteado(r: list[int], *pos: int) -> list[int]:
    r = list(r)
    for p in pos:
        r[p] ^= 1
    return r


class Matrices(unittest.TestCase):
    def test_H_por_Gt_es_cero(self):
        for c in AMBOS:
            for h in c.H:
                for g in c.G:
                    self.assertEqual(sum(a * b for a, b in zip(h, g)) % 2, 0, c.nombre)

    def test_matrices_de_clase(self):
        self.assertEqual(REP.H, [[1, 0, 0, 0, 1], [0, 1, 0, 0, 1], [0, 0, 1, 0, 1], [0, 0, 0, 1, 1]])
        self.assertEqual(HAM.H, [[1, 0, 0, 1, 0, 1, 1], [0, 1, 0, 1, 1, 1, 0], [0, 0, 1, 0, 1, 1, 1]])
        self.assertEqual((REP.n, REP.k, HAM.n, HAM.k), (5, 1, 7, 4))

    def test_P_invalida_o_H_Gt_distinto_de_cero(self):
        with self.assertRaises(ValueError):
            fec.CodigoLineal("x", [[1, 2]])
        with self.assertRaises(ValueError):
            fec.CodigoLineal("x", [])


class EjemplosDeClase(unittest.TestCase):
    def test_hamming_m1011(self):
        m = [1, 0, 1, 1]
        c = HAM.codificar_palabra(m)
        self.assertEqual(c[:3], [1, 0, 0])             # b = m·P
        self.assertEqual(c, [1, 0, 0, 1, 0, 1, 1])     # c = [b : m]
        r = [1, 0, 0, 1, 1, 1, 1]                      # error en el bit 5 (índice 4)
        s = HAM.sindrome(r)
        self.assertEqual(s, (0, 1, 1))
        self.assertEqual(s, tuple(f[4] for f in HAM.H))  # = columna 5 de H
        p = HAM.corregir_palabra(r)
        self.assertEqual((p.posicion, p.estado, p.c_corregida, p.m), (4, "corregido", c, m))

    def test_repeticion(self):
        r = [1, 0, 1, 1, 1]
        self.assertEqual(REP.sindrome(r), (0, 1, 0, 0))
        p = REP.corregir_palabra(r)
        self.assertEqual(p.posicion + 1, 2)            # posición 2 (1-indexada)
        self.assertEqual(p.c_corregida, [1, 1, 1, 1, 1])
        self.assertEqual(p.estado, "corregido")
        for i, esperado in enumerate([(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)]):
            self.assertEqual(REP.sindrome(volteado([1] * 5, i)), esperado)

    def test_sin_error_y_no_corregible(self):
        p = HAM.corregir_palabra(HAM.codificar_palabra([0, 1, 1, 0]))
        self.assertEqual((p.estado, p.posicion, p.sindrome), ("limpio", None, (0, 0, 0)))
        # repetición: dos errores en bits de paridad -> s = 1100, ninguna columna coincide
        p = REP.corregir_palabra([0, 0, 1, 1, 1])
        self.assertEqual((p.estado, p.posicion), ("no_corregible", None))
        self.assertEqual(REP.posicion_error((0, 0, 0, 0)), None)

    def test_hamming_todo_error_de_un_bit_se_corrige(self):
        for v in range(16):
            m = bits(v, 4)
            c = HAM.codificar_palabra(m)
            self.assertEqual(HAM.sindrome(c), (0, 0, 0))
            for i in range(7):
                p = HAM.corregir_palabra(volteado(c, i))
                self.assertEqual((p.c_corregida, p.m, p.posicion, p.estado), (c, m, i, "corregido"))

    def test_hamming_dos_errores_se_detectan_pero_se_corrigen_mal(self):
        # d_min = 3: s ≠ 0 siempre (se detecta), pero el decodificador de 1 bit voltea
        # un tercer bit y entrega OTRA palabra código; no hay forma de notarlo.
        for v in range(16):
            c = HAM.codificar_palabra(bits(v, 4))
            for i, j in itertools.combinations(range(7), 2):
                p = HAM.corregir_palabra(volteado(c, i, j))
                self.assertTrue(any(p.sindrome))
                self.assertNotEqual(p.c_corregida, c)
                self.assertEqual(p.estado, "corregido")  # engañoso a propósito: se cuenta tal cual


class Bytes(unittest.TestCase):
    def datos(self, n, semilla=7):
        return random.Random(semilla).randbytes(n)

    def test_ids_estables_y_busqueda(self):
        self.assertEqual({i: c.nombre for i, c in fec.CODIGOS.items()},
                         {1: "repeticion_5_1", 2: "hamming_7_4", 3: "secded_8_4"})
        self.assertIs(fec.buscar("hamming_7_4"), HAM)
        self.assertIs(fec.buscar("secded_8_4"), SEC)
        self.assertIs(fec.buscar(3), SEC)
        self.assertIs(fec.buscar(1), REP)
        with self.assertRaises(ValueError):
            fec.buscar("nada")

    def test_ida_y_vuelta_sin_errores(self):
        for c in AMBOS:
            for largo in (0, 1, 2, 1400):
                d = self.datos(largo, largo)
                marco = fec.codificar(d, c, secuencia=513)
                self.assertEqual(marco[0], c.id)
                salida, r = fec.decodificar(marco)
                self.assertEqual(salida, d)
                self.assertEqual((r.secuencia, r.largo), (513, largo))
                self.assertEqual(r.palabras, -(-(32 + 8 * largo) // c.k))
                self.assertEqual(r.bits_codificados, r.palabras * c.n)
                self.assertEqual((r.sindromes_no_cero, r.corregidas, r.no_corregibles), (0, 0, 0))
                self.assertEqual((r.sindromes, r.posiciones_corregidas), ({}, []))
                self.assertEqual(len(marco), 1 + -(-r.bits_codificados // 8))

    def test_un_bit_por_palabra(self):
        for c in AMBOS:
            d = self.datos(100)
            marco = fec.codificar(d, c, 9)
            r0 = fec.decodificar(marco)[1]
            pos = [8 + i * c.n + (i % c.n) for i in range(r0.palabras)]
            salida, r = fec.decodificar(canal.inyectar(marco, pos))
            self.assertEqual(salida, d, c.nombre)
            self.assertEqual((r.corregidas, r.sindromes_no_cero, r.no_corregibles), (r0.palabras, r0.palabras, 0))
            self.assertEqual(sum(r.sindromes.values()), r0.palabras)
            self.assertEqual(r.posiciones_corregidas, [(i, i % c.n) for i in range(64)])

    def test_un_bit_en_marco_completo(self):
        for c in AMBOS:
            d = self.datos(1400)
            marco = fec.codificar(d, c, 1)
            palabras = fec.decodificar(marco)[1].palabras
            for semilla in range(20):
                pos = canal.elegir_posiciones(8 + palabras * c.n, 1, semilla, desde_bit=8)
                salida, r = fec.decodificar(canal.inyectar(marco, pos))
                self.assertEqual(salida, d)
                self.assertEqual((r.corregidas, r.sindromes_no_cero, r.no_corregibles), (1, 1, 0))
                i, p = r.posiciones_corregidas[0]
                self.assertEqual(8 + i * c.n + p, pos[0])

    def test_dos_errores_en_hamming_se_ven_pero_corrompen(self):
        d = self.datos(50)
        marco = fec.codificar(d, HAM)
        # dos bits de la misma palabra (la 40 -> bits 8+280..): s ≠ 0, "corregida", datos mal
        base = 8 + 40 * 7
        salida, r = fec.decodificar(canal.inyectar(marco, [base, base + 3]))
        self.assertEqual((r.sindromes_no_cero, r.corregidas), (1, 1))
        self.assertNotEqual(salida, d)

    def test_repeticion_con_no_corregibles(self):
        marco = fec.codificar(b"abc", REP)
        # dos errores en los bits de paridad de una palabra: s = 1100 sin columna
        pos = [8 + 50 * 5 + 0, 8 + 50 * 5 + 1]
        _, r = fec.decodificar(canal.inyectar(marco, pos))
        self.assertEqual((r.no_corregibles, r.corregidas, r.sindromes_no_cero), (1, 0, 1))
        self.assertEqual(r.sindromes, {"1100": 1})

    def test_marco_invalido(self):
        buenos = {c.id: fec.codificar(b"hola mundo", c) for c in AMBOS}
        casos = [b"", b"\x09abc", b"\x02", b"\x02\x00", b"\x01\xff"]
        for m in buenos.values():
            casos += [m[:-1], m + b"\x00", m[:3]]
        for m in casos:
            with self.assertRaises(ValueError, msg=repr(m)):
                fec.decodificar(m)
        with self.assertRaises(ValueError):
            fec.codificar(b"", HAM, secuencia=70000)
        with self.assertRaises(ValueError):
            fec.codificar(bytes(65536), HAM)
        with self.assertRaises(ValueError):
            HAM.decodificar(buenos[1])  # id de otro código

    def test_interfaz_y_velocidad(self):
        for c in AMBOS:
            for attr in ("nombre", "n", "k", "codificar", "decodificar"):
                self.assertTrue(hasattr(c, attr))
        d = self.datos(1400)
        for c in AMBOS:
            fec.decodificar(fec.codificar(d, c))  # calienta tablas
            t0 = time.perf_counter()
            marco = fec.codificar(d, c)
            t1 = time.perf_counter()
            fec.decodificar(marco)
            t2 = time.perf_counter()
            print(f"\n[{c.nombre}] codificar 1400 B: {(t1 - t0) * 1e3:.2f} ms; "
                  f"decodificar: {(t2 - t1) * 1e3:.2f} ms", file=sys.stderr)
            self.assertLess(t2 - t0, 0.1)


class Secded(unittest.TestCase):
    """SECDED (8,4) de Hsiao: d_min = 4, corrige 1 error y detecta 2 sin corregir mal."""

    def test_matriz_y_registro(self):
        for h in SEC.H:
            for g in SEC.G:
                self.assertEqual(sum(a * b for a, b in zip(h, g)) % 2, 0)
        self.assertEqual((SEC.n, SEC.k, SEC.id, SEC.nombre), (8, 4, 3, "secded_8_4"))
        self.assertIs(fec.CODIGOS[3], SEC)

    def test_un_error_se_corrige_para_todo_mensaje(self):
        for v in range(16):
            m = bits(v, 4)
            c = SEC.codificar_palabra(m)
            self.assertEqual(SEC.sindrome(c), (0, 0, 0, 0))
            for i in range(8):
                p = SEC.corregir_palabra(volteado(c, i))
                self.assertEqual((p.c_corregida, p.m, p.posicion, p.estado), (c, m, i, "corregido"))

    def test_dos_errores_no_corregibles_con_sindrome_de_peso_par(self):
        for v in range(16):
            c = SEC.codificar_palabra(bits(v, 4))
            for i, j in itertools.combinations(range(8), 2):
                p = SEC.corregir_palabra(volteado(c, i, j))
                self.assertEqual((p.estado, p.posicion, p.c_corregida), ("no_corregible", None, volteado(c, i, j)))
                self.assertTrue(any(p.sindrome))
                self.assertEqual(sum(p.sindrome) % 2, 0)
                self.assertIsNone(SEC.posicion_error(p.sindrome))

    def test_distancia_minima_4(self):
        for v in range(1, 16):
            c = SEC.codificar_palabra(bits(v, 4))
            self.assertGreaterEqual(sum(c), 4, bin(v))

    def test_ida_y_vuelta_sin_errores(self):
        for largo in (0, 1, 2, 1400):
            d = random.Random(11).randbytes(largo)
            marco = fec.codificar(d, SEC, secuencia=7)
            salida, r = fec.decodificar(marco)
            self.assertEqual(salida, d)
            self.assertEqual((r.corregidas, r.no_corregibles, r.sindromes_no_cero), (0, 0, 0))

    def test_canal_un_bit_se_corrige_dos_bits_no(self):
        d = random.Random(5).randbytes(60)
        marco = fec.codificar(d, SEC, secuencia=2)
        # byte 0 = id; la palabra i empieza en el bit 8 + 8*i, bit j de la palabra = bit 8 + 8*i + j
        una = canal.inyectar(marco, [8 + 3 * 8 + 5])
        salida, r = fec.decodificar(una)
        self.assertEqual(salida, d)
        self.assertEqual((r.corregidas, r.no_corregibles), (1, 0))
        dos = canal.inyectar(marco, [8 + 3 * 8 + 0, 8 + 3 * 8 + 1])
        _, r = fec.decodificar(dos)
        self.assertGreaterEqual(r.no_corregibles, 1)
        self.assertEqual(r.corregidas, 0)
        self.assertEqual(r.posiciones_corregidas, [])


class Canal(unittest.TestCase):
    def test_inyectar(self):
        orig = bytes([0b00000000, 0b11111111])
        self.assertEqual(canal.inyectar(orig, [0, 15]), bytes([0b10000000, 0b11111110]))
        self.assertEqual(orig, bytes([0, 255]))  # no muta
        self.assertEqual(canal.inyectar(orig, []), orig)
        self.assertEqual(canal.inyectar(orig, [3, 3]), orig)
        for malo in (16, -1):
            with self.assertRaises(IndexError):
                canal.inyectar(orig, [malo])

    def test_elegir_posiciones(self):
        a = canal.elegir_posiciones(100, 10, semilla=1, desde_bit=8)
        self.assertEqual(a, canal.elegir_posiciones(100, 10, semilla=1, desde_bit=8))
        self.assertEqual(len(set(a)), 10)
        self.assertTrue(all(8 <= p < 100 for p in a))
        with self.assertRaises(ValueError):
            canal.elegir_posiciones(10, 5, desde_bit=8)


if __name__ == "__main__":
    unittest.main()
