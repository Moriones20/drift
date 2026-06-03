# Drift — Decisions Log

Registro de todas las decisiones tomadas durante el diseño. Cada decisión tiene contexto y alternativas consideradas para evitar re-litigar en el futuro.

---

## D001 — Tipo de proyecto

**Decisión:** Proyecto greenfield — bot de forex desde cero.
**Por qué:** El usuario tiene experiencia previa con bots de ETFs (ML, overfitting), crypto (negativo), y forex (problemas técnicos con cTrader/OANDA). Este es un nuevo intento con un approach diferente.

---

## D002 — Estrategia: Trend Following

> ⚠️ SUPERSEDIDA por D029 — Asian Session Scalper. Conservada para contexto histórico.

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

> ⚠️ SUPERSEDIDA por D029 — Asian Session Scalper. Conservada para contexto histórico.

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

> ⚠️ SUPERSEDIDA por D029 — Asian Session Scalper. Conservada para contexto histórico.

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

> ⚠️ MATIZADA por [[D037]] y [[D039]]. El *cálculo de sesión* NO usa UTC sino hora de servidor MT5 (GMT+3). UTC se mantiene como base de **almacenamiento** y el reporte semanal sí dispara en UTC. Ver D039 para las tres capas (servidor / UTC / Bogotá).

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

---

## D029 — Cambio de estrategia a Asian Session Scalper

> ⚠️ Nota: donde D029 dice 'GMT' léase hora de servidor MT5 (GMT+2/+3 con DST, [[D041]]). La hipótesis de 'sesión asiática tranquila' fue corregida — la ventana es el *daily lull* (cierre NY → pre-Tokio), no la sesión asiática — y la estrategia fue **renombrada a Daily Lull Scalper** ([[D042]]). El nombre 'Asian Session Scalper' se conserva en esta entrada como registro histórico. Ver [[D037]], [[D039]], [[D041]], [[D042]].

**Decisión:** Reemplazar la estrategia activa (mean reversion H4 con filtro MLP) por Asian Session Scalper en M15, operando solo durante la ventana 21:00-02:00 GMT. Ver `docs/knowledge/daily-lull-scalper.md` para las reglas.

**Recorrido hasta llegar aquí:**

| Iteración | Estrategia | Resultado backtest (mejor par) |
|---|---|---|
| 1 | Trend following EMA 50/200 + MACD (D002+D005) | Negativo en mayoría de pares — majors demasiado eficientes |
| 2 | Trend following v2: EMA pullback + RSI + ADX | +0.1%, PF 1.31 — insuficiente |
| 3 | Mean reversion H4: BB + RSI + ADX < 25 | +0.1% portfolio, problemas R:R |
| 4 | Mean reversion H4 + MLP regime filter (walk-forward) | AUDCAD PF 1.74, WR 60%, solo 10 trades en 6 meses |
| 5 | **Asian Session Scalper M15** | **EURCHF PF 8.17, AUDNZD PF 4.29, 277 trades en 2 años** |

> ⚠️ **Cifras infladas — ver [[D043]].** Estos números (PF 8.17, 277 trades) NO se reproducen: salieron de un snapshot con parámetros más restrictivos que los que corre el bot. Las métricas reproducibles reales (params universales de `config.yaml`, snapshot 2026-06-03): EURCHF **4.14**, AUDNZD **5.83**, portfolio **+8.75% / PF ~3.0 / DD -0.74%** sobre 5 pares. El edge sigue siendo válido pero menor que lo anunciado aquí.

**Alternativas consideradas:**
- Coexistir ambas estrategias en paralelo — añade complejidad de estado, riesgo de conflicto en pares compartidos (EURCHF/EURGBP/AUDNZD aparecen en ambas), no aporta beneficio claro dado que la nueva es mucho mejor
- Quedarnos con mean reversion + MLP — el PF 1.74 es marginal, requiere muchos trades para validar estadísticamente, y la diferencia con Asian Scalper es de orden de magnitud
- Seguir explorando una tercera estrategia antes de cambiar — válido pero ya tenemos un edge claro; iterar más sin necesidad gasta tiempo y la curva de aprendizaje en infraestructura (MT5, Telegram, riesgo) es lo que más importa para esta fase

