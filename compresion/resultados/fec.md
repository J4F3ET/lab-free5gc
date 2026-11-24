# Banco FEC: cuánto reduce el BER cada código (generado por `compresion/banco_fec.py`)

300 cargas aleatorias de 200 B por código y punto, semilla 1. Canal BSC independiente por bit (barrido) y Gilbert-Elliott (ráfaga). El byte del id de código nunca se voltea.

- BER entrada = bits volteados / bits del marco sin el id.
- BER residual = bits de datos distintos tras decodificar / bits de datos. Un paquete inválido cuenta todos sus bits de datos como erróneos.
- Categorías (cada paquete cae en una sola, en este orden): inválido (el decodificador lanza ValueError); no corregible (algún síndrome sin columna: el decodificador lo avisa); corregido mal (datos distintos sin aviso); corregido bien (síndrome distinto de cero y datos idénticos); limpio (sin síndrome). En «sin código», «corregido mal» son los paquetes con datos dañados.
- p_palabra_fallida = P(una palabra código recibe 2 o más errores). Teórica = 1-(1-p)^n-np(1-p)^(n-1) con errores independientes; observada = palabras con ≥2 errores / palabras del barrido. Con ráfaga no hay teórica (los errores no son independientes).

## 1. Curva BER residual (canal BSC)

| canal | BER entrada | sin código | repetición 5/1 (5×) | Hamming 7/4 (1,75×) | Hamming 15/11 (1,36×) | SECDED 8/4 (2×) | SECDED 16/11 (1,45×) |
|---|---|---|---|---|---|---|---|
| 0.0001 | 8.33e-05 | 8.33e-05 | 0 | 0 | 0 | 0 | 0 |
| 0.0003 | 3.46e-04 | 3.46e-04 | 0 | 6.25e-06 | 0 | 0 | 4.17e-06 |
| 0.001 | 9.60e-04 | 9.60e-04 | 2.08e-06 | 0 | 1.46e-05 | 4.17e-06 | 6.25e-06 |
| 0.003 | 3.08e-03 | 3.08e-03 | 3.37e-03 | 3.43e-03 | 3.56e-03 | 6.88e-05 | 3.52e-03 |
| 0.01 | 1.01e-02 | 1.01e-02 | 1.04e-02 | 1.09e-02 | 2.19e-02 | 1.07e-02 | 1.80e-02 |
| 0.03 | 2.99e-02 | 2.99e-02 | 6.65e-02 | 8.35e-02 | 1.34e-01 | 8.87e-02 | 1.40e-01 |
| 0.1 | 1.00e-01 | 1.00e-01 | 5.11e-01 | 4.87e-01 | 6.87e-01 | 5.75e-01 | 6.75e-01 |

## 2. Detalle por código y punto

