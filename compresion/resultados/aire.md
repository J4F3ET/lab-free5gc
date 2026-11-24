# Recompensa en bytes del aire (generado por `evaluar.py aire`)

Entrenamiento: 3600 paquetes (semilla 1). Prueba: 3600 paquetes (semilla 2). Brazos: ninguno, zstd_dict, deflate_flujo, predictivo_audio; reinicio de flujo 16; ráfaga 8.

aire(L) = 33 + f·(4 + L) bytes por paquete, con L = bytes del marco que sale del emisor (incluye la cabecera ROHC y el id de códec). «ahorro aire %» compara con no comprimir (paquete crudo) con el mismo f. «ahorro carga %» = 1 − bytes del marco / bytes crudos, sin el overhead fijo ni la paridad.

Criterio de elección (solo entrenamiento): % mal con 1 % de pérdida ≤ 2; de esas, ahorro en el aire a no más de 0,5 puntos del mejor; de esas, la que menos intenta comprimir. La línea base usa la recompensa vieja (de carga, costo fijo 0,01) y sus decisiones no dependen de f.

## 1. Malla en ENTRENAMIENTO

| f (factor FEC) | política | γ | c | costo CPU | ahorro aire % | ahorro carga % | bytes en el aire | intentos | respaldos | veces ninguno | CPU µs/paq | % mal con 1 % pérdida |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,0 (sin FEC) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 21.92 | 23.04 | 2148179 | 1526 | 329 | 2074 | 45.1 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.005 | 0.002 | 21.92 | 23.04 | 2148189 | 1834 | 638 | 1766 | 64.5 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.005 | 0.005 | 21.92 | 23.04 | 2148189 | 1385 | 189 | 2215 | 42.3 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.005 | 0.01 | 21.92 | 23.04 | 2148189 | 1267 | 71 | 2333 | 42.9 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.01 | 0.002 | 21.92 | 23.04 | 2148170 | 2408 | 1209 | 1192 | 87.1 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.01 | 0.005 | 21.92 | 23.04 | 2148150 | 1792 | 591 | 1808 | 57.0 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.01 | 0.01 | 21.92 | 23.04 | 2148190 | 1385 | 189 | 2215 | 45.4 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.02 | 0.002 | 21.93 | 23.05 | 2148006 | 2739 | 1539 | 861 | 108.7 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.02 | 0.005 | 21.93 | 23.05 | 2148006 | 2246 | 1046 | 1354 | 75.8 | 3.82 |
| 1,0 (sin FEC) | aire | 0.995 | 0.02 | 0.01 | 21.93 | 23.04 | 2148026 | 1717 | 519 | 1883 | 58.8 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.005 | 0.002 | 21.92 | 23.04 | 2148189 | 1579 | 383 | 2021 | 55.7 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.005 | 0.005 | 21.92 | 23.04 | 2148189 | 1294 | 98 | 2306 | 41.9 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.005 | 0.01 | 21.92 | 23.04 | 2148189 | 1231 | 35 | 2369 | 39.6 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.01 | 0.002 | 21.92 | 23.04 | 2148154 | 2209 | 1009 | 1391 | 70.1 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.01 | 0.005 | 21.92 | 23.04 | 2148169 | 1548 | 350 | 2052 | 52.3 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.01 | 0.01 | 21.92 | 23.04 | 2148189 | 1294 | 98 | 2306 | 45.0 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.02 | 0.002 | 21.92 | 23.04 | 2148159 | 2590 | 1390 | 1010 | 94.9 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.02 | 0.005 | 21.92 | 23.04 | 2148169 | 1936 | 738 | 1664 | 63.6 | 3.82 |
| 1,0 (sin FEC) | aire | 0.999 | 0.02 | 0.01 | 21.92 | 23.04 | 2148159 | 1532 | 333 | 2068 | 53.8 | 3.82 |
| 1,364 (Hamming 15/11) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 22.18 | 23.04 | 2886873 | 1526 | 329 | 2074 | 55.6 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.005 | 0.002 | 22.18 | 23.04 | 2886859 | 2178 | 979 | 1422 | 70.5 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.005 | 0.005 | 22.18 | 23.04 | 2886887 | 1489 | 293 | 2111 | 45.0 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.005 | 0.01 | 22.18 | 23.04 | 2886887 | 1306 | 110 | 2294 | 42.6 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.01 | 0.002 | 22.18 | 23.04 | 2886874 | 2565 | 1368 | 1035 | 90.4 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.01 | 0.005 | 22.18 | 23.04 | 2886840 | 2013 | 813 | 1587 | 64.7 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.01 | 0.01 | 22.18 | 23.04 | 2886888 | 1489 | 293 | 2111 | 46.6 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.02 | 0.002 | 22.19 | 23.05 | 2886651 | 2819 | 1620 | 781 | 97.1 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.02 | 0.005 | 22.18 | 23.04 | 2886678 | 2425 | 1228 | 1175 | 80.1 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.995 | 0.02 | 0.01 | 22.18 | 23.04 | 2886678 | 1916 | 719 | 1684 | 55.7 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.005 | 0.002 | 22.18 | 23.04 | 2886887 | 1768 | 572 | 1832 | 54.8 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.005 | 0.005 | 22.18 | 23.04 | 2886887 | 1354 | 158 | 2246 | 37.1 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.005 | 0.01 | 22.18 | 23.04 | 2886887 | 1249 | 53 | 2351 | 34.6 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.01 | 0.002 | 22.18 | 23.04 | 2886887 | 2284 | 1088 | 1316 | 71.1 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.01 | 0.005 | 22.18 | 23.04 | 2886818 | 1841 | 639 | 1759 | 49.2 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.01 | 0.01 | 22.18 | 23.04 | 2886887 | 1354 | 158 | 2246 | 36.6 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.02 | 0.002 | 22.18 | 23.04 | 2886832 | 2741 | 1541 | 859 | 82.3 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.02 | 0.005 | 22.18 | 23.04 | 2886887 | 2119 | 923 | 1481 | 62.1 | 3.82 |
| 1,364 (Hamming 15/11) | aire | 0.999 | 0.02 | 0.01 | 22.18 | 23.04 | 2886873 | 1659 | 462 | 1941 | 48.7 | 3.82 |
| 1,75 (Hamming 7/4) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 22.34 | 23.04 | 3670213 | 1526 | 329 | 2074 | 44.0 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.005 | 0.002 | 22.34 | 23.04 | 3670231 | 2254 | 1058 | 1346 | 71.4 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.005 | 0.005 | 22.34 | 23.04 | 3670213 | 1671 | 474 | 1929 | 51.9 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.005 | 0.01 | 22.34 | 23.04 | 3670231 | 1354 | 158 | 2246 | 37.0 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.01 | 0.002 | 22.34 | 23.04 | 3670198 | 2700 | 1502 | 900 | 80.4 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.01 | 0.005 | 22.34 | 23.04 | 3670232 | 2087 | 891 | 1513 | 65.5 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.01 | 0.01 | 22.34 | 23.04 | 3670189 | 1694 | 495 | 1906 | 46.9 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.02 | 0.002 | 22.34 | 23.05 | 3669937 | 2873 | 1674 | 727 | 88.7 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.02 | 0.005 | 22.34 | 23.05 | 3669928 | 2603 | 1403 | 997 | 82.7 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.995 | 0.02 | 0.01 | 22.34 | 23.04 | 3669980 | 2087 | 891 | 1513 | 62.4 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.005 | 0.002 | 22.34 | 23.04 | 3670143 | 2281 | 1079 | 1319 | 59.8 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.005 | 0.005 | 22.34 | 23.04 | 3670231 | 1426 | 230 | 2174 | 41.9 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.005 | 0.01 | 22.34 | 23.04 | 3670196 | 1357 | 158 | 2243 | 36.0 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.01 | 0.002 | 22.34 | 23.04 | 3670178 | 2588 | 1388 | 1012 | 72.5 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.01 | 0.005 | 22.34 | 23.04 | 3670231 | 1789 | 593 | 1811 | 54.0 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.01 | 0.01 | 22.34 | 23.04 | 3670231 | 1426 | 230 | 2174 | 39.1 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.02 | 0.002 | 22.34 | 23.04 | 3670152 | 2832 | 1631 | 768 | 89.1 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.02 | 0.005 | 22.34 | 23.04 | 3670222 | 2323 | 1126 | 1277 | 72.6 | 3.82 |
| 1,75 (Hamming 7/4) | aire | 0.999 | 0.02 | 0.01 | 22.34 | 23.04 | 3670231 | 1789 | 593 | 1811 | 60.0 | 3.82 |
| 2,0 (SECDED 8/4) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 22.41 | 23.04 | 4177558 | 1526 | 329 | 2074 | 46.1 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.005 | 0.002 | 22.41 | 23.04 | 4177538 | 2456 | 1257 | 1144 | 76.3 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.005 | 0.005 | 22.41 | 23.04 | 4177528 | 1833 | 634 | 1767 | 50.3 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.005 | 0.01 | 22.41 | 23.04 | 4177578 | 1385 | 189 | 2215 | 42.4 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.01 | 0.002 | 22.41 | 23.04 | 4177460 | 2813 | 1610 | 787 | 92.7 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.01 | 0.005 | 22.41 | 23.04 | 4177470 | 2348 | 1146 | 1252 | 78.7 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.01 | 0.01 | 22.41 | 23.04 | 4177500 | 1822 | 621 | 1778 | 56.9 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.02 | 0.002 | 22.41 | 23.05 | 4177222 | 2903 | 1703 | 697 | 98.8 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.02 | 0.005 | 22.41 | 23.04 | 4177262 | 2641 | 1443 | 959 | 104.5 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.995 | 0.02 | 0.01 | 22.41 | 23.05 | 4177182 | 2292 | 1090 | 1308 | 67.8 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.005 | 0.002 | 22.41 | 23.04 | 4177538 | 2255 | 1057 | 1345 | 63.7 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.005 | 0.005 | 22.41 | 23.04 | 4177518 | 1706 | 507 | 1894 | 47.9 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.005 | 0.01 | 22.41 | 23.04 | 4177578 | 1294 | 98 | 2306 | 37.8 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.01 | 0.002 | 22.41 | 23.04 | 4177508 | 2668 | 1468 | 932 | 93.4 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.01 | 0.005 | 22.41 | 23.04 | 4177458 | 2160 | 957 | 1440 | 76.8 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.01 | 0.01 | 22.41 | 23.04 | 4177518 | 1609 | 410 | 1991 | 44.6 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.02 | 0.002 | 22.41 | 23.04 | 4177558 | 2808 | 1611 | 792 | 86.7 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.02 | 0.005 | 22.41 | 23.04 | 4177508 | 2498 | 1298 | 1102 | 80.7 | 3.82 |
| 2,0 (SECDED 8/4) | aire | 0.999 | 0.02 | 0.01 | 22.41 | 23.04 | 4177538 | 1954 | 756 | 1646 | 60.0 | 3.82 |

