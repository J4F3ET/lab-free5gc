# 11 — Informe: detección y corrección de errores por síndrome (app y Grafana)

Resumen para el informe final. Detalle técnico y teoría completa en
[docs/10](10-codificacion-canal.md). Verificaciones en el lab: 22, 23 y 24-11-2026 (la ampliación final está en el §8).

## 1. Qué se hizo

Se tomó lo visto en clase (códigos de bloque lineales, apunte del 9-11) y se
implementó dentro del proyecto de compresión de paquetes sobre free5GC:

- Un **código lineal genérico** definido solo por la matriz de paridad $P$:
  $\mathbf b=\mathbf m P$, $G=[P:I_k]$, $\mathbf c=\mathbf m G$, $H=[I_{n-k}:P^T]$,
  con la comprobación $HG^T=0$ al construirlo.
- Dos instancias vistas en clase: **repetición (5,1)** y **Hamming (7,4)**
  (distancia mínima 3: detecta 2 errores y corrige 1), y una tercera añadida después:
  **SECDED (8,4)** (distancia mínima 4: corrige 1 y detecta 2 sin corregir mal; ver §6).
- Una **capa FEC** entre el marco comprimido y el socket UDP del túnel
  (`compresion/shim.py`). Está apagada por defecto (`FEC` vacío = bytes idénticos
  a antes).
- **Inyección manual** de errores desde la página de envío, y un receptor que
  calcula el síndrome $\mathbf s=\mathbf r H^T=\mathbf e H^T$, lo compara con las
  columnas de $H$ y corrige el bit señalado.
- **Visualización** en la app (pestaña «Laboratorio de síndrome») y en Grafana
  (tablero «Codificación de canal (síndrome)»).

Quedan solo documentados como siguiente paso: CRC, entrelazado, Reed-Solomon,
XOR entre paquetes, RaptorQ y LDPC/Polar (ver §6 para lo que sí entró después).

## 2. En la app: pestaña «Laboratorio de síndrome»

Página de envío de archivos (`compresion/envio.py`), segunda pestaña.

| Elemento | Qué muestra | Para qué sirve en el informe |
|---|---|---|
| Código activo | Matrices $G=[P:I_k]$ y $H=[I:P^T]$ dibujadas, con la parte de paridad sombreada | Conecta el código con el apunte |
| Botones de inyección | Voltear 1 bit, 2 bits o un bit exacto en el siguiente paquete | Provocar el error a voluntad |
| Línea de paquetes | Una casilla por paquete, coloreada por estado | Ver de un vistazo qué pasó |
| Tabla de paquetes | Últimos 100, con filtro por estado: id, lado, secuencia, códec del bandido, estado, síndromes, palabras corregidas, bits volteados | Identificar cada paquete |
| Detalle de un paquete | Códec elegido, bytes comprimidos y con paridad, bits volteados, cada síndrome y la columna de $H$ que coincide, posición corregida (numerada desde 1, como en clase), resultado | Seguir un paquete paso a paso |
| Resumen del envío | Paquetes observados y su estado al mandar un archivo | Relacionar un archivo con los errores |

**Estados de un paquete**

| Estado | Significado |
|---|---|
| `limpio` | $\mathbf s=0$: no se detecta error |
| `corregido` | $\mathbf s\neq 0$ coincide con una columna de $H$; se corrigió ese bit y el paquete quedó íntegro |
| `corregido_mal` | El síndrome corrigió un bit pero el paquete no se pudo reconstruir (típico con 2 errores en una palabra Hamming) |
| `no_corregible` | $\mathbf s$ no coincide con ninguna columna de $H$ |
| `invalido` | El marco no se pudo leer (por ejemplo, id de código alterado) |

**Cómo se ve el síndrome en la práctica**

- 1 bit volteado → $\mathbf s$ = columna $i$ de $H$ → se corrige el bit $i$ → `corregido`.
- 2 bits en la misma palabra Hamming (7,4) → el síndrome apunta a un tercer bit y
  «corrige» el equivocado → normalmente `corregido_mal`. Es la consecuencia de
  $d_{min}=3$: detecta 2 errores, pero con 2 no sabe cuál corregir.

