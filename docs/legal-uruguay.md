# Servio y la ley uruguaya de protección de datos: investigación preliminar

> **Aviso:** esto es investigación informativa para un equipo de hackathon, **no es asesoramiento legal**. Antes de vender u operar Servio en un restaurante real, consultá con un abogado uruguayo especializado en protección de datos y en derecho laboral. Fecha de la investigación: 19/10/2026. Cuando no se pudo verificar algo en una fuente primaria, se marca **«no verificado»**.

---

## Resumen ejecutivo

- **Veredicto: el producto es viable, con condiciones.** Ninguna norma uruguaya prohíbe usar cámaras en el salón ni medir el desempeño laboral. La URCDP admite de forma expresa que el empleador use cámaras «para controlar el desempeño de las tareas laborales» (Res. URCDP 58/021, apartado IV). Esa admisión tiene condiciones: inscribir la base, informar a los trabajadores, respetar zonas prohibidas, poner señalética, cumplir los principios de finalidad y proporcionalidad, y regularizar la transferencia internacional.
- **La imagen es un dato personal aunque no se analicen caras.** Captar o transmitir imágenes de personas identificables ya es «videovigilancia» y cae bajo la Ley 18.331, aunque no se grabe nada (Dictamen URCDP 10/010). La cámara de Servio ve a clientes y mozos. Por eso, mientras el frame contenga personas, se aplican la ley, el cartel y la inscripción.
- **El mayor riesgo jurídico es mandar frames a Anthropic (EE. UU.).** EE. UU. **no** es un país adecuado en general. Solo lo son las organizaciones del EU-US Data Privacy Framework, y además se exige una declaración extra del importador (Res. 23/021, 63/023 y 70/023). No se pudo verificar que Anthropic esté en ese listado. Las alternativas son consentimiento, cláusulas contractuales con **autorización previa de la URCDP** (Res. 50/022 y 8/2026) o **no mandar datos personales**: recortar solo el plato en el dispositivo antes de enviarlo.
- **La evaluación de impacto (EIPD) es obligatoria.** El Decreto 64/020, art. 6, la exige cuando se perfila el «rendimiento en el trabajo» (lit. c) y cuando se transfieren datos a un país no adecuado (lit. f). Servio cumple el primer supuesto siempre, y el segundo si manda frames con personas a EE. UU.
- **El restaurante es el responsable y Servio, el encargado.** El restaurante inscribe la base y responde ante la URCDP. Servio necesita un contrato de encargo y tiene que diseñar el producto con privacidad desde el diseño (art. 12 de la Ley 18.331, en la redacción de la Ley 19.670).

---

## 1. Marco general de protección de datos

### Normas aplicables

| Norma | Qué aporta a Servio | Fuente |
|---|---|---|
| **Ley 18.331** (2008), de protección de datos y acción de habeas data | Define qué es un dato personal y fija los principios, el consentimiento, el derecho de información, la inscripción, las transferencias y las sanciones | https://www.impo.com.uy/bases/leyes/18331-2008 |
| **Decreto 414/009**, reglamentario | Regula la inscripción y el trámite de las transferencias internacionales | https://www.impo.com.uy/bases/decretos/414-2009 |
| **Ley 19.670**, arts. 37 a 40 | Agrega extraterritorialidad, aviso de vulneraciones, responsabilidad proactiva y delegado | https://www.impo.com.uy/bases/leyes/19670-2018/37 |
| **Decreto 64/020** | Reglamenta las vulneraciones, la EIPD, la privacidad desde el diseño y el delegado | https://www.impo.com.uy/bases/decretos/64-2020 |
| **Convenio 108 / 108+** del Consejo de Europa | Uruguay ratificó el 108 (aprobado por Ley 19.030; es Parte desde el 1/8/2013) y el Protocolo CETS 223, conocido como 108+ (Ley 19.948 del 16/4/2021; instrumento depositado el 5/8/2021) | https://www.coe.int/en/web/data-protection/-/uruguay-ratfies-convention-108- · https://www.gub.uy/presidencia/comunicacion/noticias/uruguay-ratifico-ultimo-tratado-internacional-sobre-proteccion-datos |
| **Constitución**, arts. 7, 53, 54 y 72 | Protegen la vida, el honor y la libertad (art. 7), el trabajo (arts. 53 y 54) y los derechos inherentes a la personalidad humana (art. 72) | https://www.impo.com.uy/bases/constitucion/1967-1967 |

