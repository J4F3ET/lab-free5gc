# Balance latencia ↔ banda por algoritmo (generado por `evaluar.py balance`)

3600 paquetes del flujo mezclado de PRUEBA, cada uno verificado byte a byte. Modelo: compresor → enlace → descompresor como colas M/G/1 en serie (Pollaczek–Khinchine), con los tamaños y tiempos medidos de cada paquete. Ver el encabezado de `balance.py`.

## 1. Lo que cuesta y lo que ahorra cada política

| política | ahorro de bytes % | comprimir µs/paq | descomprimir µs/paq | % mal con 1 % de pérdida |
|---|---|---|---|---|
| sin compresión | 0.0 | 0.0 | 0.0 | 0.0 |
| ROHC + ninguno | 2.42 | 9.9 | 9.9 | 0.14 |
| ROHC + huffman_estatico | 13.26 | 574.4 | 1029.9 | 0.14 |
| ROHC + deflate | 15.95 | 72.3 | 22.3 | 0.14 |
| ROHC + deflate_dict | 18.08 | 93.9 | 19.7 | 0.14 |
| ROHC + deflate_flujo | 22.47 | 132.4 | 20.5 | 3.06 |
| ROHC + tic_por_paquete | 9.18 | 2550.8 | 1035.7 | 0.14 |
| ROHC + zstd_dict | 20.37 | 43.8 | 13.9 | 0.14 |
| ROHC + bandido UCB | 21.01 | 64.9 | 17.2 | 1.8 |

## 2. Latencia media en ms por escenario — CPU medida (Python, esta máquina)

Carga = cuánto ocuparía el enlace el tráfico SIN comprimir. **Negrita** = menor latencia. "Robusto" = el mejor entre los que reconstruyen mal ≤ 1 % con 1 % de pérdida. ∞ = alguna etapa no da abasto. La última columna es el bandido premiado por latencia, que sabe la velocidad del enlace.

| enlace | carga | sin compresión | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | zstd_dict | bandido UCB | bandido UCB (latencia) | gana | gana (robusto) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 kbps | 50% | 110 | 107 | 88.5 | 82.8 | 80.3 | **74.8** | 97.2 | 77.1 | 76.6 | 76.6 | deflate_flujo | zstd_dict |
| 100 kbps | 80% | 267 | 244 | 160 | 145 | 137 | **122** | 187 | 128 | 126 | 126 | deflate_flujo | zstd_dict |
| 100 kbps | 95% | 1050 | 727 | 275 | 234 | 213 | **176** | 365 | 191 | 187 | 187 | deflate_flujo | zstd_dict |
| 1 Mbps | 50% | 11 | 10.7 | 10.6 | 8.37 | 8.13 | **7.62** | 13.9 | 7.76 | 7.73 | 7.73 | deflate_flujo | bandido UCB (latencia) |
| 1 Mbps | 80% | 26.7 | 24.5 | 17.9 | 14.6 | 13.8 | **12.3** | 23.7 | 12.8 | 12.7 | 12.7 | deflate_flujo | bandido UCB (latencia) |
| 1 Mbps | 95% | 105 | 72.7 | 29.6 | 23.5 | 21.4 | **17.7** | 42.1 | 19.1 | 18.8 | 18.8 | deflate_flujo | bandido UCB (latencia) |
| 10 Mbps | 50% | 1.1 | 1.09 | 24.4 | 0.926 | 0.921 | 0.91 | ∞ | **0.829** | 0.851 | 0.84 | zstd_dict | zstd_dict |
| 10 Mbps | 80% | 2.67 | 2.46 | ∞ | 1.55 | 1.49 | 1.39 | ∞ | 1.34 | 1.35 | **1.33** | bandido UCB (latencia) | bandido UCB (latencia) |
| 10 Mbps | 95% | 10.5 | 7.29 | ∞ | 2.44 | 2.25 | 1.93 | ∞ | 1.97 | 1.96 | **1.92** | bandido UCB (latencia) | bandido UCB (latencia) |
| 100 Mbps | 50% | **0.11** | 0.128 | ∞ | 0.259 | 0.433 | ∞ | ∞ | 0.156 | 0.248 | 0.142 | sin compresión | sin compresión |
| 100 Mbps | 80% | 0.267 | 0.267 | ∞ | 9.69 | ∞ | ∞ | ∞ | **0.237** | 0.791 | 0.282 | zstd_dict | zstd_dict |
| 100 Mbps | 95% | 1.05 | 0.75 | ∞ | ∞ | ∞ | ∞ | ∞ | **0.333** | ∞ | 0.762 | zstd_dict | zstd_dict |
| 1 Gbps | 50% | **0.011** | 0.132 | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |
| 1 Gbps | 80% | **0.0267** | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |
| 1 Gbps | 95% | **0.105** | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |

## 3. Qué aprende el bandido por latencia a cada velocidad — CPU medida (Python, esta máquina)

Mismo bandido, mismos brazos; solo cambia la velocidad del enlace que conoce. % de paquetes en que eligió cada códec (antes del respaldo a "ninguno").

| enlace | ahorro % | comprimir µs/paq | % mal con 1 % de pérdida | elige ninguno % | elige deflate_flujo % | elige zstd_dict % |
|---|---|---|---|---|---|---|
| 100 kbps | 21.01 | 85.7 | 1.8 | 46.6 | 23.4 | 30.0 |
| 1 Mbps | 21.0 | 59.4 | 0.14 | 63.1 | 17.0 | 19.9 |
| 10 Mbps | 21.2 | 60.1 | 0.14 | 75.6 | 16.8 | 7.6 |
| 100 Mbps | 2.47 | 20.5 | 0.14 | 99.7 | 0.2 | 0.2 |
| 1 Gbps | 2.47 | 22.5 | 0.14 | 99.7 | 0.2 | 0.2 |

## 4. Velocidad de equilibrio por clase — CPU medida (Python, esta máquina)

Con carga 50%: por debajo de esta velocidad de enlace la política tiene MENOS latencia que no comprimir. "nunca" = no ahorra lo suficiente para pagar su CPU a ninguna velocidad; "siempre" = gana en todo el rango probado (1 kbps a 100 Gbps).

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | zstd_dict | bandido UCB |
|---|---|---|---|---|---|---|---|---|
| voip_rtp | 14 Mbps | 5.99 Mbps | 6.25 Mbps | 4.8 Mbps | 3.04 Mbps | 1.46 Mbps | 11.2 Mbps | 6.85 Mbps |
| http_texto | nunca | 970 kbps | 57.7 Mbps | 48.9 Mbps | 43.4 Mbps | 485 kbps | 77.5 Mbps | 48 Mbps |
| urllc_ctrl | 11.8 Mbps | 5.34 Mbps | 5.53 Mbps | 3.84 Mbps | 2.44 Mbps | 1.61 Mbps | 8.13 Mbps | 6.22 Mbps |
| video_rtp | 18.9 Mbps | 546 kbps | 4.51 Mbps | 4.13 Mbps | 3.12 Mbps | 121 kbps | 8.45 Mbps | 8.74 Mbps |
| iot_json | 15.6 Mbps | 1.25 Mbps | 5.95 Mbps | 9.08 Mbps | 9.13 Mbps | 890 kbps | 20.7 Mbps | 7.96 Mbps |
| web_tls | nunca | nunca | nunca | nunca | nunca | nunca | nunca | nunca |

## 5. Latencia media en ms por escenario — CPU 10× más rápida (supuesto: implementación en C o en la estación base)

Carga = cuánto ocuparía el enlace el tráfico SIN comprimir. **Negrita** = menor latencia. "Robusto" = el mejor entre los que reconstruyen mal ≤ 1 % con 1 % de pérdida. ∞ = alguna etapa no da abasto. La última columna es el bandido premiado por latencia, que sabe la velocidad del enlace.