**Dónde se inyecta.** En la recepción de `cmp-dn`, antes de decodificar. Solo se
ve el tráfico que recibe `cmp-dn`. La orden la gasta el siguiente marco que llegue
(normalmente del generador `cmp-gen`), así que para llevar el error al archivo
hay que parar `cmp-gen` antes.

**Efecto sobre el túnel.** Con FEC el marco crece $n/k$: el MTU de `cmp0` baja a
780 con Hamming (7,4) y a 247 con repetición (5,1). El sobrecoste de Hamming es
×1,75 en bytes.

## 3. En Grafana: tablero «Codificación de canal (síndrome)»

Archivo: `observability/grafana/provisioning/dashboards/json/lab5g-cmp-fec.json`
(generado por `dashboards_compresion.py`). Variables `lado` y `codigo`. Cada panel
lleva su ficha Significado / Cómo funciona / Eje X / Eje Y / A qué responde.

| Panel | Tipo | Qué muestra | Qué responde |
|---|---|---|---|
| Cómo leer este tablero | Texto | La idea de clase en pocas líneas: $\mathbf r=\mathbf c+\mathbf e$, síndrome y estados | Orientar a quien lo abre |
| Qué pasó con los paquetes | Barras | Paquetes por estado en el rango elegido | ¿Qué fracción fue limpia, corregida o perdida? |
| Estados en el tiempo | Serie apilada | Paquetes por minuto por estado | ¿Cuándo se inyectaron errores y cómo respondió el código? |
| Síndromes observados | Barras | Palabras por valor de síndrome distinto de cero | ¿El síndrome señala las columnas esperadas de $H$? |
| Códec del bandido por estado | Tabla (mapa de calor) | Paquetes por códec (fila) y estado (columna) | ¿Los códecs con estado, como `deflate_flujo`, sufren más con errores? |

**Métricas nuevas** (publicadas por el shim al pushgateway cada 10 s; todas con
etiquetas `lado` y `codigo`):

| Métrica | Etiquetas extra |
|---|---|
| `lab5g_cmp_fec_paquetes_total` | `estado`, `codec` |
| `lab5g_cmp_fec_sindrome_total` | `sindrome` |
| `lab5g_cmp_fec_bits_inyectados_total` | — |
| `lab5g_cmp_fec_palabras_total` | — |
| `lab5g_cmp_fec_bytes_total` | `tipo` (`comprimido`, `fec`) |

Las series se precargan en 0, para que `increase()` y `rate()` cuenten también el
primer evento.

**Otros cambios en Grafana**

- Se quitó el panel «CPU por contenedor del lab» del tablero overview, porque
  cAdvisor ya no existe y no tenía fuente de datos.