No se verificó la fecha exacta de entrada en vigor internacional del 108+ para Uruguay.

### Conceptos clave (Ley 18.331, art. 4)

- **Dato personal (lit. D):** «información de cualquier tipo referida a personas físicas o jurídicas determinadas o determinables». El Decreto 414/009, art. 1, incluye expresamente la información «gráfica, fotográfica».
  - Una imagen en la que se puede reconocer a una persona es un dato personal.
  - El mozo también es identificable: Servio vincula cada mesa a un mozo y a su cuenta de Telegram.
- **Dato sensible (lit. E):** datos que revelan origen racial y étnico, preferencias políticas, convicciones religiosas o morales, afiliación sindical, salud o vida sexual.
  - En principio, «cuánta comida queda en un plato» no es un dato sensible.
  - Hay un riesgo marginal: que un frame muestre algo que revele salud o religión, como una dieta especial o un símbolo religioso. Esto se reduce recortando solo el plato.
- **Datos biométricos (lit. Ñ, agregado por la Ley 19.924, art. 86):** datos obtenidos con un «tratamiento técnico específico» que permiten o confirman la identificación única de una persona, por ejemplo el «reconocimiento de imagen o voz».
  - Servio **no** hace reconocimiento facial, así que no trata datos biométricos.
  - Si alguna vez identificara personas por la cara, la Res. URCDP 30/020 exige una EIPD para todo tratamiento biométrico: https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-30020
- **Disociación (lit. G):** tratamiento que impide asociar la información a una persona determinada o determinable. Un recorte que muestre **solo el plato**, sin personas, está disociado y deja de ser un dato personal. Esta es la base jurídica de la estrategia de minimización.
- **Responsable (lit. K) y encargado (lit. H):** el restaurante decide la finalidad, así que es el responsable. Servio, como proveedor, trata los datos por cuenta del restaurante y es el encargado. Anthropic es un subencargado.

### Principios y bases legales

- **Principios (art. 5):** legalidad, veracidad, finalidad, consentimiento informado, seguridad, reserva y responsabilidad.
- **Veracidad (art. 7):** los datos tienen que ser «adecuados, ecuánimes y no excesivos en relación con la finalidad». De aquí sale el principio de proporcionalidad.
- **Finalidad (art. 8):** no se pueden usar los datos para fines «distintos o incompatibles», y hay que eliminarlos cuando dejan de ser necesarios.
- **Consentimiento (art. 9):**
  - La regla es el consentimiento «libre, previo, expreso e informado», que debe documentarse.
  - Una excepción (lit. D) es que los datos «deriven de una relación contractual, científica o profesional del titular… y sean necesarios para su desarrollo o cumplimiento».
  - **Uruguay no tiene una base de «interés legítimo» como el RGPD europeo.** Esto importa para los clientes; ver la sección 2.
- **Responsabilidad proactiva (art. 12, en la redacción del art. 39 de la Ley 19.670):** obliga a adoptar medidas como «privacidad desde el diseño, privacidad por defecto, evaluación de impacto».
- **Derecho de información (art. 13):** hay que informar, entre otras cosas, la finalidad, la existencia de la base, la identidad y el domicilio del responsable, los derechos y si hay «transferencias internacionales de datos» (lit. F).
- **Decisiones automatizadas (art. 16):** las personas tienen derecho a no quedar sujetas a una decisión con efectos jurídicos significativos basada **solo** en un tratamiento automatizado que evalúe su conducta o rendimiento. También pueden pedir los criterios usados. Es relevante si las métricas de Servio se usan para sancionar a mozos; ver la sección 3.

### Ley 19.670 y Decreto 64/020

