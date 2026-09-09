# 01 — Conceptos 5G para quien no viene de redes

Este documento explica **qué es cada cosa y por qué existe**. No hay comandos:
para eso están los otros documentos. Léelo una vez de arriba abajo y el resto
del repositorio dejará de parecer una sopa de siglas.

---

## Índice

- [1. Por qué una red móvil es tan complicada](#1-por-qué-una-red-móvil-es-tan-complicada)
- [2. Los dos planos](#2-los-dos-planos)
- [3. Las ocho piezas del núcleo, una por una](#3-las-ocho-piezas-del-núcleo-una-por-una)
- [4. Las interfaces: N1, N2, N3, N4, N6](#4-las-interfaces-n1-n2-n3-n4-n6)
- [5. Los identificadores](#5-los-identificadores)
- [6. Los cuatro protocolos que verás](#6-los-cuatro-protocolos-que-verás)
- [7. El registro, paso a paso](#7-el-registro-paso-a-paso)
- [8. La sesión de datos, paso a paso](#8-la-sesión-de-datos-paso-a-paso)
- [9. Qué es exactamente un túnel GTP-U](#9-qué-es-exactamente-un-túnel-gtp-u)
- [10. Glosario](#10-glosario)

---

## 1. Por qué una red móvil es tan complicada

Una red doméstica tiene un problema fácil: los dispositivos están quietos, son
pocos, y el router los conoce a todos.

Una red móvil tiene tres problemas que no existen en casa:

1. **El dispositivo se mueve.** Puede cambiar de antena a mitad de una llamada,
   y su conexión no debe cortarse. Alguien tiene que ir recordando dónde está.
2. **El dispositivo es de un desconocido.** Cualquiera puede comprar un teléfono
   y meterle una SIM. La red tiene que demostrar criptográficamente que esa SIM
   es quien dice ser, **antes** de dejarla pasar.
3. **Son millones.** Y todos tienen contratos distintos: velocidad, prioridad,
   datos incluidos.

Cada una de esas tres exigencias se convirtió, en el diseño del 5G, en piezas
separadas de software. Por eso hay ocho y no una: no es burocracia, es que cada
una resuelve un problema distinto y puede escalarse por separado.

---

## 2. Los dos planos

La división más importante de todas.

### Plano de control — decidir

Todo lo que consiste en **preguntar, verificar y autorizar**. Mensajes pequeños,
poco frecuentes, pero con lógica complicada detrás.

- "¿Quién eres?"
- "¿Tienes contrato?"
- "¿Puedes usar este carril de red?"
- "Ábrele un tubo de datos con esta IP."

Aquí un mensaje puede tardar 50 ms sin que pase nada malo.

### Plano de usuario — transportar

Todo lo que consiste en **mover bytes**. Ninguna decisión: solo velocidad.

- Coger el paquete que manda el teléfono
- Quitarle el envoltorio
- Sacarlo hacia internet
- Y lo mismo al revés, millones de veces por segundo

Aquí 50 ms de retraso es un desastre.

### Por qué se separaron

Porque escalan distinto. Si se te llena la red de usuarios que **navegan mucho**,
necesitas más plano de usuario. Si se te llena de usuarios que **se conectan y
desconectan** todo el rato, necesitas más plano de control. Con las dos cosas
mezcladas en un solo programa tendrías que multiplicar todo a la vez.

En este laboratorio:

| Plano | Servicios | Cuántos |
|---|---|---|
| Control | `nrf` `amf` `smf` `ausf` `udm` `udr` `pcf` `nssf` | 8 |
| Usuario | `upf` | 1 |

Ese único servicio de plano de usuario es, curiosamente, **el que más recursos
consume y el más frágil**: es el que necesita el módulo de kernel y privilegios
especiales.

---

## 3. Las ocho piezas del núcleo, una por una

### NRF — el directorio

*Network Repository Function.*

Es una **agenda telefónica**. Cuando el AMF arranca, lo primero que hace es
llamar al NRF y decir "existo, soy de tipo AMF, estoy en `10.100.200.11`". Y
cuando el AMF necesita hablar con el AUSF, no tiene su dirección escrita en
ningún fichero: se la pregunta al NRF.

**Por qué existe:** en una red real hay decenas de instancias de cada pieza, que
se crean y destruyen constantemente. Escribir direcciones fijas en la
configuración de cada una sería inmantenible.

**Consecuencia práctica en este laboratorio:** si el NRF no arranca, **nada
funciona**, y los mensajes de error de las demás piezas no dicen "el NRF está
caído", dicen cosas como "no encuentro un AUSF". Siempre mira primero al NRF.

### AMF — el recepcionista

*Access and Mobility Management Function.*

Es **la única pieza del núcleo con la que el teléfono habla directamente**. Todo
lo demás llega a través de ella.

Se ocupa de:

- Recibir el "quiero conectarme" que llega desde la antena
- Coordinar la autenticación (pero no la ejecuta: eso es del AUSF)
- Saber en qué zona está el teléfono
- Reenviar al SMF las peticiones de sesión de datos

**En este laboratorio es la pieza que habla SCTP** con la antena, y ese detalle
es la causa del error `protocol not supported` cuando falta el módulo `sctp`.

### AUSF — el verificador

*Authentication Server Function.*

Ejecuta el reto criptográfico llamado **5G-AKA**. Funciona así:

```
Red      → teléfono:  "Cifra este número aleatorio con tu clave secreta"
teléfono → red:       "Aquí está el resultado"
Red:                  "Lo he calculado yo también. Coinciden. Eres quien dices."
```

Lo elegante del diseño es que **la clave secreta nunca viaja**. Está en la SIM y
está en la base de datos del operador, y nadie más la ve nunca. En este
repositorio esa clave es el campo `key` de
[`ueransim/ue.yaml`](../ueransim/ue.yaml), y la misma la inserta en MongoDB
[`ci/provision-subscriber.sh`](../ci/provision-subscriber.sh). **Si no
coinciden, el teléfono no entra**, y el error solo dice "authentication
failure".

### UDM — el intérprete

*Unified Data Management.*

Hace los cálculos criptográficos sobre los datos del abonado y, muy importante
aquí, **descifra el SUCI**.

Un teléfono no manda su IMSI por el aire — si lo hiciera, cualquiera con una
antena podría seguirte por la ciudad. Manda una versión cifrada llamada **SUCI**,
que solo el operador puede abrir. El UDM es quien la abre.

> En este laboratorio el cifrado está **desactivado**
> (`protectionScheme: 0` en `ue.yaml`). Es a propósito: así puedes leer el IMSI
> en las capturas de tráfico mientras depuras. En una red real sería inaceptable.

### UDR — el archivero

*Unified Data Repository.*

La **única** pieza que habla con MongoDB. Las demás le preguntan a él.

**Por qué existe:** para que cambiar de base de datos no obligue a tocar siete
programas. Es la misma razón por la que una aplicación bien hecha no reparte
consultas SQL por todo el código.

### PCF — las reglas

*Policy Control Function.*

Responde a preguntas del tipo "¿cuánta velocidad le corresponde a este usuario?",
"¿esta aplicación tiene prioridad?", "¿le quedan datos del plan?".

En este laboratorio es casi decorativo — hay un solo abonado con un plan
generoso — pero el SMF **exige** que exista y consultará con él antes de abrir
cada sesión de datos.

### NSSF — el asignador de carriles

*Network Slice Selection Function.*

El *network slicing* es una de las ideas centrales del 5G: sobre el mismo
hardware conviven varias **redes lógicas independientes**.

```
        ┌─ slice 1: móviles normales    (mucho ancho de banda)
hardware├─ slice 2: coches autónomos    (latencia mínima)
        └─ slice 3: sensores IoT        (millones de dispositivos, poco tráfico)
```

Cada carril se identifica con un **S-NSSAI**, dos números:

- **SST** (*Slice/Service Type*): el tipo. `1` = banda ancha normal.
- **SD** (*Slice Differentiator*): opcional, para distinguir varios del mismo
  tipo. Aquí `010203`.

Este laboratorio tiene un solo carril: `sst=1, sd=010203`. Pero ese par de
números **tiene que aparecer idéntico en cinco ficheros y en MongoDB**, y es una
de las causas más frecuentes de que el teléfono sea rechazado.

### SMF — el jefe del tubo de datos

*Session Management Function.*

Es la pieza que mejor ilustra la separación de planos: **decide todo sobre el
tráfico de datos, y no toca ni un byte de ese tráfico.**

Cuando el teléfono pide conexión a internet, el SMF:

1. Le asigna una IP del pool `10.60.0.0/16`
2. Consulta al PCF qué reglas aplican
3. **Le da instrucciones al UPF** por la interfaz N4: "cuando llegue tráfico del
   túnel número X, sácalo por aquí"
4. Le dice al teléfono, a través del AMF, qué IP le tocó

El paso 3 es el famoso **PFCP**, y su fallo es el más común de todo free5GC.
Requiere que el SMF y el UPF estén de acuerdo en un identificador llamado
`nodeID`. **Si no coincide, el SMF reintenta para siempre en silencio**: el
teléfono se registra bien, pero nunca consigue conexión a datos.

### UPF — la pista

*User Plane Function.*

Aquí pasa el tráfico real. Su trabajo, en un bucle infinito:

```
paquete del teléfono, envuelto en GTP-U
   → quitar el envoltorio
      → aplicar NAT
         → sacarlo hacia el destino
```

Y al revés para lo que vuelve.

Es el único servicio del laboratorio con `privileged: true`, y no es por pereza:
necesita crear una interfaz de red dentro del kernel (`upfgtp`) usando el módulo
`gtp5g`. Ningún otro servicio hace nada parecido.

---

## 4. Las interfaces: N1, N2, N3, N4, N6

En 5G, **cada "cable" entre dos piezas tiene un nombre estándar**. No son cables
físicos: son conversaciones con un protocolo definido.

```
        ┌──────────────┐  N1 (lógica, va dentro de N2)  ┌───────────┐
  UE ───┤              ├────────────────────────────────┤    AMF    │
        │     gNB      │  N2  NGAP sobre SCTP           └─────┬─────┘
        │   (antena)   ├──────────────────────────────────────┘
        │              │
        │              │  N3  GTP-U (los datos)         ┌───────────┐
        │              ├────────────────────────────────┤    UPF    │
        └──────────────┘                                └─────┬─────┘
                                              N4 PFCP         │  N6
                                        ┌───────────┐         │
                                        │    SMF    ├─────────┘  hacia
                                        └───────────┘            el destino
```

| Interfaz | Entre | Protocolo | Qué lleva | Puerto |
|---|---|---|---|---|
| **N1** | teléfono ↔ AMF | NAS | Mensajes de registro y sesión. Es **lógica**: viaja empaquetada dentro de N2 | — |
| **N2** | antena ↔ AMF | NGAP sobre **SCTP** | Señalización de la antena | 38412/sctp |
| **N3** | antena ↔ UPF | **GTP-U** | Los datos del usuario, envueltos | 2152/udp |
| **N4** | SMF ↔ UPF | **PFCP** | Instrucciones del cerebro a la pista | 8805/udp |
| **N6** | UPF ↔ destino | IP normal | Los datos ya desenvueltos | — |
| **SBI** | entre piezas del núcleo | HTTP/2 + JSON | Todo lo demás | 8000/tcp |

Dos observaciones que ahorran horas de depuración:

**N1 es lógica.** Cuando el teléfono se autentica, sus mensajes viajan *dentro*
de los mensajes de la antena. No verás una conexión separada entre teléfono y
AMF, porque no existe.

**El núcleo habla HTTP.** Las ocho piezas del plano de control se comunican con
peticiones HTTP/2 y JSON, igual que una API web normal. Eso es una novedad del
5G: en 4G cada interfaz tenía su propio protocolo binario. Es también por lo que
puedes consultar el estado del NRF con un simple `curl`.

---

## 5. Los identificadores

### PLMN — el operador

*Public Land Mobile Network.* Dos números pegados:

```
MCC  732   País        (Colombia)
MNC  101   Operador dentro de ese país
```

Aquí viene la primera trampa. **El MNC puede tener 2 o 3 dígitos según el
país**, y eso cambia la aritmética de todo lo demás.

### IMSI — la SIM

Son **siempre exactamente 15 dígitos**:

```
MCC (3) + MNC (2 ó 3) + MSIN (lo que falte hasta 15)

  001 01  0000000001   →  001010000000001    MNC de 2 → MSIN de 10
  732 101 000000001    →  732101000000001    MNC de 3 → MSIN de 9
```

**El error clásico:** copiar el MSIN de 10 dígitos de un ejemplo con MNC de 2, y
usarlo con un MNC de 3. Te quedan 16 dígitos. El teléfono manda su identidad, el
UDM no encuentra a nadie con ese número, y recibes un `Registration Reject` que
no menciona la longitud en ningún momento.

### El resto

| Nombre | Qué es |
|---|---|
| **SUPI** | El IMSI con un prefijo: `imsi-732101000000001`. Es el formato interno de free5GC. |
| **SUCI** | El SUPI cifrado, que es lo que viaja por el aire. Aquí va sin cifrar, a propósito. |
| **GUTI** | Un alias temporal que la red asigna tras el registro, para no repetir el identificador permanente. |
| **TAC** | *Tracking Area Code*: la zona geográfica. La red recuerda en qué zona está cada teléfono para poder localizarlo. |
| **DNN** | *Data Network Name*: el nombre del destino. Aquí `internet`. En 4G se llamaba **APN**, y es exactamente lo mismo. |
| **S-NSSAI** | El carril de red: `sst` + `sd`. |

### La trampa de formato que arruina la tarde

free5GC y UERANSIM **escriben los mismos valores de forma distinta**:

| Valor | En free5GC (`config/*.yaml`) | En UERANSIM (`ueransim/*.yaml`) |
|---|---|---|
| MCC / MNC | `'732'` (texto, entre comillas) | `'732'` (texto) |
| TAC | `'000001'` (texto hexadecimal) | `1` (número decimal) |
| SD | `'010203'` (texto hexadecimal) | `0x010203` (número hexadecimal) |

Son **el mismo valor escrito de dos formas**. No es un error del repositorio:
son convenciones distintas de dos proyectos distintos.

> ⚠️ **Y la trampa mayor:** si escribes `mcc: 001` sin comillas, YAML lo lee como
> el número `1` y **pierdes los ceros de delante**. Entrecomilla siempre `mcc`,
> `mnc`, `sd` y `tac` en los ficheros de free5GC. Este es probablemente el error
> más repetido de todo el ecosistema.

`python3 ci/check-plmn.py` existe precisamente para atrapar esto.

---

## 6. Los cuatro protocolos que verás

### HTTP/2 — entre las piezas del núcleo

Peticiones y respuestas JSON, como cualquier API web. Puerto `8000`. Se puede
inspeccionar con `curl`.

### SCTP — entre la antena y el AMF

*Stream Control Transmission Protocol.* Un primo de TCP, diseñado para
telefonía. Dos diferencias que importan:

- **Multi-stream:** varias conversaciones independientes en una sola conexión.
  Si un mensaje se pierde, no bloquea a los demás (TCP sí lo haría).
- **Multi-homing:** puede usar varias direcciones IP a la vez para resistir
  caídas.

Lo relevante aquí es que **es un protocolo distinto de TCP y necesita su propio
módulo de kernel**. Sin `modprobe sctp` en el host, el AMF no puede ni abrir su
puerto.

### PFCP — entre el SMF y el UPF

*Packet Forwarding Control Protocol.* UDP, puerto 8805. Es el idioma en que el
cerebro le da instrucciones a la pista: "instala esta regla", "cuando veas este
túnel, haz esto otro".

Lo primero que hacen al arrancar es una **asociación PFCP**: un saludo en el que
se identifican por su `nodeID`. Si los dos ficheros no declaran el mismo, el
saludo nunca se completa y el SMF reintenta indefinidamente sin dar un error
claro.

### GTP-U — el túnel de datos

*GPRS Tunnelling Protocol, User plane.* UDP, puerto 2152. Explicado en la
[sección 9](#9-qué-es-exactamente-un-túnel-gtp-u).

---

## 7. El registro, paso a paso

Lo que pasa entre encender el teléfono y estar conectado.

```
 1.  UE  → gNB    "busco red"                    (radio; aquí simulada por UDP)
 2.  gNB → AMF    Registration Request            [N2]
 3.  AMF → AUSF   "autentica a este"              [SBI]
 4.  AUSF→ UDM    "dame sus datos"                [SBI]
 5.  UDM → UDR    "búscalo en la base de datos"   [SBI]
 6.  UDR → mongo  consulta
 7.  AMF → UE     "cifra este número aleatorio"   [N1 dentro de N2]
 8.  UE  → AMF    resultado
 9.  AUSF         compara: coincide  ✓
10.  AMF → UE     Registration Accept
```

Diez pasos, ocho programas distintos, para conectar un solo teléfono. Y todavía
**no hay conexión a datos**: eso es la siguiente sección.

**Dónde falla en la práctica:**

| Paso | Si falla | Causa típica |
|---|---|---|
| 1 | El UE no encuentra la antena | `gnbSearchList` apunta a la IP equivocada |
| 2 | `NGSetupFailure` | El PLMN o el TAC no coinciden entre `gnb.yaml` y `amfcfg.yaml` |
| 5-6 | "subscriber not found" | Falta ejecutar `provision-subscriber.sh` |
| 9 | "authentication failure" | La clave de `ue.yaml` y la de MongoDB no son la misma |

---

## 8. La sesión de datos, paso a paso

Registrado ya, el teléfono pide conexión a internet.

```
 1.  UE  → AMF    PDU Session Establishment Request  (DNN=internet, slice)
 2.  AMF → SMF    reenvía
 3.  SMF → PCF    "¿qué reglas aplican?"
 4.  SMF          asigna IP: 10.60.0.1
 5.  SMF → UPF    PFCP: "instala reglas para el túnel 0x01"   [N4]  ← crítico
 6.  UPF          crea el túnel en el kernel via gtp5g
 7.  SMF → AMF → gNB   "el túnel va a esta IP N3, con este identificador"
 8.  gNB → UE     Session Establishment Accept
 9.  UE           crea uesimtun0 con IP 10.60.0.1
```

El paso 5 es **el que más falla en todo free5GC**, y el 7 es el segundo: si el
`gtpIp` de la antena no apunta a la IP correcta del UPF, el túnel se crea, la
interfaz aparece, todo parece bien… y no pasa ni un paquete. Es el síntoma más
desconcertante del laboratorio.

---

## 9. Qué es exactamente un túnel GTP-U

Un teléfono con IP `10.60.0.1` quiere hablar con un servidor. Pero ese teléfono
está detrás de una antena que puede cambiar en cualquier momento, y su IP debe
seguirle. La solución es **meter su paquete dentro de otro paquete**.

```
Lo que el teléfono cree que manda:
   ┌────────────────────────────────────┐
   │ IP 10.60.0.1 → 8.8.8.8 │  datos    │
   └────────────────────────────────────┘

Lo que viaja de verdad entre la antena y el UPF:
   ┌──────────────────┬───────┬────────────────────────────────────┐
   │ IP gNB → IP UPF  │ GTP-U │ IP 10.60.0.1 → 8.8.8.8 │  datos    │
   │  (UDP 2152)      │  TEID │                                    │
   └──────────────────┴───────┴────────────────────────────────────┘
    ← 36 bytes de más →
```

El **TEID** (*Tunnel Endpoint Identifier*) es el número que dice a qué teléfono
pertenece ese paquete. Cada sesión tiene el suyo.

Cuando el paquete llega al UPF, este le quita el envoltorio y lo saca a la red
como si nada hubiera pasado. Al volver, lo vuelve a envolver.

### Por qué esos 36 bytes causan tantos problemas

Una red Ethernet transporta paquetes de hasta **1500 bytes** (el *MTU*). Si el
teléfono manda uno de 1500 y le añadimos 36 de envoltorio, salen 1536: **no
cabe**.

El resultado es el fallo más traicionero del laboratorio:

- `ping` funciona (paquetes pequeños) ✅
- Abrir una web funciona a medias
- Una descarga grande **se queda colgada para siempre** ❌

Y no aparece ningún error en ningún log.

La solución es decirle al teléfono que use paquetes más pequeños:

```
UE_MTU = DOCKER_MTU - 36
1464   = 1500       - 36
```

Está en [`.env.example`](../.env.example) y lo aplica
[`ueransim/ue-entrypoint.sh`](../ueransim/ue-entrypoint.sh).

> Este laboratorio pasó por una fase con Tailscale, cuyo MTU es de 1280, y
> entonces los números eran otros. Ahora que el acceso es por LAN directa, el
> camino es de 1500 completos.

---

## 10. Glosario

| Sigla | Nombre completo | En una frase |
|---|---|---|
| **5G-AKA** | Authentication and Key Agreement | El reto criptográfico que autentica la SIM |
| **AMF** | Access and Mobility Management Function | El recepcionista |
| **APN** | Access Point Name | El nombre del destino, en 4G. Hoy DNN |
| **AUSF** | Authentication Server Function | El verificador de identidad |
| **DN** | Data Network | El destino final: internet, o aquí `dn-iperf` |
| **DNN** | Data Network Name | El nombre de ese destino: `internet` |
| **gNB** | next generation NodeB | La antena 5G |
| **GTP-U** | GPRS Tunnelling Protocol, User plane | El envoltorio de los datos |
| **GUTI** | Globally Unique Temporary Identity | Alias temporal del teléfono |
| **IMSI** | International Mobile Subscriber Identity | Los 15 dígitos de la SIM |
| **MCC** | Mobile Country Code | El código de país |
| **MNC** | Mobile Network Code | El código de operador |
| **MTU** | Maximum Transmission Unit | El paquete más grande que cabe |
| **NAS** | Non-Access Stratum | Los mensajes entre teléfono y núcleo |
| **NF** | Network Function | Una pieza del núcleo |
| **NGAP** | NG Application Protocol | El idioma entre antena y AMF |
| **NRF** | Network Repository Function | La agenda |
| **NSSF** | Network Slice Selection Function | El asignador de carriles |
| **PCF** | Policy Control Function | Las reglas del contrato |
| **PDU** | Protocol Data Unit | Aquí, "sesión de datos" |
| **PFCP** | Packet Forwarding Control Protocol | Las órdenes del SMF al UPF |
| **PLMN** | Public Land Mobile Network | El identificador del operador |
| **RAN** | Radio Access Network | Las antenas |
| **SBI** | Service Based Interface | El HTTP entre las piezas del núcleo |
| **SCTP** | Stream Control Transmission Protocol | El transporte de la señalización |
| **SD** | Slice Differentiator | Distingue carriles del mismo tipo |
| **SMF** | Session Management Function | El jefe del tubo de datos |
| **S-NSSAI** | Single-Network Slice Selection Assistance Information | El identificador de carril |
| **SST** | Slice/Service Type | El tipo de carril |
| **SUCI** | Subscription Concealed Identifier | El identificador cifrado |
| **SUPI** | Subscription Permanent Identifier | El identificador permanente |
| **TAC** | Tracking Area Code | La zona geográfica |
| **TEID** | Tunnel Endpoint Identifier | El número de túnel |
| **UDM** | Unified Data Management | El intérprete de datos del abonado |
| **UDR** | Unified Data Repository | El archivero |
| **UE** | User Equipment | El teléfono |
| **UPF** | User Plane Function | La pista |

---

**Siguiente:** [02 — Instalación detallada](02-instalacion.md)