| enlace | carga | sin compresión | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | zstd_dict | bandido UCB | bandido UCB (latencia) | gana | gana (robusto) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 kbps | 50% | 110 | 107 | 87.1 | 82.8 | 80.2 | **74.7** | 93.9 | 77 | 76.5 | 76.5 | deflate_flujo | zstd_dict |
| 100 kbps | 80% | 267 | 244 | 159 | 145 | 137 | **121** | 183 | 128 | 126 | 126 | deflate_flujo | zstd_dict |
| 100 kbps | 95% | 1050 | 727 | 274 | 234 | 213 | **176** | 361 | 191 | 187 | 187 | deflate_flujo | zstd_dict |
| 1 Mbps | 50% | 11 | 10.7 | 8.85 | 8.28 | 8.03 | **7.48** | 9.72 | 7.71 | 7.66 | 7.66 | deflate_flujo | zstd_dict |
| 1 Mbps | 80% | 26.7 | 24.4 | 16 | 14.5 | 13.7 | **12.2** | 18.7 | 12.8 | 12.6 | 12.6 | deflate_flujo | zstd_dict |
| 1 Mbps | 95% | 105 | 72.7 | 27.5 | 23.4 | 21.3 | **17.6** | 36.5 | 19.1 | 18.7 | 18.7 | deflate_flujo | zstd_dict |
| 10 Mbps | 50% | 1.1 | 1.07 | 1.06 | 0.837 | 0.814 | **0.762** | 1.39 | 0.776 | 0.773 | 0.777 | deflate_flujo | zstd_dict |
| 10 Mbps | 80% | 2.67 | 2.45 | 1.79 | 1.46 | 1.38 | **1.23** | 2.37 | 1.28 | 1.27 | 1.29 | deflate_flujo | zstd_dict |
| 10 Mbps | 95% | 10.5 | 7.27 | 2.96 | 2.35 | 2.14 | **1.77** | 4.21 | 1.91 | 1.88 | 1.92 | deflate_flujo | zstd_dict |
| 100 Mbps | 50% | 0.11 | 0.109 | 2.44 | 0.0926 | 0.0921 | 0.091 | ∞ | **0.0829** | 0.0851 | 0.0836 | zstd_dict | zstd_dict |
| 100 Mbps | 80% | 0.267 | 0.246 | ∞ | 0.155 | 0.149 | 0.139 | ∞ | **0.134** | 0.135 | 0.135 | zstd_dict | zstd_dict |
| 100 Mbps | 95% | 1.05 | 0.729 | ∞ | 0.244 | 0.225 | **0.193** | ∞ | 0.197 | 0.196 | 0.198 | deflate_flujo | zstd_dict |
| 1 Gbps | 50% | **0.011** | 0.0128 | ∞ | 0.0259 | 0.0433 | ∞ | ∞ | 0.0156 | 0.0248 | 0.0147 | sin compresión | sin compresión |
| 1 Gbps | 80% | 0.0267 | 0.0267 | ∞ | 0.969 | ∞ | ∞ | ∞ | **0.0237** | 0.0791 | 0.029 | zstd_dict | zstd_dict |
| 1 Gbps | 95% | 0.105 | 0.075 | ∞ | ∞ | ∞ | ∞ | ∞ | **0.0333** | ∞ | 0.077 | zstd_dict | zstd_dict |

## 6. Qué aprende el bandido por latencia a cada velocidad — CPU 10× más rápida (supuesto: implementación en C o en la estación base)

Mismo bandido, mismos brazos; solo cambia la velocidad del enlace que conoce. % de paquetes en que eligió cada códec (antes del respaldo a "ninguno").

| enlace | ahorro % | comprimir µs/paq | % mal con 1 % de pérdida | elige ninguno % | elige deflate_flujo % | elige zstd_dict % |
|---|---|---|---|---|---|---|
| 100 kbps | 21.01 | 95.3 | 1.8 | 29.6 | 30.4 | 40.0 |
| 1 Mbps | 21.01 | 83.8 | 1.8 | 46.1 | 23.3 | 30.6 |
| 10 Mbps | 20.34 | 48.1 | 0.14 | 62.6 | 0.5 | 36.9 |
| 100 Mbps | 20.34 | 47.3 | 0.14 | 66.4 | 0.2 | 33.4 |
| 1 Gbps | 2.47 | 22.5 | 0.14 | 99.7 | 0.2 | 0.2 |

## 7. Velocidad de equilibrio por clase — CPU 10× más rápida (supuesto: implementación en C o en la estación base)

Con carga 50%: por debajo de esta velocidad de enlace la política tiene MENOS latencia que no comprimir. "nunca" = no ahorra lo suficiente para pagar su CPU a ninguna velocidad; "siempre" = gana en todo el rango probado (1 kbps a 100 Gbps).

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | zstd_dict | bandido UCB |
|---|---|---|---|---|---|---|---|---|
| voip_rtp | 140 Mbps | 59.9 Mbps | 62.5 Mbps | 48 Mbps | 30.4 Mbps | 14.6 Mbps | 112 Mbps | 68.5 Mbps |
| http_texto | nunca | 9.7 Mbps | 577 Mbps | 489 Mbps | 434 Mbps | 4.85 Mbps | 775 Mbps | 480 Mbps |
| urllc_ctrl | 118 Mbps | 53.4 Mbps | 55.3 Mbps | 38.4 Mbps | 24.4 Mbps | 16.1 Mbps | 81.3 Mbps | 62.2 Mbps |
| video_rtp | 189 Mbps | 5.46 Mbps | 45.1 Mbps | 41.3 Mbps | 31.2 Mbps | 1.21 Mbps | 84.5 Mbps | 87.4 Mbps |
| iot_json | 156 Mbps | 12.5 Mbps | 59.5 Mbps | 90.8 Mbps | 91.3 Mbps | 8.9 Mbps | 207 Mbps | 79.6 Mbps |
| web_tls | nunca | nunca | nunca | nunca | nunca | nunca | nunca | nunca |