## 2. Prueba (solo línea base y elegida)

| f (factor FEC) | política | γ | c | costo CPU | ahorro aire % | ahorro carga % | bytes en el aire | intentos | respaldos | CPU µs/paq | % mal con 1 % pérdida | Δ ahorro aire vs. línea base (puntos) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,0 (sin FEC) | sin compresión | — | — | — | 0.0 | 0.0 | 2748608 | 0 | 0 | 0.0 | 0.0 | 0.0 |
| 1,0 (sin FEC) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 21.27 | 22.36 | 2163903 | 1471 | 274 | 63.2 | 3.62 | 0.0 |
| 1,0 (sin FEC) | elegida con entrenamiento | 0.999 | 0.005 | 0.01 | 21.27 | 22.36 | 2163903 | 1234 | 37 | 40.4 | 3.62 | 0.0 |
| 1,364 (Hamming 15/11) | sin compresión | — | — | — | 0.0 | 0.0 | 3705858 | 0 | 0 | 0.0 | 0.0 | 0.0 |
| 1,364 (Hamming 15/11) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 21.52 | 22.36 | 2908320 | 1471 | 274 | 48.7 | 3.62 | 0.0 |
| 1,364 (Hamming 15/11) | elegida con entrenamiento | 0.999 | 0.005 | 0.01 | 21.52 | 22.36 | 2908320 | 1249 | 52 | 36.0 | 3.62 | 0.0 |
| 1,75 (Hamming 7/4) | sin compresión | — | — | — | 0.0 | 0.0 | 4720964 | 0 | 0 | 0.0 | 0.0 | 0.0 |
| 1,75 (Hamming 7/4) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 21.67 | 22.36 | 3697730 | 1471 | 274 | 63.5 | 3.62 | 0.0 |
| 1,75 (Hamming 7/4) | elegida con entrenamiento | 0.995 | 0.005 | 0.01 | 21.67 | 22.36 | 3697730 | 1352 | 155 | 43.5 | 3.62 | 0.0 |
| 2,0 (SECDED 8/4) | sin compresión | — | — | — | 0.0 | 0.0 | 5378416 | 0 | 0 | 0.0 | 0.0 | 0.0 |
| 2,0 (SECDED 8/4) | actual (recompensa vieja) | 0.999 | 0.02 | 0.01 | 21.74 | 22.36 | 4209006 | 1471 | 274 | 45.5 | 3.62 | 0.0 |
| 2,0 (SECDED 8/4) | elegida con entrenamiento | 0.999 | 0.005 | 0.01 | 21.74 | 22.36 | 4209006 | 1294 | 97 | 67.8 | 3.62 | 0.0 |