| código | n/k | p | BER entrada | BER residual | limpios | corregidos bien | corregidos mal | no corregibles | inválidos | p_palabra_fallida teórica / observada |
|---|---|---|---|---|---|---|---|---|---|---|
| sin_codigo | 1/1 | 0.0001 | 8.33e-05 | 8.33e-05 | 261 | 0 | 39 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.0001 | 9.97e-05 | 0 | 144 | 156 | 0 | 0 | 0 | 1.00e-07 / 0 |
| hamming_7_4 | 7/4 | 0.0001 | 8.29e-05 | 0 | 237 | 63 | 0 | 0 | 0 | 2.10e-07 / 0 |
| hamming_15_11 | 15/11 | 0.0001 | 8.48e-05 | 0 | 248 | 52 | 0 | 0 | 0 | 1.05e-06 / 0 |
| secded_8_4 | 8/4 | 0.0001 | 7.97e-05 | 0 | 231 | 69 | 0 | 0 | 0 | 2.80e-07 / 0 |
| secded_16_11 | 16/11 | 0.0001 | 8.25e-05 | 0 | 246 | 54 | 0 | 0 | 0 | 1.20e-06 / 0 |
| sin_codigo | 1/1 | 0.0003 | 3.46e-04 | 3.46e-04 | 177 | 0 | 123 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.0003 | 3.08e-04 | 0 | 27 | 273 | 0 | 0 | 0 | 8.99e-07 / 0 |
| hamming_7_4 | 7/4 | 0.0003 | 3.19e-04 | 6.25e-06 | 118 | 181 | 1 | 0 | 0 | 1.89e-06 / 8.17e-06 |
| hamming_15_11 | 15/11 | 0.0003 | 3.41e-04 | 0 | 144 | 156 | 0 | 0 | 0 | 9.43e-06 / 0 |
| secded_8_4 | 8/4 | 0.0003 | 3.15e-04 | 0 | 104 | 196 | 0 | 0 | 0 | 2.52e-06 / 0 |
| secded_16_11 | 16/11 | 0.0003 | 3.36e-04 | 4.17e-06 | 136 | 163 | 0 | 1 | 0 | 1.08e-05 / 2.24e-05 |
| sin_codigo | 1/1 | 0.001 | 9.60e-04 | 9.60e-04 | 57 | 0 | 243 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.001 | 9.89e-04 | 2.08e-06 | 0 | 297 | 0 | 3 | 0 | 9.98e-06 / 6.13e-06 |
| hamming_7_4 | 7/4 | 0.001 | 9.61e-04 | 0 | 21 | 279 | 0 | 0 | 0 | 2.09e-05 / 0 |
| hamming_15_11 | 15/11 | 0.001 | 9.54e-04 | 1.46e-05 | 38 | 259 | 3 | 0 | 0 | 1.04e-04 / 6.71e-05 |
| secded_8_4 | 8/4 | 0.001 | 9.72e-04 | 4.17e-06 | 13 | 285 | 0 | 2 | 0 | 2.79e-05 / 1.63e-05 |
| secded_16_11 | 16/11 | 0.001 | 9.59e-04 | 6.25e-06 | 32 | 265 | 0 | 3 | 0 | 1.19e-04 / 6.71e-05 |
| sin_codigo | 1/1 | 0.003 | 3.08e-03 | 3.08e-03 | 1 | 0 | 299 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.003 | 3.01e-03 | 3.37e-03 | 0 | 260 | 0 | 39 | 1 | 8.95e-05 / 9.19e-05 |
| hamming_7_4 | 7/4 | 0.003 | 2.99e-03 | 3.43e-03 | 0 | 276 | 23 | 0 | 1 | 1.87e-04 / 2.04e-04 |
| hamming_15_11 | 15/11 | 0.003 | 3.04e-03 | 3.56e-03 | 0 | 256 | 43 | 0 | 1 | 9.21e-04 / 1.14e-03 |
| secded_8_4 | 8/4 | 0.003 | 3.00e-03 | 6.88e-05 | 0 | 266 | 0 | 34 | 0 | 2.49e-04 / 2.78e-04 |
| secded_16_11 | 16/11 | 0.003 | 3.04e-03 | 3.52e-03 | 0 | 244 | 1 | 54 | 1 | 1.05e-03 / 1.43e-03 |
| sin_codigo | 1/1 | 0.01 | 1.01e-02 | 1.01e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.01 | 1.01e-02 | 1.04e-02 | 0 | 59 | 0 | 238 | 3 | 9.80e-04 / 9.68e-04 |
| hamming_7_4 | 7/4 | 0.01 | 1.01e-02 | 1.09e-02 | 0 | 127 | 170 | 0 | 3 | 2.03e-03 / 2.07e-03 |
| hamming_15_11 | 15/11 | 0.01 | 1.01e-02 | 2.19e-02 | 0 | 70 | 224 | 0 | 6 | 9.63e-03 / 9.55e-03 |
| secded_8_4 | 8/4 | 0.01 | 1.00e-02 | 1.07e-02 | 0 | 91 | 1 | 205 | 3 | 2.69e-03 / 2.72e-03 |
| secded_16_11 | 16/11 | 0.01 | 1.01e-02 | 1.80e-02 | 0 | 55 | 3 | 237 | 5 | 1.09e-02 / 1.07e-02 |
| sin_codigo | 1/1 | 0.03 | 2.99e-02 | 2.99e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.03 | 2.99e-02 | 6.65e-02 | 0 | 0 | 0 | 281 | 19 | 8.47e-03 / 8.40e-03 |
| hamming_7_4 | 7/4 | 0.03 | 2.99e-02 | 8.35e-02 | 0 | 0 | 277 | 0 | 23 | 1.71e-02 / 1.71e-02 |
| hamming_15_11 | 15/11 | 0.03 | 3.00e-02 | 1.34e-01 | 0 | 0 | 264 | 0 | 36 | 7.30e-02 / 7.27e-02 |
| secded_8_4 | 8/4 | 0.03 | 2.99e-02 | 8.87e-02 | 0 | 0 | 0 | 275 | 25 | 2.23e-02 / 2.27e-02 |
| secded_16_11 | 16/11 | 0.03 | 2.99e-02 | 1.40e-01 | 0 | 0 | 0 | 261 | 39 | 8.18e-02 / 8.15e-02 |
| sin_codigo | 1/1 | 0.1 | 1.00e-01 | 1.00e-01 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | 0.1 | 9.97e-02 | 5.11e-01 | 0 | 0 | 0 | 152 | 148 | 8.15e-02 / 8.14e-02 |
| hamming_7_4 | 7/4 | 0.1 | 9.99e-02 | 4.87e-01 | 0 | 0 | 165 | 0 | 135 | 1.50e-01 / 1.50e-01 |
| hamming_15_11 | 15/11 | 0.1 | 1.00e-01 | 6.87e-01 | 0 | 0 | 105 | 0 | 195 | 4.51e-01 / 4.51e-01 |
| secded_8_4 | 8/4 | 0.1 | 9.99e-02 | 5.75e-01 | 0 | 0 | 0 | 135 | 165 | 1.87e-01 / 1.86e-01 |
| secded_16_11 | 16/11 | 0.1 | 1.00e-01 | 6.75e-01 | 0 | 0 | 0 | 107 | 193 | 4.85e-01 / 4.83e-01 |

