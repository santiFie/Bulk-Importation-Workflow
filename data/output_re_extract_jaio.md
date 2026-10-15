# Reporte de Extracción con `re_extract_with_ocr`

- **Bucket de origen:** `importacion-jaio`
- **Total de archivos procesados:** 3

## Resumen General

| Archivo PDF | Método Utilizado | ¿OCR Aplicado? | Caracteres Extraídos | Estado |
| :--- | :---: | :---: | :---: | :---: |
| `39-jaiio-ast-03.pdf-PDFA.pdf` | `pymupdf_blocks` | No | 3,000 | ✅ Exitoso |
| `39-jaiio-ast-06.pdf-PDFA.pdf` | `pymupdf_blocks` | No | 3,000 | ✅ Exitoso |
| `39-jaiio-ast-10.pdf-PDFA.pdf` | `pymupdf_blocks` | No | 3,000 | ✅ Exitoso |

---
## Detalle de Extracciones por Documento

### 📄 Documento: `39-jaiio-ast-03.pdf-PDFA.pdf`
- **Método:** `pymupdf_blocks`
- **OCR Aplicado:** No
- **Longitud del texto:** 3000 caracteres

#### Texto Extraído:
```text
Data Fusion BAUE Estimation of a deterministic
vector, applications to image noise and blur
reduction
Gustavo A. Roitman† and Bruno Cernuschi-Frias‡
†Facultad de Ingeniería, Universidad de Buenos Aires, Argentina
‡CONICET, Buenos Aires, Argentina
Abstract. In this work1 we conceive centralized data fusion as a de-
terministic parameter estimation problem. Two dierent criterions are
compared: best ane unbiased fusion rule (BAUE), and Maximum Like-
lihood for Gaussian measurement noise. Estimates are described in terms
of their covariance matrices, the Cramer-Rao lower bound and simula-
tions. The developed fusion rules are suited to two dierent image fusion
cases: noise reduction under dierently exposed images, and blur reduc-
tion based on lens response knowledge.
Keywords: multi-sensor fusion, image fusion, BAUE, maximum likelihood, MMSE
estimation.
1
Introduction
Data fusion techniques deal with the problem of combining information obtained
from several sensing devices, with the purpose of making an optimal estimation
of some process characteristic by using all available information. This estimation
should be more precise than the observation available in each individual device.
In the development of any data fusion scheme, the rst step is to dene how
the individual measurements are modelled. In the literature, the most adopted
observation model gives a linear relationship between the observations and the
estimated variables
y = Ax + η,
(1)
where x ∈ℜN is the process characteristic to be estimated, A ∈ℜM×N repre-
sents the observation system, and η ∈ℜM is an observation aditive noise. One
question that should arise is whether our model considers x a deterministic or a
random variable. The most common assumption is that x is a random variable,
and the problem of data fusion is to nd an optimal estimation, or fusion rule,
1 This work was partially supported by the University of Buenos Aires, CONICET,
Argentina, and the Agencia Nacional de Promocion Cientica y Tecnologica, PICT
No. 209-2006, Argentina
39JAIIO - AST 2010 - ISSN:1850-2806 - Página 1551

for x given the measurements y. However, this work deals with an alternate
approach, in which x is considered to be deterministic and only the noise vector
η is random. The fusion is thus stated in terms of a deterministic parameter
estimation problem.
In order to recover information of all the components of vector x, the observation
matrix A should represent a good observation of the characteristic to be esti-
mated. This means that among all M components of y there must be at least N
linearly independent equations involving the components of x, in other words,
Rg(A) = N. However, if M = N, there's no redundancy in the measurements,
no data fusion occurs and the solution is trivial. Consequently, our attention will
be put in the problem with M > N and Rg(A) = N.
The next step is to choose an optimization criterion. The most widely adopted
criterion is minimum mean square error (MMSE), and du
```

---

### 📄 Documento: `39-jaiio-ast-06.pdf-PDFA.pdf`
- **Método:** `pymupdf_blocks`
- **OCR Aplicado:** No
- **Longitud del texto:** 3000 caracteres

