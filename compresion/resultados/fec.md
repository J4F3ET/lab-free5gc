# Banco FEC: cuánto reduce el BER cada código (generado por `compresion/banco_fec.py`)

300 cargas aleatorias de 200 B por código y punto, semilla 1. Canal BSC independiente por bit (barrido) y Gilbert-Elliott (ráfaga). El byte del id de código nunca se voltea.

- BER entrada = bits volteados / bits del marco sin el id.
- BER residual = bits de datos distintos tras decodificar / bits de datos. Un paquete inválido cuenta todos sus bits de datos como erróneos.
- Categorías (cada paquete cae en una sola, en este orden): inválido (el decodificador lanza ValueError); no corregible (algún síndrome sin columna: el decodificador lo avisa); corregido mal (datos distintos sin aviso); corregido bien (síndrome distinto de cero y datos idénticos); limpio (sin síndrome). En «sin código», «corregido mal» son los paquetes con datos dañados.
- p_palabra_fallida = P(una palabra código recibe 2 o más errores). Teórica = 1-(1-p)^n-np(1-p)^(n-1) con errores independientes; observada = palabras con ≥2 errores / palabras del barrido. Con ráfaga no hay teórica (los errores no son independientes).

## 1. Curva BER residual (canal BSC)

| canal | BER entrada | sin código | repetición 5/1 (5×) | Hamming 7/4 (1,75×) | SECDED 8/4 (2×) |
|---|---|---|---|---|---|
| 0.0001 | 8.33e-05 | 8.33e-05 | 0 | 0 | 0 |
| 0.0003 | 3.46e-04 | 3.46e-04 | 0 | 6.25e-06 | 0 |
| 0.001 | 9.60e-04 | 9.60e-04 | 2.08e-06 | 0 | 4.17e-06 |
| 0.003 | 3.08e-03 | 3.08e-03 | 3.37e-03 | 3.43e-03 | 6.88e-05 |
| 0.01 | 1.01e-02 | 1.01e-02 | 1.04e-02 | 1.09e-02 | 1.07e-02 |
| 0.03 | 2.99e-02 | 2.99e-02 | 6.65e-02 | 8.35e-02 | 8.87e-02 |
| 0.1 | 1.00e-01 | 1.00e-01 | 5.11e-01 | 4.87e-01 | 5.75e-01 |

## 2. Detalle por código y punto