## 3. Canal de ráfaga (errores agrupados)

| código | n/k | p | BER entrada | BER residual | limpios | corregidos bien | corregidos mal | no corregibles | inválidos | p_palabra_fallida teórica / observada |
|---|---|---|---|---|---|---|---|---|---|---|
| sin_codigo | 1/1 | ráfaga (0.3, 0.01, 0.2) | 1.39e-02 | 1.39e-02 | 0 | 0 | 300 | 0 | 0 | — / — |
| repeticion_5_1 | 5/1 | ráfaga (0.3, 0.01, 0.2) | 1.43e-02 | 9.67e-02 | 0 | 0 | 0 | 273 | 27 | — / 1.72e-02 |
| hamming_7_4 | 7/4 | ráfaga (0.3, 0.01, 0.2) | 1.41e-02 | 8.43e-02 | 0 | 0 | 278 | 0 | 22 | — / 2.52e-02 |
| hamming_15_11 | 15/11 | ráfaga (0.3, 0.01, 0.2) | 1.41e-02 | 9.92e-02 | 0 | 0 | 274 | 0 | 26 | — / 5.50e-02 |
| secded_8_4 | 8/4 | ráfaga (0.3, 0.01, 0.2) | 1.41e-02 | 5.59e-02 | 0 | 0 | 0 | 286 | 14 | — / 2.90e-02 |
| secded_16_11 | 16/11 | ráfaga (0.3, 0.01, 0.2) | 1.40e-02 | 8.08e-02 | 0 | 0 | 2 | 277 | 21 | — / 5.79e-02 |

## 4. Bytes en el aire por código

Medido codificando cargas de cada tamaño y comparando con `registro_fec.tamano_en_cable` (incluye el id de FEC y los 4 B de secuencia y largo). «Cable» suma además los 28 B de IP+UDP del túnel, que la función no cuenta. El MTU de cmp0 es `registro_fec.mtu_con_fec`; una carga mayor que ese MTU no la produce cmp0 con ese código y se muestra solo para medir el factor.