- **Extraterritorialidad (Ley 19.670, art. 37):** la ley se aplica a responsables y encargados establecidos en Uruguay. También alcanza a los de afuera cuando ofrecen bienes o servicios a habitantes del país o analizan su comportamiento. Si Servio se constituye fuera de Uruguay pero opera allí, igual queda alcanzado.
- **Vulneraciones de seguridad (Ley 19.670, art. 38, y Decreto 64/020, arts. 3 y 4):**
  1. Iniciar medidas para minimizar el impacto dentro de las **24 h** de constatado el incidente.
  2. Notificar a la URCDP en **72 h como máximo**.
  3. Informar a los titulares en lenguaje claro cuando sus derechos se vean afectados de forma significativa.
  4. El encargado (Servio) tiene que avisar **de inmediato** al responsable (el restaurante).
- **Privacidad desde el diseño y por defecto (Decreto 64/020, arts. 8 y 9):** el art. 9 exige que «solo sean objeto de tratamiento los datos personales que sean necesarios para cada uno de los fines específicos».
- **EIPD (Decreto 64/020, arts. 6 y 7):** debe hacerse **antes** de empezar el tratamiento cuando haya, entre otros supuestos:
  - lit. c: evaluación de aspectos personales para crear o usar perfiles, «en particular mediante el análisis o la predicción de aspectos referidos a su rendimiento en el trabajo»;
  - lit. e: grandes volúmenes de datos;
  - lit. f: transferencias a Estados sin nivel adecuado de protección.

  **Servio queda alcanzado por el lit. c**, y por el lit. f si manda frames con personas a EE. UU. Fuente: https://www.impo.com.uy/bases/decretos/64-2020/6
- **Delegado de protección de datos (Ley 19.670, art. 40, y Decreto 64/020, art. 10):** es obligatorio para entidades públicas, para privadas que traten datos sensibles como negocio principal y para privadas que traten grandes volúmenes, es decir, datos de **más de 35.000 personas**.
  - Un restaurante normalmente **no** está obligado.
  - Servio, como encargado de muchos restaurantes, podría superar el umbral si cuenta a los clientes captados. Esta interpretación **no está verificada**.
- **Sanciones (Ley 18.331, art. 35):**
  - Van desde observación y apercibimiento hasta una multa de **500.000 UI**, suspensión de la base por 5 días y clausura.
  - Ejemplo real: la Res. URCDP 37/2025 (14/11/2025) multó con 6.000 UI a particulares denunciados por videovigilancia que no respondieron a la Unidad. Fuente: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2026-03/Res.%2037%202025.pdf

---

## 2. Videovigilancia y transferencia internacional

### Qué dice la URCDP

**Dictamen URCDP 10/010 (16/4/2010)**

Fuente: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/documentos/publicaciones/dictamen-10-010.pdf

- Define la videovigilancia como «toda grabación, captación, **transmisión**, conservación y almacenamiento de imágenes». Esas imágenes «constituyen información personal» y por lo tanto se aplica la ley.
  - **Consecuencia para Servio:** no guardar video **no** saca a Servio de la ley. Captar y transmitir frames ya es tratamiento.
- La ley se aplica cuando las cámaras captan imágenes «que refiera[n] a personas identificadas o identificables» (Considerando V).
- Principios que aplica (Considerando VI):
  - legalidad, que implica **inscribir la base** en el Registro de la URCDP;
  - finalidad, que debe quedar **por escrito** junto con una política de privacidad;
  - consentimiento informado;
  - veracidad, de modo que la videovigilancia sea **subsidiaria** y se use solo «cuando no exista otro medio menos lesivo de la intimidad»;
  - seguridad.
- Conservación: se borran las imágenes al agotarse la finalidad (Considerando VII).
- Obligaciones (Considerando VIII): inscribir la base e «informar a las personas que sus imágenes están siendo captadas».
- Distintivo o cartel obligatorio (Considerando IX y Dictamina II): debe indicar ante quién (responsable y domicilio) se ejercen los derechos, con la matriz que diseña la URCDP.

**Resolución URCDP 989/010 (30/7/2010)**