**Por qué:**
1. Edge medible: el backtest sobre 2 años de datos M15 reales de MT5 muestra PF entre 2.0 y 8.0 en 5 de 6 pares, con drawdowns máximos < 1%.
2. Hipótesis del por qué funciona: la sesión asiática (21:00-02:00 GMT) es la ventana más tranquila del día forex — Londres ya cerró, New York está cerrando, Tokyo aún no abre con fuerza. Los precios tienden a moverse en rango y volver al centro. La estrategia explota explícitamente esa propiedad.
3. Toda la infraestructura existente se reutiliza sin cambios: `risk.py`, `executor.py`, `db.py`, `telegram_bot.py` son agnósticas a la estrategia.
4. El cambio es localizado: solo `strategy.py`, `main.py` (scheduler M15 + ventana horaria) y la config necesitan modificación significativa.

**Riesgos asumidos:**
- ~~USDJPY en el primer backtest dio PF 0.89 (perdedor) — Tokyo opera USDJPY activamente durante la sesión, lo cual rompe la hipótesis de "mercado tranquilo". La optimización validará si encontramos parámetros que lo salvan o si debe descartarse.~~ **Resuelto**: La optimización confirmó la sospecha. Incluso con sus parámetros óptimos individuales USDJPY apenas pasa el filtro (PF 1.83, 39 trades, Sharpe 0.85). Con parámetros universales se vuelve perdedor (PF 0.75). Decisión empírica: **descartado**. Pares finales: AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP.
- 16x más evaluaciones por noche (de H4 cada 4h → M15 cada 15min durante 5 horas). Más superficie para bugs y más carga sobre MT5. Mitigación: batch de fetches por tick, no secuencial por par.
- Holidays japoneses (Golden Week, Año Nuevo) hacen explotar los spreads. Para Phase 3 habrá que añadir filtro de calendario; por ahora el SL fijo protege.

**Parámetros finales (post-optimización, universales):**

```yaml
sl_atr_mult: 2.5         # antes default 1.5
adx_max_threshold: 35.0  # antes default 25
rsi_oversold: 35.0       # antes default 30
rsi_overbought: 65.0     # antes default 70
range_atr_min: 1.0
range_atr_max: 4.0       # antes default 3.0
pairs: [AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP]
```

Detalle completo en `docs/plans/daily-lull-scalper-live.md` y resultados raw en `backtest/results_lull_opt/`.

---

## D030 — Time-stop 02:00 registra `close_reason = 'session_close'`

**Decisión:** Añadir `'session_close'` como valor explícito del enum `trades.close_reason` y usarlo en el cierre forzado de las 02:00 UTC. Eliminar `'friday_close'` del enum.

**Por qué:** El time-stop de fin de sesión es un cierre conceptualmente distinto de SL/TP/manual; merece su propia categoría para reportes y estadísticas. El bug crítico de la auditoría era que `main.py` ya escribía `"session_close"` pero el `CHECK` no lo admitía → `IntegrityError` nocturno. `'friday_close'` pertenecía a la estrategia anterior (D026) y ya no tiene ruta que lo escriba bajo Asian Scalper (las sesiones de viernes se omiten y todo cierra a las 02:00).

**Alternativas:** reusar `'manual'` — rechazada por perder granularidad en el reporte semanal.

**Migración:** SQLite no permite `ALTER` de un `CHECK`; se hace rebuild guardado de la tabla `trades` (análogo a las migraciones de `signals`). La DB actual es demo/vacía, así que no hay riesgo de pérdida.

## D031 — Cierre de sesión bajo `_trade_lock` y aislado por posición

