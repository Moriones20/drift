# Drift — Decisions Log

Registro de todas las decisiones tomadas durante el diseño. Cada decisión tiene contexto y alternativas consideradas para evitar re-litigar en el futuro.

---

## D001 — Tipo de proyecto

**Decisión:** Proyecto greenfield — bot de forex desde cero.
**Por qué:** El usuario tiene experiencia previa con bots de ETFs (ML, overfitting), crypto (negativo), y forex (problemas técnicos con cTrader/OANDA). Este es un nuevo intento con un approach diferente.

---

## D002 — Estrategia: Trend Following

**Decisión:** Trend following como estrategia base.
**Alternativas consideradas:**
- Mean reversion — viable pero requiere pares que se muevan en rango, más complejo de calibrar
- Híbrido (reglas + ML ligero) — guardado para futuro (Phase futura)
- ML puro — descartado por experiencia previa con overfitting
- Grid trading — descartado, riesgo de explosión en tendencias fuertes
- Scalping — descartado, latencia y spreads matan a retail

**Por qué:** Trend following es el más robusto a largo plazo, el más difícil de overfittear, y con $500 no se puede permitir muchos trades pequeños que se coman en spreads. La investigación confirmó que mean reversion y trend following son las únicas categorías consistentemente rentables para retail.

---

## D003 — Broker: ICMarkets + MT5

**Decisión:** ICMarkets con cuenta Raw Spread en MetaTrader 5.
**Alternativas consideradas:**
- OANDA (REST API) — el usuario nunca pudo crear cuenta demo, spreads más altos (~1.0-1.3 pips vs 0.1)
- cTrader — el usuario tuvo problemas de conexión y nunca pudo cerrar un trade
- Pepperstone — casi idéntico a ICMarkets pero menos apalancamiento (1:500 vs 1:1000) y más restricciones en LATAM
- FXCM — viable pero menos comunidad y spreads más altos
- Interactive Brokers — mínimo $10,000, fuera de presupuesto

**Por qué:** MT5 tiene paquete Python oficial que funciona nativamente en Windows (el OS del usuario). ICMarkets tiene spreads bajos, demo fácil, sin restricciones en Colombia, y apalancamiento hasta 1:1000. No carga con las malas experiencias previas de OANDA/cTrader.

---

## D004 — Timeframes: D1 + H4

**Decisión:** D1 para filtro de tendencia, H4 para señal de entrada.
**Alternativas consideradas:**
- Solo H4 — menos contexto de tendencia
- Solo D1 — pocas señales, entradas imprecisas
- D1 + H1 — más señales pero más ruido

**Por qué:** D1 da la dirección macro (¿la tendencia sube o baja?), H4 da el timing de entrada más preciso. Es la combinación clásica de trend following multi-timeframe.

---

## D005 — Indicadores: EMA 50/200 + MACD + ATR

**Decisión:** Cruce EMA 50/200 en D1 (dirección) + MACD 12,26,9 en H4 (entrada) + ATR 14 (stop loss).
**Alternativas consideradas:**
- Opción A (minimalista): 200 EMA + 20 EMA pullback + ATR — más simple pero menos señales
- Opción C (breakout Turtle Traders): ADX + Canal Donchian + ATR — más mecánico pero menos flexible

**Por qué:** La combinación B es la más conocida y documentada. Investigación confirmó que 2-3 indicadores es el sweet spot — más es overfitting, menos puede ser insuficiente. El cruce EMA es el filtro de tendencia más probado históricamente.

---

## D006 — Riesgo: 1% por trade

**Decisión:** 1% del balance actual por trade.
**Alternativas consideradas:**
- 2% — moderado, pero 10 trades malos = 20% de pérdida
- 3% — agresivo, rachas malas duelen demasiado con $500

**Por qué:** Con $500, la prioridad es sobrevivir y aprender. 1% permite perder 20 trades seguidos y conservar 80% del capital. Position sizing dinámico basado en balance actual (no fijo).

---

## D007 — Max trades simultáneos: 4

**Decisión:** Máximo 4 trades abiertos al mismo tiempo (4% del capital en riesgo máximo).
**Alternativas consideradas:**
- 3 — demasiado conservador, pocas oportunidades
- 5 — mitad del drawdown permitido comprometido
- Sin límite — demasiado riesgo

**Por qué:** Balance entre oportunidad y protección. Con trend following en H4, se esperan 1-3 trades por semana, así que 4 simultáneos da margen suficiente.

---

## D008 — Take profit: Trailing stop + ratio 1:2

