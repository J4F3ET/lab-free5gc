# Ajuste del bandido (conjunto de ENTRENAMIENTO, semilla 1)

Criterio, en este orden:

1. ahorro a no más de 0,5 puntos del mejor;
2. con 1 % de pérdida, a lo sumo 2 % de los paquetes mal reconstruidos (el doble de lo que pierde el canal);
3. de las que cumplen, la que menos intenta comprimir (más veces eligió "ninguno"): es el gasto de CPU medido sin ruido. La CPU en µs se muestra, pero no decide: en esta máquina la misma configuración dio 59 µs en una corrida y 113 µs en la siguiente. Si empatan, la de menos brazos.

Antes de medir la robustez el criterio era solo "mayor ahorro y luego menos CPU": con él ganaba DEFLATE con diccionario, que ahorra ~0,4 puntos más pero reconstruye mal el doble de paquetes con pérdida y gasta más CPU. Solo "menos daño" tampoco sirve: elige los 7 brazos, que gastan 4 veces más CPU para dañar medio punto menos.

| brazos | γ | c | ahorro % | % mal con 1 % de pérdida | CPU µs/paq | veces que eligió ninguno (ideal 2400) |
|---|---|---|---|---|---|---|
| 7 (todos) | 0.99 | 0.02 | 22.43 | 1.38 | 266.5 | 1235 |
| 7 (todos) | 0.99 | 0.05 | 22.47 | 1.74 | 372.0 | 600 |
| 7 (todos) | 0.995 | 0.02 | 22.54 | 1.68 | 215.2 | 1445 |
| 7 (todos) | 0.995 | 0.05 | 22.74 | 2.41 | 355.0 | 704 |
| 7 (todos) | 0.999 | 0.02 | 22.53 | 1.66 | 183.1 | 1849 |
| 7 (todos) | 0.999 | 0.05 | 22.56 | 1.38 | 297.3 | 998 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.99 | 0.02 | 22.42 | 3.14 | 81.9 | 1947 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.99 | 0.05 | 22.99 | 3.76 | 101.4 | 1396 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.995 | 0.02 | 22.24 | 2.41 | 75.5 | 2068 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.995 | 0.05 | 23.01 | 3.73 | 99.5 | 1539 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.999 | 0.02 | 20.65 | 1.12 | 64.1 | 2213 |
| 3 (ninguno, deflate_dict, deflate_flujo) | 0.999 | 0.05 | 23.04 | 3.87 | 89.6 | 1794 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.99 | 0.02 | 22.6 | 1.91 | 73.1 | 1874 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.99 | 0.05 | 22.72 | 1.99 | 87.8 | 1358 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.995 | 0.02 | 22.63 | 1.91 | 62.8 | 2068 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.995 | 0.05 | 22.61 | 1.77 | 85.8 | 1502 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.999 | 0.02 | 22.63 | 1.91 | 64.7 | 2213 |
| 3 (ninguno, zstd_dict, deflate_flujo) | 0.999 | 0.05 | 22.63 | 1.91 | 79.5 | 1737 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.99 | 0.02 | 22.56 | 1.8 | 88.6 | 1740 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.99 | 0.05 | 22.75 | 2.7 | 101.5 | 1076 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.995 | 0.02 | 22.61 | 1.97 | 75.4 | 1846 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.995 | 0.05 | 22.58 | 1.71 | 86.6 | 1249 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.999 | 0.02 | 22.6 | 1.77 | 65.1 | 2074 |
| 4 (ninguno, zstd_dict, deflate_dict, deflate_flujo) | 0.999 | 0.05 | 22.6 | 1.66 | 82.1 | 1449 |

Elegido: brazos 3 (ninguno, zstd_dict, deflate_flujo), γ = 0.999, c = 0.02.