**Decisión:** El cierre forzado de las 02:00 se ejecuta dentro de `_trade_lock` y cada posición se procesa en su propio `try/except`.

**Por qué:** `_close_session_trades` corría sin el lock que sí toma el hilo de monitoreo (race en `order_send`/DB), y un fallo en la primera posición abortaba el lote dejando las demás abiertas en el broker (anulando el time-stop) y pausando el bot. La robustez del time-stop es una salvaguarda central de supervivencia.

## D032 — El reporte semanal se deriva de la config, no se hardcodea

**Decisión:** El instante de disparo del reporte se calcula desde `reports.weekly_report_day` + `weekly_report_hour` interpretados en `reports.timezone`, en lugar del "lunes 01:00 UTC" hardcodeado.

**Por qué:** Convención del proyecto "config nunca hardcodeada" (D017). Los campos existían y se documentaban pero el código los ignoraba. El comportamiento por defecto (domingo 20:00 UTC-5 ⇒ lunes 01:00 UTC) se preserva sin regresión.

## D033 — Paridad de parámetros backtest ⇄ vivo

**Decisión:** Los defaults de clase de `DailyLullStrategy` pasan a los valores optimizados de D029, y `run_lull_backtest`/`run_lull.py` aceptan y aplican los 6 parámetros desde `config.yaml`.

**Por qué:** Un run "plano" del backtest usaba `1.5/25/30·70/1-3` mientras vivo usa `2.5/35/35·65/1-4`. La validación debe correr con los mismos parámetros que producción para ser significativa. `config.yaml` es la fuente única de verdad.

## D034 — Etiqueta de cierre sin `trailing_stop` mientras el trailing esté desactivado

**Decisión:** En `_detect_closed_trades`, los cierres de motivo MT5 desconocido se registran como `'manual'`, no `'trailing_stop'`.

**Por qué:** `use_trailing_stop: false` en Asian Scalper; etiquetar como trailing un cierre que no lo es distorsiona reportes y estadísticas. SL/TP se siguen detectando por `DEAL_REASON`.

## D035 — Eliminar código y config legacy de la estrategia anterior

**Decisión:** Eliminar config parseada-pero-no-usada (`friday_close_hour_utc`, `take_profit_ratio`, `stop_loss_atr_multiplier`, `take_profit_mode`), funciones sin consumidores en runtime (`calculate_sl_tp`, `check_tp_hit`, `compute_ema/macd/rsi/adx/bollinger`) y campos legacy de `StrategyConfig` (EMA/MACD/genéricos), siempre que grep confirme que no tienen uso.

**Por qué:** Convención "sin código muerto". Son restos del trend-following y mean-reversion previos al Asian Scalper. `compute_atr` se conserva (lo usa `trailing.py`).

## D036 — `DEFAULT_PAIRS` coherente con D029

**Decisión:** `config.py:DEFAULT_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]`.

**Por qué:** El fallback listaba `[AUDCAD, NZDCAD, AUDNZD, EURCHF, EURGBP]`, divergente de los 5 pares finales de D029 y de `config.yaml`/`CLAUDE.md`. Aunque `config.yaml` siempre manda, un fallback divergente es una trampa.

## D037 — Scheduler alineado al tiempo del servidor MT5 (GMT+3)

**Decisión:** El scheduler (`main.py`) ahora razona en tiempo del servidor MT5, no en UTC real. El offset se deriva una sola vez al inicio usando `get_server_utc_offset()` en `drift/mt5_client.py` (compara el epoch del último tick contra UTC real y redondea al entero de horas más cercano). `server_now(offset)` genera el "now" de servidor que se pasa a `_in_session_window`, `_next_session_start`, `_next_m15_close` y `_seconds_until`.