#### Texto Extraído:
```text
Estimación
de
la
Complejidad
Lo
cal
en
Imágenes
de
T
exturas
*
Agustin
Mailing†‡
,
T
omás
Criv
elli†
y
Bruno
Cern
usc
hi-F
rías†‡
†F
acultad
de
Ingeniería,
Univ
ersidad
de
Buenos
Aires,
Argen
tina
‡IAM-CONICET,
Buenos
Aires,
Argen
tina
Resumen
Cuando
se
utilizan
mo
delos
de
diferen
tes
órdenes
para
el
mo
delado
de
datos,
normalmen
te,
es
el
mo
delo
más
complejo
quien
a
jus-
tará
mejor
pues
presen
ta
ma
y
or
exibilidad.
No
obstan
te,
en
o
casiones
los
datos
pueden
ser
descriptos
de
igual
manera
p
or
mo
delos
de
distin
to
orden.
En
este
caso
decimos
que
la
complejidad
de
los
datos
(de
acuerdo
a
los
mo
delos
de
referencia)
está
dada
p
or
el
mo
delo
más
sencillo
que
lo
describ
e.
En
este
traba
jo
se
presen
ta
un
méto
do
para
determinar
esta
complejidad
lo
calmen
te
en
una
imagen
(de
texturas)
de
manera
sencil-
la
den
tro
de
un
esquema
de
mo
delos
enca
jados.
Si
bien
la
estructura
de
los
mo
delos
utilizados
es
simple,
p
ermitiendo
su
clara
y
fácil
imple-
men
tación,
el
méto
do
es
lo
sucien
temen
te
exible
y
general
para
ser
utilizado
con
estructuras
y
mo
delos
m
uy
complejos.
Key
w
ords:
Complejidad,
T
exturas,
Pro
cesos
Aleatorios,
Pro
cesamien
to
de
imágenes
1.
In
tro
ducción
En
la
gran
ma
y
oría,
p
or
no
decir
prácticamen
te
to
dos
los
algoritmos
y
méto-
dos
que
in
v
olucran
imágenes
está
presen
te
en
forma
explícita
o
no,
la
complejidad
de
los
datos
que
el
tip
o
de
imágenes
transp
orta.
Esto
es
la
Información.
Si
se
pretende
comprimir
una
imagen
su
complejidad
es
quien
determinará
cuál
es
la
tasa
de
compresión
máxima
que
se
obtendrá
sin
p
érdida
o
cuán
ta
información
se
p
erderá
(en
algunos
casos
para
siempre)
según
la
compresión
pretendida.
En
algunos
casos
la
información
que
transp
orta
una
imagen
es
determinísti-
ca:
p
or
ejemplo
en
una
típica
foto;
y
en
otros
la
información
esta
dada
p
or
la
estadística
de
los
datos:
una
imagen
del
mo
vimien
to
de
gen
te
en
una
calle
con-
currida
o
un
cine,
o
una
imagen
satelital
de
un
cielo
n
ublado.
En
este
traba
jo,
es
esta
información
estadística
la
que
p
ermite
determinar
qué
regiones
(de
aquí
el
término
lo
c
al
)
con
tienen
más
información
resp
ecto
del
mo
delo
seleccionado.
Esto
p
ermitiría
p
or
ejemplo
determinar
qué
región
es
la
que
con
tiene
ma
y
or
in-
formación
para
ser
pro
cesada,
en
viada
y/o
almacenada.
*
T
raba
jo
realizado
gracias
al
ap
o
y
o
de
la
Univ
ersidad
de
Buenos
Aires,
CONICET,
Argen
tina,
y
la
Agencia
Nacional
de
Promo
ción
Cien
tíca
y
T
ecnológica,
PICT
No.
209-2006,
Argen
tina
39JAIIO - AST 2010 - ISSN:1850-2806 - Página 1582

Uno
de
los
problemas
cen
trales
en
el
análisis
de
la
información
es
la
elección
del
orden
de
un
mo
delo
que
se
a
juste
a
los
datos
observ
ados.
En
el
con
texto
de
un
enfo
que
paramétrico,
esto
se
relaciona
al
n
úmero
de
parámetros
del
mo
delo.
Sup
ongamos
que
los
datos
corresp
onden
a
una
imagen,
la
cual
se
quiere
clasi-
car
en
tre
div
ersas
clases,
cada
una
de
las
cuales
esta
descripta
p
or
un
mo
delo
paramétrico
de
diferen
te
orden.
En
general,
el
mo
d
```