- Aprueba los logos de videovigilancia que deben usar los responsables públicos y privados. Lo confirma la Res. 58/021, Considerando II.
- No se leyó el PDF de la 989/010 (**no verificado** en su detalle).
- Ficha: https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-989010
- Logos descargables, por ejemplo: https://www.gub.uy/unidad-reguladora-control-datos-personales/comunicacion/publicaciones/logo-videovigilancia-formato-horizontal-150mm-x-81-mm

**Resolución URCDP 58/021 (21/12/2021), criterios consolidados**

Fuente: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2022-01/Resoluci%C3%B3n%2058-%202021.pdf

- Remite al Dictamen 10/010 y a la Res. 989/010 como criterio general.
- Su apartado IV trata el ámbito laboral; ver la sección 3.
- No tiene un apartado sobre comercios o locales abiertos al público, así que un restaurante cae bajo el criterio general.
- **No fija un plazo de conservación en días.** No se encontró en fuentes primarias un plazo máximo general, como los 30 días que suelen mencionarse: **no verificado**.

**Guía de videovigilancia en el ámbito laboral (URCDP)**

- Página: https://www.gub.uy/unidad-reguladora-control-datos-personales/comunicacion/publicaciones/guia-videovigilancia-ambito-laboral
- Devolvió HTTP 403, así que su contenido **no se verificó** directamente.

### ¿Hacen falta cartel e inscripción si no se analizan caras ni se guarda video?

**Sí, mientras la cámara capte personas identificables.**

- La definición del Dictamen 10/010 incluye la mera «captación» y «transmisión».
- El criterio jurídico es si la persona es identificable en la imagen, no si el software la analiza.
- Una cámara cenital en un salón capta cuerpos, manos, ropa y a veces caras. Con contexto (hora, mesa, reserva) eso puede identificar a alguien.
- Por lo tanto:
  - **Cartel o distintivo** en la entrada y en el salón, con el logo de la URCDP y los datos del responsable y su domicilio.
  - **Inscripción de la base** «Videovigilancia / Servio» ante la URCDP (Ley 18.331, arts. 28 y 29; Decreto 414/009, arts. 15 a 17).
    - El plazo es de **90 días desde el inicio de actividades** (Decreto 414/009, art. 17).
    - El número de inscripción debe quedar visible (art. 19).
  - **Base de desempeño de mozos:** las alertas, respuestas y tiempos por mozo forman una base distinta, o un fin adicional de la misma base, que también hay que declarar.

**Consentimiento de los clientes.**

- El Dictamen 10/010 dice que hace falta consentimiento salvo en seguridad pública. La ley uruguaya no tiene «interés legítimo» como base.
- En la práctica, el modelo de la URCDP para comercios se apoya en **información previa con cartel e inscripción**. No se encontró una resolución que exija consentimiento escrito de cada cliente de un comercio videovigilado.
- **Esta es una zona gris (no verificado):** entrar al local después de haber sido informado se trata, en los hechos, como consentimiento tácito. Para reducir el riesgo:
  1. Minimizar: que Servio no conserve ni envíe imágenes de personas.
  2. Informar claramente con cartel y aviso de privacidad.

### Envío de frames a la nube (Anthropic, EE. UU.)

**¿Es una transferencia internacional?** Sí.

- La Res. 23/021, Considerando I, dice que la transferencia internacional «supone su transmisión fuera del territorio nacional y constituye una cesión o comunicación que tiene por objeto la realización de un tratamiento por cuenta del responsable».
- Mandar un frame con personas a una API en EE. UU. encaja en esa definición.

**¿EE. UU. es un país adecuado? Solo en parte.**

1. **Res. 23/021 (8/6/2021):** sacó a EE. UU. del listado de países adecuados tras el fallo Schrems II. Dice que las transferencias a EE. UU. «deberán justificarse a través del consentimiento de los interesados o de alguna de las excepciones previstas en el artículo 23… y en su caso, contar con la autorización expresa de esta Unidad».
   - Países adecuados según esa resolución: miembros de la UE y el EEE, Andorra, Argentina, Canadá (sector privado), Guernsey, Isla de Man, Islas Feroe, Israel, Japón, Jersey, Nueva Zelanda, Reino Unido y Suiza.
   - PDF: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2021-09/RESOLUCION%2023%202021.pdf