**Bug que corrige:** El servidor ICMarkets usa hora anclada al cierre de NY (00:00 servidor = 17:00 NY). El scheduler anterior usaba `datetime.now(timezone.utc)` (UTC real) mientras que las velas de MT5 llevan timestamps en hora de servidor. Al comparar hora del scheduler con `bar_time.hour` en la estrategia, había un desajuste de +3 horas: cuando el scheduler creía que eran las 21:00 UTC, la vela leía las 00:00 servidor. Resultado: el rango nunca se bloqueaba y el bot nunca operaba.

**Las horas de sesión no cambian.** `session_start_hour=21`, `session_end_hour=2`, `trading_allowed` en (23, 0, 1) son todos tiempo de servidor — exactamente la convención del backtest y el edge validado. No se tocan los valores, solo se garantiza que el scheduler habla el mismo idioma que las velas.

**Reporte semanal no afectado.** `_check_weekly_report` sigue usando `datetime.now(timezone.utc)` (UTC real) porque su trigger está configurado en la zona horaria del usuario (UTC-5) y no tiene relación con la sesión de broker.

**Nota geográfica:** El docstring original y D029 describen la ventana 21:00-02:00 como "sesión asiática tranquila". El nombre es geográficamente incorrecto: en UTC real es 18:00-23:00 (verano) / 19:00-00:00 (invierno) = el *daily lull* entre el cierre de NY y la apertura de Tokio, no la sesión asiática de libro (la ventana **termina** cuando Tokio abre a las 00:00 UTC). La premisa sí es correcta — es el período más muerto del día, ideal para mean reversion — corroborado con fuentes externas. Ver [[D041]] para la identidad real de la sesión y la corrección del supuesto "sin DST". El edge fue validado fuera de muestra en esta ventana en tiempo de servidor; la lógica y los números son correctos.

---

## D038 — (FUTURO, no implementar aún) Pipeline multi-estrategia por sesión horaria

**Estado:** Idea anotada para implementación futura. **No tocar hasta que el bot actual (Asian Scalper en 1 sola ventana) lleve ≥2 meses estable en demo y luego en live** (D016). Esta nota existe solo para no perder la idea; no es un compromiso de diseño.

**Idea:** Generalizar Drift de "un bot con una estrategia y una ventana horaria fija" a "un orquestador que corre N estrategias, cada una activa en su propia ventana de sesión". Ejemplo del usuario:
- Madrugada (hora local) → estrategia tipo Asian Session (mean reversion en rango, la actual).
- Tarde (hora local) → estrategia tipo London Session (breakout / momentum de apertura europea).
- Y así sucesivamente (NY session, overlap London-NY, etc.).

**Por qué se anota y no se hace ya:**
1. Cada sesión tiene un régimen de mercado distinto → cada una necesita su propia estrategia *validada con su propio backtest* antes de ir a live. Hoy solo tenemos UNA estrategia validada (Asian Scalper, D029). Añadir London sin backtest sería exactamente el "operar a conveniencia" que queremos evitar.
2. Multiplica el estado y la superficie de bugs (varias `SessionState` por par × estrategia, varias ventanas, posible solape). La infraestructura de riesgo (`risk.py`, `max_open_trades`, D024) tendría que arbitrar entre estrategias que compiten por los mismos pares/cupos.
3. La lección de D037 (desalineación de zona horaria) se multiplica: cada ventana debe razonar en hora de servidor MT5, no en hora local ni GMT.

**Esbozo de diseño (cuando llegue el momento, re-litigar aquí):**
- Una interfaz `Strategy` común: `define_window()`, `evaluate_pair()`, `should_close()`, su propio bloque de params en `config.yaml` (p. ej. `strategies: { asian: {...}, london: {...} }`).
- El scheduler de `main.py` deja de hardcodear horas (21/22/23/0/1/2) y las deriva de la estrategia activa en cada tick → resuelve también el acoplamiento parcial detectado en la auditoría 2026-06-02 (ver más abajo).
- Un router que, dado el "server_now", decide qué estrategia(s) están en ventana y reparte el cupo de riesgo global.
- Cada estrategia nueva entra solo con: backtest out-of-sample propio + ≥2 meses demo, igual que la primera.