---

### 📄 Documento: `39-jaiio-ast-10.pdf-PDFA.pdf`
- **Método:** `pymupdf_blocks`
- **OCR Aplicado:** No
- **Longitud del texto:** 3000 caracteres

#### Texto Extraído:
```text
Métodos secuenciales de Monte Carlo aplicados a
modelos ocultos de Markov con proceso de estado
y de medición correlacionados.
* Pedro Gadze† y Bruno Cernuschi-Frías†‡
†Facultad de Ingeniería, Universidad de Buenos Aires, Argentina
‡CONICET, Buenos Aires, Argentina
Resumen En el marco del ﬁltrado Bayesiano, se presenta un modelo en
el cual el proceso de medición y el estado siguiente son condicionalmente
dependientes, dado el conjunto de observaciones pasadas y el estado ac-
tual. Además se busca la distribución de predicción para este modelo, y
la distribución de ﬁltrado se halla con una simple actualización a partir
de la de predicción. La distribución requerida se aproxima utilizando el
muestreo secuencial de importancia, y la distribución queda representa-
da por un conjunto de muestras aleatorias, que son obtenidas a partir
de una función de importancia, y se le asigna a las mismas un peso de
acuerdo a su relevancia.
Key words: Estimación Bayesiana, ﬁltrado óptimo, ﬁltrado estocástico, méto-
dos secuenciales de Monte Carlo
1.
Introducción
El ﬁltrado Bayesiano consiste en calcular la función de densidad de probabili-
dad (FDP) a posteriori del estado de un sistema a partir de toda la información
disponible, incluido el conjunto de mediciones. En varias aplicaciones en proce-
samiento de señal y teoría de control se desea estimar de manera recursiva esta
distribución. Salvo en casos especiales, como el caso de los modelos de espacio
de estados lineales Gaussianos donde es posible aplicar el ﬁltro de Kalman [1]
[2], es imposible evaluar analíticamente estas distribuciones.
Para los sistemas que son no lineales y/o no Gaussianos, una solución alterna-
tiva son los ya conocidos ﬁltros de partículas. Los mismos fueron introducidos por
Gordon et al [3], y la idea básica es producir una muestra de variables aleatorias
independientes, llamadas partículas, distribuidas aproximadamente de acuerdo
a la FDP a posteriori del estado. El sistema es modelado como una cadena dis-
creta de Markov oculta, que consiste en una ecuación de estados que describe
la evolución del estado en el tiempo, y una ecuación de medición que conecta
* This work was partially supported by the University of Buenos Aires, CONICET,
Argentina, and the Agencia Nacional de Promoción Cientíﬁca y Tecnológica, PICT
No. 209-2006, Argentina.
39JAIIO - AST 2010 - ISSN:1850-2806 - Página 1614

la observación con el estado. En el enfoque clásico se considera que el proceso
de medición y el estado siguiente son condicionalmente independientes dado el
estado actual. En [1] Anderson y Moore proponen un modelo lineal Gaussiano
donde el ruido del proceso de estados y el de medición están correlacionados.
Por otro lado, en los trabajos realizados por Arnaud, Memin y Cernuschi-
Frias [4], y por Arnaud, y Mémin [5] se propone un seguidor puntual en secuencia
de imágenes basado en el ﬁltrado de partículas donde todo el modelo, tanto la
ecuación del proceso como la de medición se obtienen a partir de l
```

---