2. **Res. 63/023 (21/11/2023):** agrega como adecuadas **solo a las organizaciones del listado del Marco de Privacidad de Datos** (Data Privacy Framework) del Departamento de Comercio de EE. UU. También agrega a Corea del Sur. https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-63023
3. **Res. 70/023 (publicada el 5/12/2023):** para esas organizaciones, el exportador uruguayo debe presentar ante la URCDP una «declaración expresa» del importador. En ella el importador declara «haber extendido la aplicación de las salvaguardas de dicho marco a los datos transferidos desde Uruguay». Si no hay declaración, se pueden usar cláusulas contractuales «autorizadas previamente por esta Unidad» u otros fundamentos legales. https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-70023

**¿Anthropic está en el Data Privacy Framework? No verificado.**

- La búsqueda no lo confirmó.
- El DPA público de Anthropic se basa en las cláusulas contractuales tipo de la UE, no en el DPF, y define a Anthropic como encargado («Customer is the controller and Anthropic is Customer's processor»).
- Ese DPA no menciona Uruguay. https://www.anthropic.com/legal/data-processing-addendum

**Vías legales para transferir (Ley 18.331, art. 23)**

https://www.impo.com.uy/bases/leyes/18331-2008/23

| Vía | Viabilidad para Servio |
|---|---|
| a) **Consentimiento inequívoco** del interesado (lit. A) | Poco práctico para clientes. Posible para mozos, aunque el consentimiento de un empleado frente a su empleador es dudosamente «libre» |
| b) Necesaria para ejecutar un contrato con el interesado (lit. B y C) | No aplica a los clientes, cuyo contrato no requiere el análisis de platos. Es dudoso para los mozos |
| c) **Garantías suficientes con cláusulas contractuales y autorización de la URCDP** (inciso final) | Es la vía formal. Puede usar las cláusulas RIPD (Res. 50/022) o las del Consejo de Europa del Convenio 108+ (Res. 8/2026, 17/4/2026). **Ambas resoluciones aclaran que usarlas «no excluye la autorización previa de esta Unidad»** |
| d) Organización del DPF más declaración (Res. 63/023 y 70/023) | Solo si Anthropic figura en el DPF y firma la declaración. **No verificado** |
| e) **No transferir datos personales**: recortar o anonimizar en el dispositivo y mandar solo el plato | **Es la mejor opción.** Si el recorte no contiene personas identificables, no es un dato personal (art. 4, lit. G, disociación) y no hay transferencia internacional de datos personales |

Fuentes de la vía c):
- Res. 50/022: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2023-03/Res.%2050-2022.pdf
- Res. 8/2026: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2026-05/Res.%208%20URCDP%20-%20Aprobaci%C3%B3n%20Cl%C3%A1usulas%20Contractuales%20Consejo%20de%20Europa.pdf

El trámite de autorización lo inicia el exportador y debe incluir una copia del contrato (Decreto 414/009, arts. 34 y 35).

---

## 3. Control laboral: medir el desempeño de los mozos

### ¿Es legal?

**Sí, con condiciones.** La Res. URCDP 58/021, apartado IV, dice que «cuando las cámaras se utilicen en el ámbito laboral, el empleador puede adoptar medidas que permitan **controlar el desempeño de las tareas laborales** de sus dependientes», con estas condiciones:

- **a. Inscripción y responsables:** designar quién hace la inscripción, quién accede a las imágenes y quién atiende las solicitudes de ejercicio de derechos.
- **b. Información a los empleados:** informar «que se instalarán cámaras, los lugares donde estarán localizadas, si captan imagen y voz, el tiempo de conservación de las imágenes y cómo ejercer sus derechos». Además, «no deben registrarse conversaciones privadas».
  - Servio no usa audio, y conviene mantenerlo así.