Relacionado: [[D023]] (híbrido con filtro de régimen — complementario: D023 elige estrategia por *régimen detectado*, D038 por *franja horaria*). Ambas pueden converger.

---

## D039 — Arquitectura de zonas horarias: tres capas (servidor / UTC / Bogotá)

**Decisión:** Separar explícitamente tres capas de tiempo y no mezclarlas nunca:

1. **CALCULAR sesiones → hora de servidor MT5 (GMT+2 invierno / GMT+3 verano, anclado al cierre NY; ver [[D041]]).** La ventana 21:00-02:00 y el lock del rango se evalúan contra los timestamps de las velas, que el servidor estampa en su hora local. El scheduler usa `server_now(offset)` (D037). Esta capa queda **intacta** — es la que hace que el rango se lockee correctamente y está cubierta por `tests/test_strategy.py`.
2. **GUARDAR en DB → UTC real, siempre (ISO 8601).** Es la base canónica: estable ante cambios de broker/offset/DST, hace comparables trades y señales, y desacopla "cuándo pasó" de "cómo se muestra".
3. **MOSTRAR al usuario → Bogotá (UTC-5), configurable vía `reports.timezone`.** El usuario nunca ve UTC ni hora-servidor.

**Bug que corrige:** El índice de velas (`mt5_client.py`, `pd.to_datetime(..., utc=True)` sobre el epoch del servidor) queda en hora-servidor **mal etiquetada como UTC**. Esos timestamps se persistían tal cual en `signals` (`m15_candle_time`, `h4_candle_time`, `analyzed_at`), mientras que `trades.opened_at/closed_at` usan `datetime.now(timezone.utc)` = **UTC real**. Resultado: dos columnas que dicen `+00:00` pero en bases distintas (3h de diferencia). Al pasar ambas por `format_time` (UTC→UTC-5), los trades mostraban la hora Bogotá correcta pero las señales aparecían +3h.

**Implementación (enfoque de borde, no de núcleo):** No se toca la lógica de sesión (evita re-romper D037 y reescribir ~200 asserts). Se convierte server→UTC **en el momento exacto de persistir** (`db.log_signal` recibe el `server_offset` y resta el offset antes de `isoformat()`; `executor` convierte el `time_open` derivado de `pos.time`). `formatting.py` lee la zona de `reports.timezone` y convierte UTC→Bogotá al mostrar. Regla: **server-time nunca se guarda ni se muestra crudo.**

**Datos demo previos:** las señales ya escritas en demo quedan en la base mixta antigua; no se migran (es data demo desechable). A partir de este cambio todo queda en UTC real.

Relacionado: [[D037]] (scheduler en hora servidor), [[D018]] (UTC interno — matizada aquí).

---

## D040 — Reintento de órdenes ante rechazos transitorios del broker (rollover 00:00)

**Decisión:** `open_trade` reintenta el `order_send` ante retcodes **transitorios** del broker, hasta `system.order_retry_attempts` veces (default 3), esperando `system.order_retry_delay_seconds` (default 25.0) entre intentos y releyendo precio fresco en cada uno. Retcodes transitorios: `10004` requote, `10018` market closed, `10021` price off, `10024` too many requests, `10031` no connection. Los retcodes fatales (stops inválidos, sin fondos, volumen inválido) fallan de inmediato sin reintentar.

**Bug que corrige (observado en demo 2026-06-02):** Una señal de compra VÁLIDA de EURJPY (todas las condiciones cumplidas) disparó a las **00:00:00 hora servidor** y la orden fue rechazada con `retcode=10018 "Market closed"`. Las 00:00 servidor es el **rollover diario de ICMarkets** (halt de ~1-2 min en la medianoche del servidor), y cae dentro de la ventana de entrada del Asian Scalper (23:00-01:59 servidor). Con un único `order_send` se perdía la entrada. 3 intentos × 25s cubren ~75s → el mercado reabre y la orden entra.