Elegido con entrenamiento por factor: 1,0 (sin FEC): γ = 0.999, c = 0.005, costo = 0.01; 1,364 (Hamming 15/11): γ = 0.999, c = 0.005, costo = 0.01; 1,75 (Hamming 7/4): γ = 0.995, c = 0.005, costo = 0.01; 2,0 (SECDED 8/4): γ = 0.999, c = 0.005, costo = 0.01.

## Nota de lectura

- En bytes del aire la recompensa nueva no mejora nada: la configuración elegida con entrenamiento da en prueba los mismos bytes que la línea base para los cuatro factores (Δ 0,00 puntos) y solo gasta menos CPU (con f = 1: 1234 intentos y 40 µs/paquete frente a 1471 y 63 µs).
- En prueba, la línea base da 21,27 % (f = 1), 21,52 % (f = 1,364), 21,67 % (f = 1,75) y 21,74 % (f = 2) de ahorro en el aire. Sube con f porque el overhead fijo de 33 B pesa menos en marcos más grandes, no porque el bandido elija mejor.
- En entrenamiento, el mejor de toda la malla suma como mucho +0,01 puntos sobre la línea base (21,93 contra 21,92 con f = 1; 22,34 contra 22,34 con f = 1,75): γ, c y el costo de CPU casi no cambian las decisiones.
- Ninguna configuración cumple el límite de 2 % de paquetes mal reconstruidos con 1 % de pérdida: todas dan 3,82 % en entrenamiento y 3,6 % en prueba. Con una prueba aparte, solo "ninguno" y predictivo_audio dan 0,25 %, y con deflate_flujo 3,3 a 3,8 %, así que el fallo viene de deflate_flujo y no de γ, c ni del costo. Por eso el criterio de ajustar no discrimina aquí y la elección cae en el empate por menos intentos.
- ajuste.md da 1,91 % para (ninguno, zstd_dict, deflate_flujo) con γ 0,999 y c 0,02, y hoy esa misma configuración da 3,76 %. Ese archivo probablemente es anterior a cambios en el código; no lo regeneré.
- Conclusión: según la sombra de bandido.md (oráculo 20,84 % frente a 20,58 % del bandido), el bandido ya está cerca de su techo en bytes del aire. Para ganar más ancho de banda hay que cambiar qué códecs se ofrecen o la paridad FEC, no la recompensa.