- **c. Zonas prohibidas:** no se pueden poner cámaras en «baños, vestuarios, espacios de descanso, lugares destinados a la alimentación o con finalidades sindicales y cualquier otro sitio que pueda invadir la intimidad de los empleados».
  - El salón es un recinto de trabajo para los mozos.
  - **Ojo:** la cámara no debe cubrir el área donde comen o descansan los empleados, como el comedor del personal o una mesa del fondo usada para comer.
- **d. Acceso restringido** al sistema.
- **e. Contrato con el proveedor** del servicio: es recomendable firmar uno que defina quién accede a la información.

### Base legal para los datos de los mozos

- Ley 18.331, art. 9, lit. D: no hace falta consentimiento cuando los datos derivan de la relación contractual y son **necesarios** para su desarrollo o cumplimiento.
  - Se puede argumentar que los tiempos de atención de mesa se relacionan con la ejecución del trabajo.
  - **No verificado como criterio de la URCDP** para métricas individuales.
  - Sigue siendo obligatorio informar (art. 13) y cumplir finalidad y proporcionalidad (arts. 7 y 8).
- Límites constitucionales:
  - art. 7: honor y libertad;
  - art. 53: el trabajo «bajo la protección especial de la ley»;
  - art. 54: garantías para quien está en una relación de trabajo;
  - art. 72: derechos inherentes a la personalidad humana, que es la base doctrinaria de la dignidad e intimidad del trabajador.
- No se encontró una ley uruguaya específica sobre control electrónico del trabajador: **no verificado**.

### ¿Se pueden usar las métricas para sancionar?

- **No hay una prohibición expresa.** Pero:
  1. **Finalidad (art. 8):** si se informó que la finalidad es «mejorar el servicio y avisar al mozo», usar los datos para sancionar o despedir es un fin distinto que debe haberse informado de antemano. Hay que declararlo desde el inicio, o bien excluirlo.
  2. **Decisiones automatizadas (art. 16):** una sanción basada **únicamente** en un ranking automático es impugnable. Tiene que haber revisión humana y explicación de los criterios.
  3. **Veracidad (art. 7):** las estimaciones del LLM tienen error, como confundir un plato «casi terminado» con uno «vacío». Usarlas como prueba disciplinaria sin validación es riesgoso.
  4. **Libertad sindical:** la cámara no puede captar actividad sindical (Res. 58/021, IV.c). Usar métricas para represalias antisindicales tiene un riesgo alto. Antecedente: la Res. URCDP 79/014 sancionó con 3.001 UI un caso de videovigilancia en el lugar de trabajo vinculado a la actividad sindical (citada en «Principales criterios administrativos 2009-2015» de la URCDP; el texto de la resolución **no se verificó**). https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/documentos/publicaciones/Principales%20criterios%20administrativos%202009-2015.pdf

### MTSS, sindicatos y negociación colectiva

- No se encontró una norma que exija consultar al sindicato ni pedir autorización al MTSS antes de instalar cámaras o métricas: **no verificado**.
- Uruguay tiene negociación colectiva tripartita por sector, con Consejos de Salarios que incluyen a gastronomía. Conviene revisar si el convenio colectivo vigente del sector tiene cláusulas sobre control tecnológico: **no verificado**.
- Como buena práctica, conviene informar y, de ser posible, acordar el uso de las métricas con los trabajadores o su representación.

### Jurisprudencia

- No se encontraron sentencias uruguayas sobre cámaras o métricas de desempeño de empleados en las fuentes consultadas: **no verificado**.
- Como referencia comparada no vinculante, el TEDH, en *López Ribalda c. España* (Gran Sala, 2019), valoró que se informe previamente al trabajador y que la medida sea proporcionada.

---

## 4. Checklist práctico para Servio en Uruguay

**A. Diseño del producto (lo que el equipo controla)**