**Alternativas consideradas:**
- Saltar proactivamente la vela 00:00 — más simple pero nunca opera ese extremo y diverge del backtest.
- Solo logging limpio — no recupera el trade.
- **Reintentar (elegida)** — recupera la entrada, es general (cualquier halt breve), no hardcodea las 00:00.

**Trade-offs asumidos:** el reintento bloquea el tick M15 hasta ~75s por par que falla. Aceptable: la cadencia entre velas es 900s y el rollover ocurre como mucho una vez por sesión. Si se agotan los intentos, el rollback de `traded` (ya existente en `main.py`) permite que la vela 00:15 reintente. Backtest no modela el halt; esta es robustez de ejecución en vivo, no cambia la lógica de señales.

**Guardia de precio (anti-entrada-tardía):** un fill tardío podría entrar a un precio peor mientras el SL/TP siguen anclados al cierre de la señal, degradando el R:R. Para evitarlo, `open_trade` recibe `guard_boundary` (range_low en buy, range_high en sell) y **solo en los reintentos** (no en el primer intento, para no abandonar por el spread bid/ask) abandona la orden si el bid actual ya revirtió hacia dentro del rango (buy: bid > range_low; sell: bid < range_high). Las velas y la condición de entrada son bid-based, por eso la guardia compara contra el bid. Así solo se entra tarde si el extremo sigue válido; si revirtió, se abandona y la vela siguiente reevalúa.

Relacionado: memoria `broker-rollover-market-closed`.

---

## D041 — Identidad real de la sesión y comportamiento DST del servidor ICMarkets

**Contexto:** Auditoría 2026-06-02 (continuación de D037/D039). Se investigó en fuentes externas (no en datos guardados, que estaban en duda): (a) cuál es realmente la sesión "más tranquila" que el proyecto llama *Asian session*, y (b) si el servidor de ICMarkets aplica DST, porque D037/D039 afirmaban "GMT+3 fijo, sin DST".

**Hallazgo 1 — La sesión NO es la asiática; es el *daily lull* cierre-NY → pre-Tokio.**
La ventana 21:00-02:00 hora-servidor = **18:00-23:00 UTC (verano) / 19:00-00:00 UTC (invierno)**. El consenso de la industria ubica el período de menor liquidez/volatilidad del día forex justo ahí: el *daily lull* entre el cierre de Nueva York (~21:00 UTC verano / 22:00 UTC invierno) y la apertura de Tokio (00:00 UTC). Ranking de volatilidad de sesiones: **Sídney (más baja) < Tokio < Londres < Nueva York**. La ventana captura el cierre de NY + la apertura quieta de Sídney — más tranquila aún que Tokio. El nombre "Asian session" es geográficamente incorrecto (la ventana termina cuando Tokio abre), pero la **premisa es empíricamente correcta**: operar mean reversion en la franja más muerta del día. Nombre fiel: *NY-Close / Pre-Asia Lull Scalper*.
Fuentes: Maven Trading (sessions/volatility guide), BabyPips (forex market hours), PU Prime, Dukascopy.

**Hallazgo 2 — ICMarkets SÍ aplica DST. La afirmación "GMT+3 fijo" de D037/D039 era incorrecta.**
Documentación oficial de ICMarkets: el servidor es **GMT+2 en invierno (US standard) / GMT+3 en verano (US DST)**, anclado al cierre de NY (00:00 servidor = 17:00 NY todo el año). Cambia dos veces al año siguiendo el DST de EE.UU. (≈marzo y noviembre).
Fuente: blog oficial IC Markets ("US Daylight Savings & Server Time Changing to GMT+3").