| código | n/k | p | BER entrada | BER residual | limpios | corregidos bien | corregidos mal | no corregibles | inválidos | p_palabra_fallida teórica / observada |
|---|---|---|---|---|---|---|---|---|---|---|
| sin_codigo | 1/1 | 0.0001 | 8.33e-05 | 8.33e-05 | 261 | 0 | 39 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.0001 | 9.97e-05 | 0 | 144 | 156 | 0 | 0 | 0 | 1.00e-07 / 0 |
| hamming_7_4 | 7/4 | 0.0001 | 8.29e-05 | 0 | 237 | 63 | 0 | 0 | 0 | 2.10e-07 / 0 |
| secded_8_4 | 8/4 | 0.0001 | 7.97e-05 | 0 | 231 | 69 | 0 | 0 | 0 | 2.80e-07 / 0 |
| sin_codigo | 1/1 | 0.0003 | 3.46e-04 | 3.46e-04 | 177 | 0 | 123 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.0003 | 3.08e-04 | 0 | 27 | 273 | 0 | 0 | 0 | 8.99e-07 / 0 |
| hamming_7_4 | 7/4 | 0.0003 | 3.19e-04 | 6.25e-06 | 118 | 181 | 1 | 0 | 0 | 1.89e-06 / 8.17e-06 |
| secded_8_4 | 8/4 | 0.0003 | 3.15e-04 | 0 | 104 | 196 | 0 | 0 | 0 | 2.52e-06 / 0 |
| sin_codigo | 1/1 | 0.001 | 9.60e-04 | 9.60e-04 | 57 | 0 | 243 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.001 | 9.89e-04 | 2.08e-06 | 0 | 297 | 0 | 3 | 0 | 9.98e-06 / 6.13e-06 |
| hamming_7_4 | 7/4 | 0.001 | 9.61e-04 | 0 | 21 | 279 | 0 | 0 | 0 | 2.09e-05 / 0 |
| secded_8_4 | 8/4 | 0.001 | 9.72e-04 | 4.17e-06 | 13 | 285 | 0 | 2 | 0 | 2.79e-05 / 1.63e-05 |
| sin_codigo | 1/1 | 0.003 | 3.08e-03 | 3.08e-03 | 1 | 0 | 299 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.003 | 3.01e-03 | 3.37e-03 | 0 | 260 | 0 | 39 | 1 | 8.95e-05 / 9.19e-05 |
| hamming_7_4 | 7/4 | 0.003 | 2.99e-03 | 3.43e-03 | 0 | 276 | 23 | 0 | 1 | 1.87e-04 / 2.04e-04 |
| secded_8_4 | 8/4 | 0.003 | 3.00e-03 | 6.88e-05 | 0 | 266 | 0 | 34 | 0 | 2.49e-04 / 2.78e-04 |
| sin_codigo | 1/1 | 0.01 | 1.01e-02 | 1.01e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.01 | 1.01e-02 | 1.04e-02 | 0 | 59 | 0 | 238 | 3 | 9.80e-04 / 9.68e-04 |
| hamming_7_4 | 7/4 | 0.01 | 1.01e-02 | 1.09e-02 | 0 | 127 | 170 | 0 | 3 | 2.03e-03 / 2.07e-03 |
| secded_8_4 | 8/4 | 0.01 | 1.00e-02 | 1.07e-02 | 0 | 91 | 1 | 205 | 3 | 2.69e-03 / 2.72e-03 |
| sin_codigo | 1/1 | 0.03 | 2.99e-02 | 2.99e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.03 | 2.99e-02 | 6.65e-02 | 0 | 0 | 0 | 281 | 19 | 8.47e-03 / 8.40e-03 |
| hamming_7_4 | 7/4 | 0.03 | 2.99e-02 | 8.35e-02 | 0 | 0 | 277 | 0 | 23 | 1.71e-02 / 1.71e-02 |
| secded_8_4 | 8/4 | 0.03 | 2.99e-02 | 8.87e-02 | 0 | 0 | 0 | 275 | 25 | 2.23e-02 / 2.27e-02 |
| sin_codigo | 1/1 | 0.1 | 1.00e-01 | 1.00e-01 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.1 | 9.97e-02 | 5.11e-01 | 0 | 0 | 0 | 152 | 148 | 8.15e-02 / 8.14e-02 |
| hamming_7_4 | 7/4 | 0.1 | 9.99e-02 | 4.87e-01 | 0 | 0 | 165 | 0 | 135 | 1.50e-01 / 1.50e-01 |
| secded_8_4 | 8/4 | 0.1 | 9.99e-02 | 5.75e-01 | 0 | 0 | 0 | 135 | 165 | 1.87e-01 / 1.86e-01 |

## 3. Canal de ráfaga (errores agrupados)

| código | n/k | p | BER entrada | BER residual | limpios | corregidos bien | corregidos mal | no corregibles | inválidos | p_palabra_fallida teórica / observada |
|---|---|---|---|---|---|---|---|---|---|---|
| sin_codigo | 1/1 | ráfaga (0.3, 0.01, 0.2) | 1.39e-02 | 1.39e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | ráfaga (0.3, 0.01, 0.2) | 1.43e-02 | 9.67e-02 | 0 | 0 | 0 | 273 | 27 | — / 1.72e-02 |
| hamming_7_4 | 7/4 | ráfaga (0.3, 0.01, 0.2) | 1.41e-02 | 8.43e-02 | 0 | 0 | 278 | 0 | 22 | — / 2.52e-02 |
| secded_8_4 | 8/4 | ráfaga (0.3, 0.01, 0.2) | 1.41e-02 | 5.59e-02 | 0 | 0 | 0 | 286 | 14 | — / 2.90e-02 |

## Nota de lectura

- Sin código, el BER residual es el de entrada (9.60e-04 a p=1e-3). Hamming (7,4) lo baja a 0 y SECDED (8,4) a 4.17e-06, pagando 1,75× y 2× de bits.
- Hamming (7,4) con 2 errores en una palabra: el síndrome no es cero, así que cuenta como «corregido», pero voltea un tercer bit. A p=3e-2 hay 277 paquetes corregidos mal de 300 sin ningún aviso.
- SECDED (8,4) deja esos casos como «no corregibles» (síndrome de peso par sin columna): a p=3e-2 marca 275 paquetes como detectados y solo 0 salen mal sin aviso.
- Cruce: a p=3e-3 Hamming y repetición 5/1 ya empeoran el BER (3.43e-03 y 3.37e-03 frente a 2.99e-03 sin código). Repetición 5/1 detecta 2 y 3 errores sin corregirlos: a p=3e-2 deja 281 paquetes no corregibles y 0 corregidos mal.
- Ráfaga (BER de entrada medio 1.41e-02): Hamming falla en 2.52e-02 de sus palabras, frente a 2.07e-03 con errores independientes al BER más cercano (p=0.01), y su BER residual es 8.43e-02 frente a 1.09e-02.