1. [ ] **Recortar en el dispositivo:** YOLO corre en local y solo se manda a la nube el recorte del plato, sin personas. Opcionalmente, difuminar manos o cuerpos que entren en el recorte. Así se busca la disociación (art. 4, lit. G) y se evita, o al menos reduce, la transferencia internacional.
2. [ ] **No guardar frames ni video.** Persistir solo los estados de los platos. Si se guarda algo para depurar, que sea temporal, cifrado y con un plazo de borrado corto y documentado.
3. [ ] **Nada de reconocimiento facial ni audio.** Así no se tratan datos biométricos (art. 4, lit. Ñ; Res. 30/020) ni se graban conversaciones (Res. 58/021, IV.b).
4. [ ] **Encuadre de la cámara:** solo las mesas. Excluir el área de descanso o comida del personal, los baños, la vía pública y los espacios sindicales (Res. 58/021, IV.c).
5. [ ] **Minimizar los datos de los mozos:** guardar un identificador interno en lugar del nombre cuando sea posible, conservar las métricas por un plazo definido (por ejemplo, agregar después de N meses) y limitar el acceso al dueño o encargado.
6. [ ] **Privacidad por defecto:** mostrar métricas agregadas por defecto, con el detalle individual como opción, y ofrecer revisión humana antes de cualquier uso disciplinario.
7. [ ] **Seguridad:** cifrado, control de acceso y registros de auditoría. Tener un plan de respuesta a incidentes: 24 h para contener y 72 h para notificar a la URCDP (Decreto 64/020, arts. 3 y 4).

**B. Trámites del restaurante (responsable), con apoyo de Servio**

8. [ ] **Inscribir las bases ante la URCDP** dentro de los 90 días desde el inicio: «videovigilancia/análisis de mesas» y «desempeño de personal». Declarar la finalidad, la conservación y las transferencias internacionales (Ley 18.331, art. 29; Decreto 414/009, arts. 15 a 17).
9. [ ] **Cartel o distintivo de la URCDP** visible en la entrada y en el salón, con el responsable y su domicilio (Dictamen 10/010; Res. 989/010).
10. [ ] **Aviso de privacidad para clientes** según el art. 13: finalidad (gestión del servicio), responsable, encargado, derechos, transferencias internacionales si las hay, y que no se reconocen caras ni se guarda video. Puede estar en un QR en la mesa o la carta.
11. [ ] **Información escrita a los mozos** según la Res. 58/021, IV.b, y el art. 13: ubicación de las cámaras, que no hay audio, qué métricas se calculan, para qué, plazos de conservación, quién las ve, si pueden o no usarse para evaluar o sancionar, y cómo ejercer sus derechos. Hacerla firmar como constancia de recepción.
12. [ ] **EIPD antes de empezar** (Decreto 64/020, arts. 6 y 7): por el perfilado del rendimiento laboral (lit. c) y, si se manda algo con personas a EE. UU., por la transferencia (lit. f). Servio puede entregar una plantilla de EIPD prellenada.
13. [ ] **Contrato de encargo** entre el restaurante y Servio: objeto, instrucciones, seguridad, subencargados (Anthropic), confidencialidad, borrado y aviso de incidentes (art. 12; Res. 58/021, IV.e).

**C. Transferencia internacional (si se mandan imágenes con personas)**

14. [ ] Elegir una vía:
    - a) verificar si Anthropic está en el DPF y obtener la declaración de la Res. 70/023;
    - b) firmar cláusulas RIPD o del Consejo de Europa y **pedir autorización previa a la URCDP** (Res. 50/022 y 8/2026; Decreto 414/009, arts. 34 y 35);
    - c) evaluar un proveedor o región en un país adecuado, por ejemplo un endpoint en la UE (no verificado que exista para el modelo usado).
15. [ ] Revisar las condiciones del proveedor: rol de encargado, retención y no uso para entrenamiento (el DPA de Anthropic dice que el cliente es el responsable y Anthropic el encargado).

**D. Otros**

16. [ ] **Delegado de protección de datos:** probablemente no sea obligatorio para un restaurante. Servio debe revisarlo si trata datos de más de 35.000 personas (Decreto 64/020, art. 10).
17. [ ] **Revisar el convenio colectivo** de gastronomía y considerar informar al sindicato (buena práctica; no verificado como obligación).

---

## 5. Veredicto

**Servio es legalmente viable en Uruguay, con condiciones.**