**Por qué esto NO invalida el barrido ni el edge:**
- Como el reloj del servidor está anclado al cierre de NY, "21:00 servidor" es **siempre** el mismo momento de mercado (14:00 NY) en verano e invierno; el DST está horneado en los timestamps de las velas.
- El backtest lee esas etiquetas servidor → operó sobre una ventana consistente y correctamente anclada al *lull* durante los 2 años completos, sin importar el DST. El barrido nunca necesitó el offset UTC absoluto: solo compara etiquetas servidor.
- El bot en vivo razona en hora-servidor con el mismo offset derivado → paridad backtest⇄vivo intacta.
- Corolario: el DST del servidor es, para esta estrategia, una **ventaja** — mantiene la ventana centrada sobre el *lull* (anclado al cierre NY) todo el año. La preocupación previa ("la ventana se descentra ±1h en invierno") estaba al revés: ventana y *lull* respiran juntos.

**Defectos reales derivados del supuesto "sin DST" (NO afectan el edge; sí la robustez en vivo):**
1. **Offset derivado una sola vez al inicio** (`main.py`, "fixed for the entire session"). La derivación es dinámica (`get_server_utc_offset`) y correcta al arrancar, pero si el proceso corre de forma continua a través de un cambio DST (marzo/noviembre) sin reiniciar, el offset queda obsoleto 1h → mini-regresión de D037 (scheduler desincronizado de las velas) hasta el reinicio. Acotado a ~2 fines de semana al año.
2. **Fallback hardcodeado a `timedelta(hours=3)`** en `get_server_utc_offset` cuando no hay tick (mercado cerrado): asume verano; en invierno es +1h incorrecto. Bajo riesgo (el arranque suele ocurrir con mercado abierto y la derivación dinámica tiene prioridad), pero es un valor incorrecto medio año.

**Decisión:**
- Corregir las afirmaciones falsas "sin DST" en docs (D037, D039) y comentarios de código — no cambia comportamiento.
- Mantener la config `session_*` sin cambios (no romper el edge validado); el renombrado es conceptual/documental.
- Defectos 1 y 2: **implementados** el 2026-06-02 (commit `fix(timezones)`): re-derivación del offset al inicio de cada sesión en `main.py` + fallback DST-aware (`_us_dst_active`, 2º dom marzo → 1er dom noviembre) en `get_server_utc_offset`. Mitigación de respaldo: reiniciar el bot tras cada cambio de DST de EE.UU.

Relacionado: [[D037]], [[D039]], [[D029]].

---

## D042 — Renombrado: Asian Session Scalper → Daily Lull Scalper

**Decisión (2026-06-03):** Renombrar la estrategia de "Asian Session Scalper" a **"Daily Lull Scalper"** en todo el código, archivos, identificadores, config y documentación activa.

**Por qué:** El nombre "Asian Session" es geográficamente incorrecto (ver [[D041]]): la ventana 21:00-02:00 hora-servidor es el *daily lull* entre el cierre de NY y la apertura de Tokio (18:00-23:00 UTC verano / 19:00-00:00 invierno), no la sesión asiática — de hecho termina cuando Tokio abre y captura la apertura más quieta de Sídney. "Daily lull" es el término técnico de la industria para ese hueco de baja liquidez. El nombre viejo confundía el razonamiento del *porqué* funciona el edge.

**Mapeo de identificadores:**
- Clase: `AsianSessionStrategy` → `DailyLullStrategy` (en `backtest/lull_engine.py` y `drift/strategy.py`)
- Archivos: `asian_engine.py` → `lull_engine.py`, `run_asian.py` → `run_lull.py`, `optimize_asian.py` → `optimize_lull.py`
- Resultados: `backtest/results_asian/` → `results_lull/`, `results_asian_opt/` → `results_lull_opt/`
- Constante: `ASIAN_PAIRS` → `LULL_PAIRS`; funciones `prepare/load/save/download_asian*` → `*_lull*`
- Reason strings (DB/logs): `asian_scalper_buy`/`asian_scalper_sell` → `lull_scalper_buy`/`lull_scalper_sell`
- Doc: `docs/knowledge/asian-session-scalper.md` → `daily-lull-scalper.md`

