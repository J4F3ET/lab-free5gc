# El bandido por dentro (generado por `evaluar.py bandido`)

3600 paquetes del flujo mezclado de PRUEBA. Brazos: ninguno, zstd_dict, deflate_flujo, predictivo_audio; γ = 0.999, c = 0.02. Los bytes son de CARGA (después de comprimir cabeceras). Son las mismas variables que `shim.py` publica para el tablero «bandido en vivo».

## 1. Por brazo

recompensa = ahorro − 0,01 si el brazo comprime (costo de CPU); «respaldo» = lo eligió y la salida no achicó, así que el paquete fue sin comprimir.

| brazo | veces elegido | % de los paquetes | % que cayó al respaldo | ahorro efectivo % | recompensa media | µs de CPU por paquete |
|---|---|---|---|---|---|---|
| ninguno | 2129 | 59.1 | 0.0 | 0.0 | 0.0 | 2.7 |
| zstd_dict | 97 | 2.7 | 93.8 | 5.28 | 0.0195 | 102.1 |
| deflate_flujo | 1281 | 35.6 | 7.1 | 53.56 | 0.6198 | 221.4 |
| predictivo_audio | 93 | 2.6 | 98.9 | 0.02 | -0.0099 | 514.4 |

## 2. Por contexto (estado interno al terminar)

Contexto = (tamaño de la carga: chico < 64 B, mediano 64-511 B, grande ≥ 512 B; entropía relativa de sus primeros 256 B: baja < 0,75, media 0,75-0,9, alta ≥ 0,9). N es con descuento (γ): cuenta sobre todo los últimos ~1/(1−γ) paquetes. El bandido manda el brazo de mayor índice = media + c·√(ln ΣN / N).

| contexto (tamaño / entropía) | paquetes | clases que caen ahí | ninguno: media / N / índice | zstd_dict: media / N / índice | deflate_flujo: media / N / índice | predictivo_audio: media / N / índice |
|---|---|---|---|---|---|---|
| chico / baja | 600 | urllc_ctrl 600 | 0.000 / 404 / 0.002 | -0.010 / 16 / 0.002 | -0.010 / 16 / 0.002 | -0.010 / 16 / 0.002 |
| chico / alta | 600 | voip_rtp 600 | 0.000 / 404 / 0.002 | -0.010 / 16 / 0.002 | -0.010 / 16 / 0.002 | -0.010 / 16 / 0.002 |
| mediano / baja | 600 | iot_json 600 | 0.000 / 1 / 0.067 | 0.447 / 1 / 0.513 | 0.789 / 450 / 0.791 | -0.010 / 1 / 0.057 |
| grande / baja | 600 | http_texto 600 | 0.000 / 1 / 0.067 | 0.471 / 3 / 0.501 | 0.547 / 447 / 0.549 | -0.001 / 1 / 0.066 |
| grande / media | 1008 | web_tls 600, video_rtp 408 | 0.000 / 582 / 0.002 | -0.010 / 18 / 0.002 | -0.010 / 18 / 0.002 | -0.010 / 18 / 0.002 |
| grande / alta | 192 | video_rtp 192 | 0.000 / 140 / 0.004 | -0.010 / 12 / 0.003 | -0.010 / 12 / 0.003 | -0.010 / 12 / 0.003 |

## 3. ¿Podía hacerlo mejor? (sombra)

Cada paquete se comprimió además con todos los códecs rápidos, sin mandarlo. «mejor por paquete» = el códec que menos bytes daba en cada paquete (un oráculo: nadie puede saberlo antes de comprimir). arrepentimiento = (bytes del bandido − bytes del mejor) / bytes originales.

| algoritmo | bytes de carga | ahorro % | µs por paquete |
|---|---|---|---|
| mejor por paquete (oráculo) | 2005787 | 20.84 | — |
| deflate_flujo | 2011407 | 20.62 | 219.7 |
| **bandido_ucb** | 2012391 | 20.58 | 96.4 |
| zstd_dict | 2064401 | 18.53 | 137.1 |
| deflate_dict | 2124326 | 16.16 | 154.1 |
| deflate | 2179984 | 13.96 | 123.9 |
| predictivo_audio | 2529086 | 0.19 | 754.9 |
| ninguno | 2533808 | 0.0 | 3.4 |

Arrepentimiento del bandido: **0.26 puntos** (ahorra 20.58 % de un máximo de 20.84 %).

Por clase de tráfico:

| clase | ahorro del bandido % | mejor por paquete % | arrepentimiento (puntos) |
|---|---|---|---|
| http_texto | 55.39 | 56.14 | 0.75 |
| iot_json | 79.54 | 79.84 | 0.3 |
| urllc_ctrl | 0.0 | 0.07 | 0.07 |
| video_rtp | 0.0 | 0.0 | 0.0 |
| voip_rtp | 0.0 | 0.0 | 0.0 |
| web_tls | 0.0 | 0.0 | 0.0 |

## 4. Validación: ¿sirven estos brazos y este contexto?

La misma corrida cambiando una sola cosa. «intentos» = veces que eligió un brazo que comprime (gasta CPU); «respaldos» = intentos que no achicaron (CPU perdida).

| variante | ahorro % | mejor por paquete % | arrepentimiento (puntos) | intentos | respaldos | µs de CPU por paquete |
|---|---|---|---|---|---|---|
| red: 4 brazos + contexto 3×3 + ráfaga 8 | 20.58 | 20.84 | 0.26 | 1471 | 274 | 96.4 |
| sin ráfaga (decide en cada paquete) | 19.16 | 19.47 | 0.3 | 1471 | 274 | 92.4 |
| ráfaga 4 | 20.57 | 20.85 | 0.29 | 1471 | 274 | 101.4 |
| ráfaga 16 | 20.58 | 20.84 | 0.26 | 1471 | 274 | 109.9 |
| sin predictivo_audio | 20.61 | 20.85 | 0.24 | 1386 | 188 | 98.1 |
| sin contexto (un solo estado) | 18.5 | 18.7 | 0.2 | 3598 | 2398 | 120.5 |
| solo tamaño (3 contextos) | 20.63 | 20.9 | 0.27 | 2506 | 1309 | 185.4 |
| solo entropía (3 contextos) | 20.67 | 20.9 | 0.23 | 1979 | 781 | 131.6 |
| 2 brazos: ninguno, deflate_flujo | 20.67 | 20.9 | 0.23 | 1292 | 94 | 83.7 |
| 2 brazos: ninguno, zstd_dict | 18.5 | 18.7 | 0.21 | 1292 | 94 | 98.8 |
| todos los rápidos (6 brazos) | 20.55 | 20.85 | 0.29 | 1648 | 451 | 128.1 |

El «mejor por paquete» se calcula con los mismos códecs de la sombra en todas las filas; cambia un poco entre filas porque deflate_flujo recuerda solo los paquetes que esa variante mandó por él.