**Decisión:** Dual — trailing stop que sigue al precio + take profit fijo a 1:2 del riesgo. El que se active primero cierra el trade.
**Alternativas consideradas:**
- Solo trailing stop — puede dejar correr demasiado y devolver ganancias
- Solo ratio fijo — puede salir antes de que la tendencia termine
- Señal opuesta (MACD) — más lento para reaccionar

**Por qué:** Dos capas de protección. El trailing captura tendencias fuertes, el TP fijo asegura ganancias cuando el precio alcanza 2x el riesgo.

---

## D009 — Drawdown máximo: 10%

**Decisión:** Si el capital baja 10% desde su pico, el bot se pausa automáticamente y notifica.
**Alternativas consideradas:**
- 15% — demasiado margen con $500
- 20% — perder $100 de $500 es significativo

**Por qué:** Con $500 cada dólar cuenta. Si pierdes 10%, algo no está funcionando y hay que analizar antes de seguir.

---

## D010 — Notificaciones: Telegram

**Decisión:** Bot de Telegram para notificaciones y comandos.
**Por qué:** El usuario lo pidió específicamente. Gratis, funciona en móvil, permite comandos bidireccionales.

---

## D011 — Base de datos: SQLite

**Decisión:** SQLite para almacenar trades, señales y estado.
**Alternativas consideradas:**
- PostgreSQL — overkill para un bot personal, requiere servidor

**Por qué:** Archivo local, cero mantenimiento, suficiente para un bot personal. Portátil al mover a VPS.

---

## D012 — Backtesting: Backtesting.py

**Decisión:** Backtesting.py como framework de backtesting.
**Alternativas consideradas:**
- Backtrader — popular pero no mantenido activamente
- VectorBT — más potente pero más complejo
- Custom con pandas — control total pero más trabajo

**Por qué:** Ligero, simple, ideal para prototipar rápido. Si se necesita más potencia, se puede migrar a VectorBT después.

---

## D013 — Selección de pares: Manual con iteración en demo

**Decisión:** Empezar con 6 pares majors fijos, observar rendimiento en demo, descartar y probar nuevos hasta encontrar la mejor lista.
**Alternativas consideradas:**
- Escaneo en tiempo real — más complejo, innecesario para empezar
- Backtesting masivo para selección — se descartó porque seleccionar pares solo por rendimiento pasado es "manejar mirando el retrovisor"

**Por qué:** Pragmático. Observar en demo da datos reales sin sobreingeniería. El usuario identificó correctamente que selección por backtest pasado no garantiza rendimiento futuro.

---

## D014 — Operación: 24/5

**Decisión:** El bot opera 24 horas, de lunes a viernes.
**Alternativas consideradas:**
- Solo sesiones Londres/NY — más volumen pero limita oportunidades

**Por qué:** Con timeframes D1/H4, la sesión importa menos. El bot analiza cada 4 horas, no necesita estar "pegado" a una sesión específica.

---

## D015 — Infraestructura: PC en demo, VPS en live

**Decisión:** PC personal durante fase de demo, migrar a VPS Windows cuando se pase a live.
**Alternativas consideradas:**
- VPS desde el inicio — gasto innecesario durante pruebas
- Solo PC — riesgo de interrupción en live

**Por qué:** No gastar dinero mientras se prueba. VPS solo se justifica cuando hay capital real en juego.

---

## D016 — Tiempo mínimo en demo: 2 meses

**Decisión:** Mínimo 2 meses en cuenta demo antes de considerar live.
**Alternativas consideradas:**
- 1 mes — puede ser suerte
- 3 meses — puede generar impaciencia innecesaria

**Por qué:** Suficiente para ver diferentes condiciones de mercado y ganar confianza sin apresurarse.

---

## D017 — Configuración: config.yaml

**Decisión:** Toda la configuración en un archivo `config.yaml`.
**Alternativas consideradas:**
- Variables de entorno — menos legible
- Interfaz gráfica — overkill

**Por qué:** Fácil de leer, editar y versionar. Un solo archivo para todo.

---

## D018 — Zona horaria: UTC interno, UTC-5 presentación

**Decisión:** El sistema trabaja internamente en UTC. Todas las notificaciones y reportes se presentan en UTC-5 (Colombia).
**Por qué:** UTC es estándar para sistemas financieros. La conversión a UTC-5 es solo para la capa de presentación al usuario.

---

## D019 — Reporte semanal: Domingos 8pm UTC-5

**Decisión:** Reporte automático por Telegram los domingos a las 8pm hora Colombia.
**Por qué:** Timing elegido por el usuario. Sirve como punto de partida para la sesión de journal dominical.

---

## D020 — Journal dominical: Análisis con datos, no emociones