**No afecta:** la lógica, los parámetros validados ([[D029]]), ni el schema de `config.yaml` (los nombres de campo nunca usaron "asian"). Verificado: 113 tests verdes y `ruff` limpio tras el rename.

**Registro histórico:** Las entradas previas del log (D029, D030, D035, etc.) conservan el nombre "Asian Session Scalper" porque documentan decisiones tomadas cuando ese era el nombre. Esta entrada (D042) es el punto de renombrado; no se reescribe el historial.

Relacionado: [[D041]], [[D029]].

---

## D043 — Reconciliación de métricas: las cifras de D029 estaban infladas

**Decisión (2026-06-03):** Reconciliar todas las métricas de backtest publicadas a un único snapshot reproducible, tras descubrir que las cifras de D029 (EURCHF PF 8.17, 277 trades) no se reproducen.

**Qué se encontró:** Había ≥3 snapshots de métricas incoherentes entre docs y JSON:
- **A — D029 + JSON commiteados:** EURCHF PF 8.17 / 46 trades, total 277. Generado con parámetros más restrictivos que los del bot (probablemente los defaults "planos" pre-[[D033]]: ADX 25, RSI 30/70, rango 1-3).
- **B — tabla del spec (`daily-lull-scalper.md`):** coincidía con los params universales reales en 4/5 pares.
- **C — re-run actual:** params universales de `config.yaml` (2.5/35/35·65/1-4) sobre data fresca.

Mismo período de datos pero ~3× los trades entre A y C → la diferencia era de **parámetros**, no de datos. Los JSON de `results_lull/` estaban stale (no coincidían con código+config+datos actuales); se regeneraron.

**Causa raíz:** las cifras "de venta" de D029 (PF hasta 8.17) salieron de un set de parámetros distinto al que el bot ejecuta. Los JSON nunca se regeneraron tras fijar los params universales ([[D033]]) y re-descargar datos.

**Métricas reproducibles (snapshot 2026-06-03; 6 pares re-descargados de MT5, params universales):**

| Par | Return | PF | Trades | Sharpe |
|---|---|---|---|---|
| AUDNZD¹ | +16.15% | 5.83 | 198 | 7.32 |
| EURCHF | +12.65% | 4.14 | 141 | 4.18 |
| GBPJPY | +5.63% | 2.68 | 137 | 2.96 |
| EURGBP | +4.89% | 2.77 | 139 | 3.00 |
| EURJPY | +4.43% | 2.07 | 148 | 1.91 |
| USDJPY (descartado) | -1.82% | 0.75 | 140 | -0.96 |

**Portfolio (5 pares vivos, $2500): +8.75% ($+218.77), PF medio ~3.0, peor DD -0.74%, 763 trades.** Reproducible: `python backtest/run_lull.py`.

¹ Limitación de datos: el broker (ICMarkets demo) solo tiene M15 de AUDNZD desde **2025-01-02** (~1.4 años); el resto cubre 2 años. No es corregible (límite del broker), documentado.

**Implicación:** el edge **sigue siendo positivo y robusto** (5 pares PF 2.07-5.83, DD < 1%, +8.75% en ~2 años), pero **es menor que lo que D029 anunció** (EURCHF "8.17" → 4.14 real). La decisión de D029 (elegir esta estrategia) sigue siendo válida; solo la magnitud estaba sobreestimada.

**Pendiente:** la validación out-of-sample citada en el spec ("PF 7.56 in-sample → 6.98 out-of-sample") también provino de un snapshot no reproducible — re-confirmar con un split in/out-of-sample limpio antes de ir a live.

Relacionado: [[D029]], [[D033]], [[D041]].
