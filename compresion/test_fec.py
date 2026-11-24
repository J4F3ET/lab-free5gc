"""Pruebas de codificación de canal:  python -m unittest compresion/test_fec.py"""
from __future__ import annotations

import itertools, random, sys, time, tracemalloc, unittest
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
                         {1: "repeticion_5_1", 2: "hamming_7_4", 3: "secded_8_4",
                          4: "hamming_15_11", 5: "secded_16_11"})
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


H15 = fec.HAMMING_15_11
S16 = fec.SECDED_16_11


def mensajes(k: int, cantidad: int = 64, semilla: int = 3) -> list[list[int]]:
    """Mensajes de prueba con semilla fija: el todo-0, el todo-1 y el resto aleatorios."""
    rnd = random.Random(semilla)
    return [[0] * k, [1] * k] + [[rnd.randrange(2) for _ in range(k)] for _ in range(cantidad - 2)]


class Hamming15(unittest.TestCase):
    """Hamming (15,11): r = 4, d_min = 3. Corrige 1 error; con 2 corrige mal sin avisar."""

    def test_matriz_y_registro(self):
        for h in H15.H:
            for g in H15.G:
                self.assertEqual(sum(a * b for a, b in zip(h, g)) % 2, 0)
        self.assertEqual((H15.n, H15.k, H15.id, H15.nombre), (15, 11, 4, "hamming_15_11"))
        self.assertIs(fec.CODIGOS[4], H15)
        self.assertIs(fec.buscar("hamming_15_11"), H15)
        self.assertIs(fec.buscar(4), H15)

    def test_columnas_de_H_son_todos_los_no_nulos_de_4_bits(self):
        cols = {tuple(H15.H[f][c] for f in range(4)) for c in range(15)}
        self.assertEqual(cols, {tuple(bits(v, 4)) for v in range(1, 16)})

    def test_un_error_se_corrige_para_todo_mensaje_de_muestra(self):
        for m in mensajes(11):
            c = H15.codificar_palabra(m)
            self.assertEqual(H15.sindrome(c), (0, 0, 0, 0))
            for i in range(15):
                p = H15.corregir_palabra(volteado(c, i))
                self.assertEqual((p.c_corregida, p.m, p.posicion, p.estado), (c, m, i, "corregido"))

    def test_distancia_minima_3(self):
        for v in range(1, 1 << 11):  # los 2047 mensajes no nulos
            c = H15.codificar_palabra(bits(v, 11))
            self.assertGreaterEqual(sum(c), 3, bin(v))

    def test_dos_errores_en_posiciones_1_y_2_corrigen_mal(self):
        m = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0]
        c = H15.codificar_palabra(m)
        r = volteado(c, 0, 1)                                  # errores en las posiciones 1 y 2 (1-indexadas)
        p = H15.corregir_palabra(r)
        self.assertEqual(p.sindrome, (1, 1, 0, 0))             # = columna 1 + columna 2 de H
        self.assertEqual(p.sindrome, tuple(H15.H[f][p.posicion] for f in range(4)))  # es una columna de H
        self.assertEqual((p.estado, p.posicion), ("corregido", 9))
        self.assertEqual(p.c_corregida, volteado(r, 9))        # voltea un tercer bit...
        self.assertNotEqual(p.m, m)                            # ...y el mensaje sale mal, sin aviso

    def test_ida_y_vuelta_y_un_bit_en_el_canal(self):
        for largo in (0, 1, 2, 1400):
            d = random.Random(largo + 1).randbytes(largo)
            marco = fec.codificar(d, H15, secuencia=3)
            self.assertEqual(fec.decodificar(marco)[0], d)
            palabras = fec.decodificar(marco)[1].palabras
            salida, r = fec.decodificar(canal.inyectar(marco, [8 + 1 * 15 + 2]))
            self.assertEqual(salida, d)
            self.assertEqual((r.corregidas, r.no_corregibles), (1, 0))
            self.assertEqual(r.posiciones_corregidas, [(1, 2)])
            self.assertEqual(r.palabras, palabras)