- La normativa permite cámaras en locales y el control del desempeño laboral (Res. 58/021, IV) siempre que se cumplan estas condiciones:
  - inscripción;
  - cartel;
  - información previa a clientes y trabajadores;
  - finalidad declarada;
  - proporcionalidad y subsidiariedad;
  - zonas excluidas;
  - EIPD;
  - un régimen válido para la transferencia internacional.
- El diseño actual, que no procesa caras, no guarda video y guarda solo estados de platos, va en la dirección correcta. **No alcanza por sí solo** para quedar fuera de la ley, porque la captación y la transmisión ya son tratamiento.

### Riesgos principales, de mayor a menor

1. **Transferencia a EE. UU. sin cobertura.** Mandar frames con clientes o mozos a Anthropic sin DPF más declaración, sin cláusulas autorizadas y sin consentimiento viola el art. 23 y las Res. 23/021, 63/023 y 70/023. **Mitigación:** mandar solo recortes de platos sin personas.
2. **Uso disciplinario de métricas imprecisas.** Puede violar la finalidad (art. 8), la veracidad (art. 7) y el régimen de decisiones automatizadas (art. 16). Además puede generar conflicto laboral o sindical.
3. **Falta de inscripción, cartel o información.** Es la infracción más común en las resoluciones de la URCDP sobre videovigilancia. Las multas llegan hasta 500.000 UI (art. 35).
4. **Falta de EIPD.** Es obligatoria por el perfilado laboral (Decreto 64/020, art. 6, lit. c).
5. **Captación de zonas prohibidas** (descanso o comida del personal) **o de la vía pública** por un mal encuadre de la cámara.
6. **Zona gris sobre el consentimiento de los clientes.** El Dictamen 10/010 habla de consentimiento y no hay base de interés legítimo. Se mitiga con información clara y minimización.

**Para la demo de la hackathon** no hace falta nada de esto. Para un piloto en un restaurante real hacen falta, como mínimo, los ítems 1, 2, 4, 8 a 13 y 14 o 1 del checklist.

---

## Fuentes consultadas

- Ley 18.331: https://www.impo.com.uy/bases/leyes/18331-2008. Artículos consultados: 4, 9, 12, 13, 23 y 35, por ejemplo https://www.impo.com.uy/bases/leyes/18331-2008/4
- Ley 19.670, arts. 37 a 40: https://www.impo.com.uy/bases/leyes/19670-2018/37, /38, /39 y /40
- Decreto 414/009: https://www.impo.com.uy/bases/decretos/414-2009
- Decreto 64/020: https://www.impo.com.uy/bases/decretos/64-2020 (art. 6: https://www.impo.com.uy/bases/decretos/64-2020/6)
- Constitución: https://www.impo.com.uy/bases/constitucion/1967-1967
- Dictamen URCDP 10/010: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/documentos/publicaciones/dictamen-10-010.pdf
- Res. URCDP 58/021: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2022-01/Resoluci%C3%B3n%2058-%202021.pdf
- Res. URCDP 989/010 (ficha): https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-989010
- Res. URCDP 30/020 (biometría): https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-30020
- Res. URCDP 23/021: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2021-09/RESOLUCION%2023%202021.pdf
- Res. URCDP 63/023: https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-63023
- Res. URCDP 70/023: https://www.gub.uy/unidad-reguladora-control-datos-personales/institucional/normativa/resolucion-n-70023
- Res. URCDP 50/022: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2023-03/Res.%2050-2022.pdf
- Res. URCDP 8/2026: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2026-05/Res.%208%20URCDP%20-%20Aprobaci%C3%B3n%20Cl%C3%A1usulas%20Contractuales%20Consejo%20de%20Europa.pdf
- Res. URCDP 37/2025: https://www.gub.uy/unidad-reguladora-control-datos-personales/sites/unidad-reguladora-control-datos-personales/files/2026-03/Res.%2037%202025.pdf
- Convenio 108+: https://www.coe.int/en/web/data-protection/-/uruguay-ratfies-convention-108-
- DPA de Anthropic: https://www.anthropic.com/legal/data-processing-addendum