- Grafana arranca bajo demanda (`lab5g.sh grafana up` / `down`) y se desactivaron
  17 plugins que el lab no usa (PR #14), para que arranque más ligero en un host
  de 3.8 GB.

## 4. Verificación

- **Pruebas automáticas:** 108 a 109 pruebas unitarias (`test_fec.py`,
  `test_registro_fec.py`, `test_envio_fec.py`, `test_tablero_fec.py`, más las de
  compresores y reproductores). Incluyen los ejemplos exactos de clase
  (Hamming (7,4), $\mathbf m=1011\to\mathbf c=1001011$, error en el bit 5 →
  $\mathbf s=011$).
- **Lab real (22-11):** un archivo enviado con FEC activa llegó idéntico; las
  métricas `lab5g_cmp_fec_*` llegaron al pushgateway; las 94 consultas de los
  tableros devuelven datos en Prometheus. Un agente validador (Opus) revisó el
  trabajo y lo dio por «funciona con reservas».

## 5. Límites que conviene declarar

- (Resuelto el 24-11) El tablero de FEC se vio dibujado en Grafana y concuerda con el tráfico (§6).
- La pestaña **no dibuja las palabras una por una** con los bits volteados en
  rojo; muestra las matrices, y los síndromes y las posiciones como texto.
- «Íntegro» solo significa que el paquete descomprimió sin excepción. Una
  corrección equivocada puede pasar por íntegra; `sha_identico` en `/enviar`
  compara el archivo de ida con el de vuelta.
- Un paquete `no_corregible` se entrega igual a la TUN, salvo con `REENVIO=1` (§8), que lo descarta y pide el reenvío.
- El costo de CPU de la FEC está medido en un PC, no en el LXC 110 (más lento).
- La página de envío no tiene login: no debe exponerse por Funnel.
- Con SECDED, tres o más errores en una palabra todavía pueden corregirse mal; el
  canal de ráfagas solo se probó con pruebas unitarias y en el banco offline, no en el lab.

## 6. Ampliación del 23-11: SECDED, canales aleatorios y curva BER

**SECDED (8,4).** Es un Hamming con un bit de paridad extra (código de Hsiao): las 8
columnas de $H$ son distintas y de peso impar, así que $d_{min}=4$. Regla: $\mathbf s=0$,
sin error; $\mathbf s$ de peso impar, un error y se corrige; $\mathbf s$ de peso par, **dos
errores y no se corrige** («doble detectado»). Cuesta el doble de bytes (MTU de `cmp0` 677).
Entró en el motor genérico solo con una matriz $P$ nueva.

**Canales aleatorios.** Variable `CANAL`: `bsc:P` (cada bit se voltea con probabilidad
$P$) o `rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO` (Gilbert-Elliott, errores agrupados),
con semilla `CANAL_SEMILLA`. Se aplican al recibir, antes de decodificar, y se suman a la
inyección manual.

**Curva BER (banco offline, `compresion/resultados/fec.md`).** BER residual contra BER de
entrada, 300 paquetes de 200 B por punto:

| BER de entrada | Sin código | Repetición (5,1) | Hamming (7,4) | SECDED (8,4) |
|---|---|---|---|---|
| 9,6e-04 | 9,6e-04 | 2,1e-06 | 0 | 4,2e-06 |
| 3,1e-03 | 3,1e-03 | 3,4e-03 | 3,4e-03 | 6,9e-05 |
| 1,0e-02 | 1,0e-02 | 1,0e-02 | 1,1e-02 | 1,1e-02 |
| 3,0e-02 | 3,0e-02 | 6,7e-02 | 8,4e-02 | 8,9e-02 |

Lectura: con ruido bajo ($\le 10^{-3}$) los códigos casi eliminan los errores. Por encima
de unos $3\cdot10^{-3}$ los códigos cortos dejan de ayudar, porque aparecen palabras con
dos o más errores. Hamming las corrige mal sin avisar (a $p=3\cdot10^{-2}$, 277 de 300
paquetes); SECDED las detecta. La probabilidad teórica de palabra fallida coincide con la
observada. Con errores en ráfaga todos los códigos pierden eficacia. Advertencia de método:
un paquete inválido cuenta todos sus bits de datos como erróneos y uno no corregible se
entrega con errores, por eso a $p$ alto el BER residual supera al de entrada.

**Verificado en el lab real (23-11), `FEC=secded_8_4`:**

| Caso | Resultado |
|---|---|
| 1 bit | `corregido`, síndrome `0001` |
| 2 bits en la misma palabra | `no_corregible` («doble detectado»), síndrome `0110` (peso par); no corrige ningún bit |
| 2 bits en palabras distintas | 2 palabras `corregido` |
| Canal `bsc:1e-3`, 3 archivos de 20 KB | los 3 con SHA idéntico; 234 a 1396 síndromes por envío, casi todos corregidos |

**En la app y en Grafana:** la pestaña muestra el canal activo y «doble detectado» con
SECDED. El tablero suma dos paneles: «BER de entrada del canal» y «Sobrecoste de la
protección» (7 elementos en total). Las 96 consultas de los cuatro tableros devuelven
datos en Prometheus; el tablero de síndrome se vio renderizado en Grafana el 24-11 y concuerda con el tráfico: BER de entrada ≈ 0,1 % (el `bsc:1e-3` activo), sobrecoste ≈ 2,03 (SECDED) y 8 síndromes de peso impar casi iguales (~194 000) más los de peso par (~800, dobles detectados).

**Por qué no se hizo XOR, Reed-Solomon ni RaptorQ entre paquetes:** reconstruyen un
paquete perdido a partir de otros, y ese paquete llega tarde; los códecs con estado (como
`deflate_flujo`) necesitan los paquetes en orden y se desincronizarían.

## 7. Pendiente (no hecho)

CRC-32 del marco (saber con certeza si el paquete llegó íntegro), entrelazado
(ráfagas), borrado de paquetes, Reed-Solomon y XOR entre paquetes (pérdida de paquetes
enteros), RaptorQ, y estado del arte de 5G NR (LDPC para datos, Polar para control). Detalle y fuentes en [docs/10 §8-9](10-codificacion-canal.md).

## 8. Ampliación del 24-11: más códigos, código adaptativo y reenvío

Objetivo del cambio: **ahorrar ancho de banda contando los bits de paridad**, no solo la
velocidad. Todo está apagado por defecto y se activa por variable de entorno.

### 8.1 Dos códigos más, mismo motor

Se añadieron con solo una matriz $P$ nueva: **Hamming (15,11)** y **SECDED (16,11)**
(Hsiao, $d_{min}=4$). Para el mismo ruido pagan mucho menos que SECDED (8,4):

| Código | $n/k$ | Sobrecoste | MTU de `cmp0` | Detecta 2 errores |
|---|---|---|---|---|
| Repetición (5,1) | 5,0 | ×5,00 | 247 | no |
| Hamming (7,4) | 1,75 | ×1,75 | 780 | no |
| SECDED (8,4) | 2,00 | ×2,00 | 677 | sí |
| Hamming (15,11) | 1,36 | ×1,36 | 1011 | no |
| SECDED (16,11) | 1,45 | ×1,45 | 945 | sí |

Curva BER (banco offline, 300 paquetes de 200 B por punto, `compresion/resultados/fec.md`):

| BER de entrada | Sin código | Hamming (15,11) | SECDED (16,11) | SECDED (8,4) |
|---|---|---|---|---|
| 9,6e-04 | 9,6e-04 | 1,5e-05 | 6,3e-06 | 4,2e-06 |
| 3,1e-03 | 3,1e-03 | 3,6e-03 | 3,5e-03 | 6,9e-05 |
| 1,0e-02 | 1,0e-02 | 2,2e-02 | 1,8e-02 | 1,1e-02 |

Lectura: con $p\le10^{-3}$ los códigos largos bastan y son más baratos; hacia $3\cdot10^{-3}$
solo SECDED (8,4) sigue ayudando. **El mejor código depende del ruido.**

**Bandido en bytes del aire.** Se cambió su recompensa a bytes en el aire,
$aire(L)=33+f\,(4+L)$ con $f=n/k$. Medido offline, ese cambio da **0,00 puntos** de ahorro
adicional: lo que más pesa en el ancho de banda es la elección del código FEC, no la del
compresor.

### 8.2 Código adaptativo (`FEC=auto`)

El receptor **mide el ruido con los propios síndromes** y se lo dice al emisor:

1. Estima el BER: $\widehat{p}pprox(\#	ext{síndromes}
e0+\#	ext{no corregibles})/(	ext{palabras}\cdot n)$,
   como cociente de sumas con olvido exponencial (alfa 0,02).
2. Cada ~2 s manda un **informe** de 12 bytes (marca `0xFF`, BER×$10^9$, muestras, secuencia,
   CRC8). No va protegido por FEC.
3. El emisor elige con **histéresis** entre Hamming (15,11) ($\widehat p\le10^{-3}$), SECDED
   (16,11) ($\le2\cdot10^{-3}$) y SECDED (8,4) (resto). Sube protección al instante y la
   baja solo tras 3 informes con BER bajo la mitad del umbral.
4. Los marcos son autodescriptivos (el primer byte es el id del código), así que el cambio
   no necesita acuerdo previo. El MTU de `cmp0` es el mínimo de la escalera (677).

Verificado en el lab real (cada punto con `CANAL` fijo, reiniciando entre uno y otro):

| `CANAL` | BER estimado | Código elegido |
|---|---|---|
| `bsc:1e-4` | $pprox3\cdot10^{-5}$ (estimador viejo, subestimaba) | Hamming (15,11) |
| `bsc:3e-3` | $3{,}0\cdot10^{-3}$ | SECDED (8,4) |
| `bsc:1e-3` | $1{,}0\cdot10^{-3}$ | SECDED (16,11) |

**Error encontrado y corregido en el lab:** la primera versión promediaba cocientes por
paquete y estimaba 3 veces menos que el BER real (3e-5 contra 1e-4). Se cambió a cociente de
sumas con olvido y el estimado coincide con el canal (3,0e-3 contra 3e-3).

No se midió un cambio de ruido en caliente; la subida y bajada con histéresis solo están
cubiertas por pruebas unitarias.

### 8.3 Reenvío por NACK (`REENVIO=1`)

SECDED **detecta** los dobles errores pero no los corrige. Con `REENVIO=1` el receptor
descarta el paquete con una palabra no corregible y manda un **NACK** (marca `0xFE`, hasta 8
secuencias, CRC8). El emisor guarda los últimos 128 marcos limpios y los reenvía, con un
máximo de 2 reenvíos por secuencia y 8 por segundo.

| Prueba en el lab | Resultado |
|---|---|
| SECDED (8,4), BSC 3e-3, archivo | idéntico (SHA); 9 NACK enviados, 10 reenvíos |
| SECDED (8,4), BSC 1e-2 | la transferencia no termina: casi todos los paquetes (≈2800 palabras) traen algún doble error y el reenvío vuelve a caer en lo mismo |
| `auto`, BSC 1e-3, ~minutos | miles de NACK y reenvíos, contadores cuadrados entre ambos lados, sin NACK corruptos |

Hamming y repetición no detectan los dobles errores (los corrigen mal en silencio), así que
ahí no hay NACK y recupera TCP. La página de envío marca en **azul** los paquetes que
llegaron por reenvío, con filtro «reenviado» y contador en la tarjeta de FEC.

Se corrigió un error de diseño antes de desplegar: el emisor aplicaba ruido a cada reenvío y
el receptor lo aplicaba otra vez (doble ruido); ahora el emisor reenvía el marco limpio.

### 8.4 Métricas nuevas

`lab5g_cmp_fec_ber_estimado`, `lab5g_cmp_fec_codigo_activo{codigo}`,
`lab5g_cmp_fec_cambios_codigo_total`, `lab5g_cmp_fec_informes_total{tipo}` y
`lab5g_cmp_fec_reenvio_total{tipo}`. No se añadieron paneles de Grafana para ellas.

### 8.5 Verificación y límites

- **Pruebas automáticas:** 249 en 14 archivos, listados en `deploy.yml`.
- **Estado del lab al cerrar:** `FEC=auto`, `CANAL=bsc:1e-3`, `REENVIO=1`.
- Con tres o más errores en una palabra SECDED corrige mal y no hay NACK.
- Con compresores con estado (`deflate_flujo`) un reenvío llega fuera de orden y su
  descompresión puede fallar; no se midió por separado.
- El reenvío no sirve con ruido extremo (1e-2): descarta casi todo.
- El informe del receptor y el NACK no llevan FEC, solo CRC8.
- La confirmación visual del azul en la línea de tiempo depende de quien abra la página.
- Cada sentido decide su código por separado y el lazo de realimentación tiene un retardo de
  unos 2 a 6 s.