class Secded16(unittest.TestCase):
    """SECDED (16,11): r = 5, d_min = 4. Corrige 1 error y detecta 2 sin corregir mal."""

    def test_matriz_y_registro(self):
        for h in S16.H:
            for g in S16.G:
                self.assertEqual(sum(a * b for a, b in zip(h, g)) % 2, 0)
        self.assertEqual((S16.n, S16.k, S16.id, S16.nombre), (16, 11, 5, "secded_16_11"))
        self.assertIs(fec.CODIGOS[5], S16)
        self.assertIs(fec.buscar("secded_16_11"), S16)
        self.assertIs(fec.buscar(5), S16)

    def test_columnas_de_H_impares_y_distintas(self):
        cols = [tuple(S16.H[f][c] for f in range(5)) for c in range(16)]
        self.assertEqual(len(set(cols)), 16)
        self.assertTrue(all(sum(c) % 2 == 1 for c in cols))

    def test_un_error_se_corrige_para_todo_mensaje_de_muestra(self):
        for m in mensajes(11):
            c = S16.codificar_palabra(m)
            self.assertEqual(S16.sindrome(c), (0, 0, 0, 0, 0))
            for i in range(16):
                p = S16.corregir_palabra(volteado(c, i))
                self.assertEqual((p.c_corregida, p.m, p.posicion, p.estado), (c, m, i, "corregido"))

    def test_todas_las_parejas_no_corregibles_con_sindrome_de_peso_par(self):
        for m in mensajes(11, cantidad=8):
            c = S16.codificar_palabra(m)
            for i, j in itertools.combinations(range(16), 2):
                r = volteado(c, i, j)
                p = S16.corregir_palabra(r)
                self.assertEqual((p.estado, p.posicion, p.c_corregida), ("no_corregible", None, r))
                self.assertTrue(any(p.sindrome))
                self.assertEqual(sum(p.sindrome) % 2, 0)
                self.assertIsNone(S16.posicion_error(p.sindrome))

    def test_distancia_minima_4(self):
        for v in range(1, 1 << 11):  # los 2047 mensajes no nulos
            c = S16.codificar_palabra(bits(v, 11))
            self.assertGreaterEqual(sum(c), 4, bin(v))

    def test_ida_y_vuelta_y_un_bit_en_el_canal(self):
        for largo in (0, 1, 2, 1400):
            d = random.Random(largo + 2).randbytes(largo)
            marco = fec.codificar(d, S16, secuencia=77)
            self.assertEqual(fec.decodificar(marco)[0], d)
            palabras = fec.decodificar(marco)[1].palabras
            salida, r = fec.decodificar(canal.inyectar(marco, [8 + 1 * 16 + 5]))
            self.assertEqual(salida, d)
            self.assertEqual((r.corregidas, r.no_corregibles, r.posiciones_corregidas),
                             (1, 0, [(1, 5)]))
            self.assertEqual(r.palabras, palabras)
            if palabras > 40:
                dos = canal.inyectar(marco, [8 + 40 * 16 + 0, 8 + 40 * 16 + 1])
                _, r2 = fec.decodificar(dos)
                self.assertEqual((r2.no_corregibles, r2.corregidas), (1, 0))

    def test_decodificar_1400_B_en_menos_de_150_ms(self):
        d = random.Random(21).randbytes(1400)
        marco = fec.codificar(d, S16)
        fec.decodificar(marco)  # calienta tablas
        ts = []
        for _ in range(5):
            t0 = time.perf_counter()
            salida, _ = fec.decodificar(marco)
            ts.append(time.perf_counter() - t0)
        self.assertEqual(salida, d)
        self.assertLess(sum(ts) / len(ts), 0.150)

    def test_tablas_pico_de_memoria_menor_de_6_MB(self):
        tracemalloc.start()
        try:
            c = fec.CodigoLineal("secded_16_11_medida", [list(f) for f in S16.P])
            c._tablas()
            _, pico = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(pico, 6 * 1024 * 1024)


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