**Decisión:** Sesión de análisis post-reporte cada domingo. Se investigan noticias relevantes. NO se cambia configuración reactivamente.
**Por qué:** Regla de oro de Drift — cambios solo con evidencia y análisis frío. Una mala semana no justifica cambios; un patrón de datos sí.

---

## D021 — Logging completo: Trades + señales rechazadas

**Decisión:** Guardar en SQLite no solo los trades ejecutados sino también cada señal analizada que fue rechazada, con el motivo.
**Por qué:** Permite "shadow trading" — ver qué hubiera pasado si el bot hubiera tomado trades que rechazó. Esencial para el journal dominical y para optimización futura.

---

## D022 — Reporte bajo demanda: Solo manual

**Decisión:** El comando `/report` es solo manual — el usuario lo ejecuta cuando quiere.
**Alternativas consideradas:**
- Automático diario + manual — el usuario prefirió solo manual

**Por qué:** Preferencia del usuario. El reporte semanal automático es suficiente para monitoreo pasivo.

---

## D023 — Estrategia futura: Híbrido con filtro de régimen

**Decisión:** Guardar en roadmap futuro la estrategia híbrida que detecta si el mercado está en tendencia o rango y aplica la estrategia correspondiente.
**Por qué:** Es la evolución natural del trend following. Se implementa cuando la base esté sólida y probada.

---

## D024 — Correlación: Máximo 2 trades en la misma dirección de una moneda

**Decisión:** Si ya hay 2 trades abiertos vendiendo la misma moneda (ej: EURUSD buy + GBPUSD buy = ambos venden USD), no abrir más en esa dirección.
**Alternativas consideradas:**
- Sin límite, solo loggear exposición — no protege activamente
- Ignorar — con 4 trades máximo el riesgo es limitado, pero 4 trades en la misma dirección del USD es esencialmente un solo trade disfrazado

**Por qué:** Pares como EURUSD y GBPUSD están altamente correlacionados. Sin este límite, 4 trades podrían ser una sola apuesta concentrada en el USD, multiplicando el riesgo real.

---

## D025 — Sin filtro de noticias en MVP

**Decisión:** No implementar filtro de noticias de alto impacto en el MVP. Revisar en el journal si las noticias causan problemas.
**Alternativas consideradas:**
- Filtro automático consultando calendario económico — más seguro pero requiere integrar fuente de datos externa
- Bloqueo manual vía comando Telegram — simple pero requiere intervención del usuario

**Por qué:** Con timeframes H4/D1, el bot no hace scalping. El ATR ya mide y se adapta a la volatilidad, incluyendo la causada por noticias. Si el journal dominical muestra que las noticias causan pérdidas consistentes, se agrega el filtro en una fase futura.

---

## D026 — Gaps de fin de semana: Cerrar trades en pérdida el viernes

**Decisión:** Antes del cierre del mercado el viernes, cerrar trades que estén en pérdida o recién abiertos. Mantener abiertos solo los que estén en ganancia (protegidos por trailing stop).
**Alternativas consideradas:**
- Cerrar todo el viernes — elimina riesgo pero puede cortar tendencias buenas
- Mantener todo abierto — riesgo de gap fuerte el lunes que pase el stop loss

**Por qué:** Un trade en ganancia ya tiene trailing stop que lo protege y vale la pena dejarlo correr. Un trade en pérdida o nuevo tiene más riesgo que beneficio sobre el fin de semana, donde eventos geopolíticos pueden causar gaps de 100+ pips.

---

## D027 — Backtest de validación antes de demo

**Decisión:** Hacer un backtest rápido en 1-2 pares antes de correr en demo, para validar que la estrategia no es fundamentalmente mala. El backtest masivo y optimización se mantienen en Phase 2.
**Alternativas consideradas:**
- Backtest completo primero — más riguroso pero retrasa el inicio de demo
- Sin backtest previo — correr directo en demo (dinero ficticio, sin riesgo real)

**Por qué:** Balance entre rapidez y validación. No necesitas un backtest completo para arrancar en demo, pero sí necesitas saber que la estrategia tiene algún mérito antes de invertir 2 meses observándola.

---

## D028 — Comisiones incluidas en cálculos

**Decisión:** Incluir la comisión de ICMarkets ($7/lote round trip) en el cálculo del P&L, reportes y ratio de riesgo/beneficio.
**Alternativas consideradas:**
- Ignorar — con micro lotes la comisión es centavos (~$0.07-$0.35 por trade)

**Por qué:** Aunque la diferencia es pequeña con micro lotes, incluir comisiones hace que los reportes reflejen la realidad exacta. Es fácil de implementar y previene sorpresas al escalar el tamaño de las posiciones.