| carga (B) | código | n/k | marco FEC (B) | cable con IP+UDP (B) | factor marco/carga | factor cable/carga | MTU de cmp0 (B) |
|---|---|---|---|---|---|---|---|
| 40 | repeticion_5_1 | 5/1 | 221 | 249 | 5.525 | 6.225 | 247 |
| 40 | hamming_7_4 | 7/4 | 78 | 106 | 1.950 | 2.650 | 780 |
| 40 | hamming_15_11 | 15/11 | 61 | 89 | 1.525 | 2.225 | 1011 |
| 40 | secded_8_4 | 8/4 | 89 | 117 | 2.225 | 2.925 | 677 |
| 40 | secded_16_11 | 16/11 | 65 | 93 | 1.625 | 2.325 | 945 |
| 200 | repeticion_5_1 | 5/1 | 1021 | 1049 | 5.105 | 5.245 | 247 |
| 200 | hamming_7_4 | 7/4 | 358 | 386 | 1.790 | 1.930 | 780 |
| 200 | hamming_15_11 | 15/11 | 281 | 309 | 1.405 | 1.545 | 1011 |
| 200 | secded_8_4 | 8/4 | 409 | 437 | 2.045 | 2.185 | 677 |
| 200 | secded_16_11 | 16/11 | 299 | 327 | 1.495 | 1.635 | 945 |
| 780 | repeticion_5_1 | 5/1 | 3921 | 3949 | 5.027 | 5.063 | 247 |
| 780 | hamming_7_4 | 7/4 | 1373 | 1401 | 1.760 | 1.796 | 780 |
| 780 | hamming_15_11 | 15/11 | 1072 | 1100 | 1.374 | 1.410 | 1011 |
| 780 | secded_8_4 | 8/4 | 1569 | 1597 | 2.012 | 2.047 | 677 |
| 780 | secded_16_11 | 16/11 | 1143 | 1171 | 1.465 | 1.501 | 945 |
| 1400 | repeticion_5_1 | 5/1 | 7021 | 7049 | 5.015 | 5.035 | 247 |
| 1400 | hamming_7_4 | 7/4 | 2458 | 2486 | 1.756 | 1.776 | 780 |
| 1400 | hamming_15_11 | 15/11 | 1918 | 1946 | 1.370 | 1.390 | 1011 |
| 1400 | secded_8_4 | 8/4 | 2809 | 2837 | 2.006 | 2.026 | 677 |
| 1400 | secded_16_11 | 16/11 | 2045 | 2073 | 1.461 | 1.481 | 945 |

El factor tiende a n/k para cargas grandes y se aleja en las chicas por el overhead fijo (1 B de id, 4 B de cabecera, 28 B de túnel y el relleno hasta el byte).

## Nota de lectura

- Sin código, el BER residual es el de entrada (9.60e-04 a p=1e-3). BER residual a p=1e-3: repetición 5/1 (5×) 2.08e-06, Hamming 7/4 (1,75×) 0, Hamming 15/11 (1,36×) 1.46e-05, SECDED 8/4 (2×) 4.17e-06, SECDED 16/11 (1,45×) 6.25e-06.
- Dónde cruza cada código (primer punto del barrido con BER residual ≥ BER entrada): repetición 5/1 (5×) p=0.003, Hamming 7/4 (1,75×) p=0.003, Hamming 15/11 (1,36×) p=0.003, SECDED 8/4 (2×) p=0.01, SECDED 16/11 (1,45×) p=0.003.
- Hamming 7/4 y 15/11 con 2 errores en una palabra: el síndrome no es cero y es una columna de H, así que se cuenta como «corregido» y se voltea un tercer bit. A p=3e-2 hay 277 paquetes corregidos mal de 300 con 7/4 y 264 con 15/11, sin ningún aviso.
- SECDED 8/4 y 16/11 dejan esos casos como «no corregibles» (síndrome de peso par): a p=3e-2 marcan 275 y 261 paquetes como detectados, y solo 0 y 0 salen mal sin aviso.
- 15/11 y 16/11 cuestan menos banda (1,36× y 1,45× frente a 1,75× y 2×), pero no son mejores en todo: a p=1e-3 dejan 1.46e-05 y 6.25e-06 de BER residual (7/4 deja 0 y 8/4 4.17e-06), y con ruido alto sufren más palabras con 2 o más errores: a p=3e-2 la fracción observada de palabras fallidas es 7.27e-02 (15/11) frente a 1.71e-02 (7/4), y 8.15e-02 (16/11) frente a 2.27e-02 (8/4).
- Ráfaga (BER de entrada medio 1.41e-02): BER residual repetición 5/1 (5×) 9.67e-02, Hamming 7/4 (1,75×) 8.43e-02, Hamming 15/11 (1,36×) 9.92e-02, SECDED 8/4 (2×) 5.59e-02, SECDED 16/11 (1,45×) 8.08e-02. Hamming 7/4 falla en 2.52e-02 de sus palabras frente a 2.07e-03 con errores independientes al BER más cercano (p=0.01).
