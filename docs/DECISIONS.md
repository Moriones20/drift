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

**Validación out-of-sample (2026-06-03, `backtest/validate_oos.py`):** Split cronológico por par (IS 70% / OOS 30%, ~7 meses no vistos: oct-2025 → jun-2026). Dos modos:
- *Walk-forward* (riguroso): grid-search SOLO en IS, params congelados, evaluados en OOS nunca visto. **Resultado: 5/5 pares rentables OOS, retención media de PF 1.12 (3 de 5 mejoran en OOS), portfolio OOS +2.92%.** Los params óptimos de IS caen cerca de los universales (EURCHF idéntico: 2.5/35/35·65/1-4), lo que confirma que la elección de [[D029]] no es un artefacto del full-sample.
- *Frozen* (params de `config.yaml`): 5/5 rentables OOS, retención media 1.17, portfolio OOS +3.04%.

**Veredicto: el edge generaliza** — no hay colapso out-of-sample, que es el patrón que delataría overfitting. Reemplaza la cita previa no reproducible ("PF 7.56 → 6.98"). Caveats: (a) AUDNZD es el más débil — retención 0.46 por un IS PF inflado (ventana corta), aunque sigue rentable OOS (PF 3.72, +4.35%); (b) el retorno OOS anualiza ~5%, menor que el +8.75%/2a in-sample — realista, no eufórico.

Relacionado: [[D029]], [[D033]], [[D041]].

---

## D044 — Vela 00:00: esperar a que pase el rollover antes de ejecutar (refina D040)

**Decisión (2026-06-03):** La vela que cierra a las **00:00 hora servidor** se evalúa y ejecuta con un retraso configurable (`system.rollover_settle_seconds`, default 150s) en vez de los +5s normales. Las otras 10 velas de la ventana siguen a +5s.

**Bug que corrige (observado en vivo el 2026-06-03):** El reintento de [[D040]] recuperó una señal válida de EURCHF en el rollover, **pero llenó en el gap de reapertura**: señal a 0.91604, fill a 0.92026 (~42 pips arriba), TP (0.91894) quedó por debajo del fill → trade invertido, cerró por time-stop en **-$29.24**. Si hubiera entrado al precio de la señal habría sido **ganador** (el precio cerró en 0.918). La guardia de precio no lo evitó: durante la reapertura `symbol_info_tick` devolvió un quote viejo que pasó la guardia mientras `order_send` ejecutaba contra el precio real ya gapeado.

**Por qué este enfoque (y no más reintentos/guardia):** El bot evalúa sobre la **vela en formación** (`get_candles` usa `copy_rates_from_pos` pos 0). Al retrasar la evaluación de la vela 00:00 a ~00:02:30, la estrategia **re-evalúa sobre el precio fresco** ya con el mercado reabierto: si el precio gapeó y sigue fuera del extremo, la condición de entrada (`close ≤ range_low` en buy) **es falsa → no entra**; si volvió al extremo, entra limpio en mercado líquido. La propia estrategia hace de guardia, sin depender de quotes poco fiables durante el halt.

**Alternativas descartadas:** correr todo el reloj desfasado (:03/:18/...) — penaliza las 10 velas sanas con deriva innecesaria; saltar la vela 00:00 — pierde entradas legítimas (la de hoy habría sido ganadora). El reintento de [[D040]] se conserva como red de seguridad para rechazos transitorios fuera del rollover.

> **Ajuste (ver [[D045]]):** la idea de "re-evaluar a las 00:02:30" se subsume en D045 (evaluar velas cerradas). D044 **no queda obsoleta**: su valor real es **retrasar la ejecución del wake de las 00:00 fuera del halt**. Con D045 ese wake evalúa la vela 23:45 (ya cerrada); si dispara, la orden saldría a las 00:00:05 (en el halt) — el retraso de D044 la empuja a ~00:02:30, post-halt. D044 (ejecución) y D045 (señal) son complementarias.

Relacionado: [[D040]], [[D045]], memoria `broker-rollover-market-closed`.

---

## D045 — Evaluar velas M15 CERRADAS, no la vela en formación (fidelidad vivo↔backtest)

**Decisión (2026-06-03):** El bot vivo descarta la vela M15 en formación y evalúa solo la **última vela cerrada** (`drift.strategy.closed_bars` filtra `index < bar_close_time`, llamado en `_analyse_pair_m15`). Antes usaba `m15_df.iloc[-1]` = la vela en formación (`get_candles` → `copy_rates_from_pos` pos 0).

**Bug que corrige:** `Backtesting.py` decide cada señal sobre el **cierre de la vela completa**; el bot vivo decidía sobre la vela **en formación a los +5s** (≈ su apertura). Es un desfase sistemático de ~1 vela en TODAS las velas, y en las 00:00 servidor caía sobre el **wick del rollover** → disparaba entradas que el backtest nunca tomó y con fills gapeados. Era la causa raíz de "el vivo no reproduce el backtest".

**Cómo se descubrió:** El análisis de hora de entrada del backtest (`backtest/analyze_entry_hours.py`) mostró 92% del PnL en la hora 00 y 88% win en la vela 00:00 exacta — pero el backtest evalúa esa vela en su **cierre (00:15, post-rollover, limpio)**, mientras el vivo la evaluaba en formación a las 00:00:05 (durante el halt). `analyze_exclude_rollover.py` confirmó que el edge sobrevive sin la vela 00:00 (+6.41%, PF 1.8-5.7), o sea es real, no un artefacto — el problema era de **ejecución/fidelidad**, no de estrategia.

**Efecto del cambio:** Con velas cerradas, la vela 00:00 se evalúa en el wake de las **00:15:05** (ya cerrada, precio limpio) y se ejecuta en mercado abierto — reproduciendo fielmente el backtest. Trazado bar-a-bar: el rango se define con bars 21:00-22:45 (wakes 21:15-23:00), se lockea con bar 23:00 (wake 23:15), y se opera bars 23:00→01:30. Única divergencia: se pierde la entrada de la bar 01:45 (en el backtest aporta +0.49 sobre 2 años — despreciable). El scheduler no requiere cambios: la lógica de sesión de la estrategia se basa en la hora del **índice** de la vela.

**Complementa:** [[D044]] (retrasa la ejecución del wake 00:00 fuera del halt) y [[D040]] (reintento/guardia como red de seguridad general). Los tres juntos: señal sobre vela cerrada (D045) + ejecución post-halt (D044) + retry de respaldo (D040).

Relacionado: [[D044]], [[D040]], [[D043]] (reconciliación de métricas), `backtest/analyze_entry_hours.py`, `backtest/analyze_exclude_rollover.py`.

## D046 — Guardia de reward-tras-spread: no entrar si el spread se come el edge

**Decisión (2026-06-04):** `executor.open_trade` abandona la entrada (en cualquier intento, antes de mandar la orden) si, tras pagar el spread vivo, sobrevive menos de `min_reward_fraction` (default **0.5**) del reward buscado. Reward buscado = `|tp - entry_reference|` (la señal, basada en bid/cierre); reward tras spread = `tp - ask` (compra) o `bid - tp` (venta). Config: `system.min_reward_fraction`. `0.0` desactiva la guardia (backward-compat).

**Bug que corrige:** El primer día con D044+D045 activos (sesión 2026-06-04) las 3 entradas se evaluaron bien sobre velas cerradas, pero **los 3 fills gapearon por encima de su propio TP** y las 3 cerraron en pérdida (−$12.37 EURJPY, −$8.89 GBPJPY *por `take_profit`*, −$4.85 EURGBP; total −$26.11). Causa: la señal/TP/SL se calculan sobre el **cierre (bid)**, pero una orden a mercado llena al **ask** (compra). A las 00:15 servidor, 15 min tras el rollover, los spreads de cruces JPY siguen inflados (10-20 pips), y como los TP de mean reversion son diminutos (~6 pips), **el spread solo deja el fill pasado el TP** → un "take profit" que es pérdida.

**Por qué D044/D045 no bastaban:** D045 arregla la *señal* (vela cerrada); D044 saca la *ejecución* del halt. Pero ninguno mira el **spread** en el momento del fill. El backtest llena al **open de la vela siguiente (precio único, sin spread)**, así que nunca ve este costo — destapa que parte del edge "92% en la hora 00" podía estar inflado por fills sin spread justo cuando el spread real es máximo.

**Efecto:** Con `min_reward_fraction=0.5`, las 3 entradas de 2026-06-04 se habrían rechazado (reward tras spread negativo en las tres). Filtra entradas estructuralmente perdedoras sin tocar las sanas.

**Revalidación del edge con spread (2026-06-08, `backtest/analyze_spread_cost.py`):** re-tasado bajo spread realista (normal + inflado en rollover, anclado a los gaps vivos del 4-jun), el edge cae de **+8.75% bruto** a **+5.11% (modelo central: solo exceso de rollover)** y **+3.10% (pesimista: spread completo en todo)** sobre ~1.4-2 años. El **47% de los trades** caen en la ventana rollover (00:00-00:29). Conclusión: el edge es real pero ~la mitad de lo anunciado — expectativa go-live **low-single-digit anual**, no ~9%. D046 no está en ese modelo y debería empujar el realizado hacia arriba (rechaza justo las peores entradas rollover).

**Complementa:** [[D045]] (señal sobre vela cerrada) + [[D044]] (ejecución post-halt) + [[D040]] (retry/guardia de reversión). D046 es la cuarta capa: aunque señal y timing sean correctos, no se entra si el spread mata el R:R.

Relacionado: [[D045]], [[D044]], [[D040]], `drift/executor.py`, `tests/test_executor.py` (TestRewardAfterSpreadGuard).

## D047 — Rechazar offset de servidor implausible con mercado cerrado (tick viejo)

**Decisión (2026-06-07):** `get_server_utc_offset` rechaza un offset derivado si `|raw_offset_seconds| > 14h` y cae al fallback DST-aware (`_us_dst_active` → UTC+2/+3). Antes el fallback solo se activaba si `tick is None`.

**Bug que corrige:** Al reiniciar el bot un **domingo con el mercado cerrado**, `symbol_info_tick` devuelve el **último tick del viernes** (epoch viejo, no `None`). Comparándolo con la hora real daba `raw=-151086s` → **offset UTC-42**, que se aceptaba como válido. Consecuencia: el scheduler creía que era viernes, programó un sleep de ~45h y habría operado con la hora de sesión corrida. Detectado en el reinicio para cargar D046: log `MT5 server offset derived: UTC-42 (raw=-151086.1s)` y `sleeping until ... (162296s)`.

**Por qué el fallback previo no bastaba:** D041 solo contemplaba `tick is None`. Un tick **viejo pero presente** (fin de semana / feriado) pasa ese chequeo y produce un número plausible-en-tipo pero absurdo-en-valor. Los offsets reales de broker están dentro de ±14h (máximo mundial +14); cualquier cosa mayor implica tick rancio.

**Efecto:** Reiniciar el bot en cualquier momento (incluido fin de semana) deriva un offset correcto: con tick fresco usa el real; con tick viejo o ausente usa el fallback DST-aware (UTC+3 en verano US, UTC+2 en invierno). El offset se re-deriva igual al inicio de cada sesión (D041), cuando ya hay ticks frescos.

**Complementa:** [[D041]] (derivación DST-aware del offset; D047 endurece su fallback).

Relacionado: [[D041]], [[D039]], `drift/mt5_client.py`, `tests/test_timezones.py` (TestServerOffsetStaleTickGuard).

## D048 — `balance_at_close` debe leer el balance liquidado, no un snapshot previo

**Decisión (2026-06-08):** Al cerrar un trade, `balance_at_close` se lee **fresco de MT5** (`get_balance()` → `account_info().balance`) en el momento del cierre, no de una variable `balance` capturada antes. Aplica a los dos sitios de cierre en `main.py`: cierre de sesión (`_close_session_trades`) y reconciliación SL/TP/manual (`_detect_closed_trades`). Se eliminó el parámetro `balance` de ambas funciones (quedó muerto).

**Bug que corrige:** Las dos funciones recibían `balance` como un snapshot tomado al inicio del tick/sesión y lo persistían tal cual. Ese valor **no incluye el P&L ni la comisión del propio trade que se cierra**, y cuando varios trades cierran casi a la vez todos guardaban el mismo valor rezagado. Ejemplo real: tras la sesión del 4-jun los 3 trades guardaron `balance_at_close=1958.25`, pero el balance liquidado real era ~1939.59. Eso me llevó a un diagnóstico equivocado (atribuir −$18.66 a "sesiones del 5/6-jun" que en realidad nunca ocurrieron — viernes/sábado se saltan por diseño). El `profit_loss` por trade siempre fue correcto; solo `balance_at_close` mentía.

**Efecto:** `balance_at_close` ahora refleja el balance real de la cuenta tras cada cierre, así los reportes y la reconstrucción de balance cuadran con MT5. Sin red de seguridad perdida: si `get_balance()` falla, en el cierre de sesión la excepción se aísla por posición (D031) y la reconciliación del hilo de monitoreo la recupera en el siguiente ciclo.

Relacionado: [[D031]] (aislamiento por posición en el cierre), `drift/db.py` (`close_trade_record`), `main.py`, `tests/test_session_fixes.py` (test_balance_at_close_is_settled_balance).

## D049 — Arranque de Telegram resiliente: un fallo de red transitorio no debe matar el bot

**Decisión (2026-06-08):** El setup del bot de Telegram (`setup_bot` + `bot_app.initialize()`) se hace vía `_setup_telegram_resilient`, que reintenta ante cualquier excepción con backoff escalonado (`_TELEGRAM_SETUP_MAX_ATTEMPTS=10`, base 15s, tope 60s ≈ ~7 min de tolerancia) y solo re-lanza (fatal) tras agotar los intentos. Antes el setup era de un solo tiro y cualquier error subía sin manejo.

**Bug que corrige:** Al reiniciar para cargar D048, un fallo DNS transitorio (`httpx.ConnectError: [Errno 11001] getaddrinfo failed`, el resolver del router caído) en el arranque de Telegram **mató el proceso entero** — aunque operar no necesita Telegram en ese instante. Peor: el handler FATAL decía "NSSM should restart" pero el bot corre por **Task Scheduler**, que no estaba configurado para reiniciar ante fallo → el bot quedó **caído** sin relevantarse. Telegram es el plano de control/notificación, no una dependencia de trading: esperar a que la red vuelva es lo correcto para un bot autónomo 24/5.

**Efecto:** Un blip de red en el arranque ya no tumba el bot; espera y reintenta. Corregidas también las referencias obsoletas a "NSSM" en el handler FATAL → ahora apuntan a Task Scheduler. **Pendiente de entorno (no código):** configurar la tarea "Drift" para reiniciar ante fallo (`Set-ScheduledTask` con RestartCount/RestartInterval), como segunda capa por si el proceso muere por algo fuera de la ventana de reintentos.

**Origen:** el resolver DNS del router (192.168.80.1) caído el 8-jun; el ruteo por IP funcionaba (ping a 8.8.8.8 OK) pero ninguna resolución de nombres. Problema de red del usuario, pero destapó la fragilidad del arranque.

Relacionado: `main.py` (`_setup_telegram_resilient`), `tests/test_startup_resilience.py`, [[D047]] (otra robustez de arranque ante condiciones de red/mercado).

## D050 — Pivote a plataforma multi-estrategia (framework primero, antes del go-live)

**Decisión (2026-06-08, sesión `/spec`):** Drift evoluciona de bot mono-estrategia a **plataforma que hospeda N estrategias** sobre una sola cuenta MT5, con el Daily Lull como instancia #1. El framework se construye **antes** del go-live (Phase 3), en paper, y el Lull sale live ya sobre la arquitectura multi-estrategia. Esta sesión es **solo diseño**; produce docs, no código.

**Por qué framework primero:** El Lull aún está en **paper/demo, no live** (no hay capital real sobre su lógica). El costo de construir el framework es fijo y es lo más barato que será nunca: una sola estrategia que portar, sin downside live (el peor bug es un trade demo distinto). Sacar el Lull live mono-estrategia y refactorizar después implicaría meter cambios estructurales en un sistema con capital real corriendo. El ROADMAP ya listaba "Multi-estrategia" en *Futuro*; esto ejecuta esa rama antes de lo previsto porque el momento (pre-live) es óptimo.

**Alternativa descartada:** Go-live del Lull primero, refactor después. Rechazada por el riesgo de refactorizar un sistema live y por desperdiciar la ventana barata del paper.

**Pitch actualizado:** de *"bot que opera el Daily Lull"* a *"plataforma de trading autónoma que hospeda N estrategias con riesgo aislado por estrategia sobre una cuenta"*.

Relacionado: [[D051]], [[D052]], [[D053]], [[D054]], [[D055]], [[D056]], [[D057]], `ROADMAP.md` (Phase 2.6).

## D051 — Contrato `Strategy.on_bar` + motor "reloj por suscripción" (timing heterogéneo)

**Decisión:** El motor (`main.py` reescrito) NO conoce ventanas de sesión. Solo conoce cierres de vela. Cada estrategia **declara los timeframes que quiere** (`timeframes: frozenset[str]`, p.ej. `{"M15"}`) y el motor hace tick en la **unión** de esos límites. En cada tick llama a `strategy.on_bar(pair, timeframe, bar_close_time, market, ctx) -> Decision`. La estrategia decide internamente (según su propia lógica de ventana/estado) qué hacer; devuelve `Open(signal) | Close(ticket) | CloseAll(reason) | NoOp`.

**Reparto motor/estrategia:**
- **Motor (compartido, una vez):** reloj y sleep stop-aware; `MarketData` (fetch de velas dedup por (par, timeframe) por tick — si dos estrategias piden el mismo par/TF, se baja una vez); gating de riesgo (global + por estrategia); ejecución, atribución por magic, logging, notificación, detección de cierres; threads de monitoreo y Telegram.
- **Estrategia (privado):** su estado per-par (el `SessionState` del Lull deja de vivir en `main.py`); su lógica de ventana/timing; sus params; sus reglas especiales (skip-Friday, time-stop a las 02:00, espera de rollover) pasan a ser **lógica interna del Lull**, no del motor.

**Por qué:** HC3 — el timing es heterogéneo (una trend-following futura sería 24/5 en H1, sin ventana). Un motor que asuma la ventana del Lull no escala. "Motor tonto, estrategia lista" mantiene el timing donde pertenece.

**Alternativas descartadas:** (a) Motor con "ventanas de sesión" como concepto de primera clase — acopla el motor a un modelo de timing concreto. (b) Un thread independiente por estrategia — da aislamiento pero, con una cuenta y un brake de riesgo compartido (HC2), los threads pelean por el lock de riesgo/DB; la concurrencia se vuelve el problema dominante. Un solo reloj con estrategias como objetos es más simple y suficiente.

Relacionado: [[D050]], [[D055]] (el mismo `on_bar` es la fuente de verdad del backtest), `drift/strategies/base.py`.

## D052 — Magic number base + offset por estrategia

**Decisión:** `system.magic_number` (234000) pasa a ser la **base**. Cada estrategia define `magic_offset` en su config; el magic efectivo = base + offset. `get_open_positions(magic)` filtra por magic exacto → atribución de posiciones a nivel del **broker** (fuente de verdad cuando el bot reinicia o la DB se desincroniza). `_validate` rechaza dos estrategias con el mismo magic efectivo.

**Migración:** El Daily Lull usa `magic_offset: 0` → magic efectivo **234000**, idéntico a hoy, así adopta sus posiciones demo vivas sin huérfanos. La convención informal "magic 0 = orden manual" no aplica aquí porque Drift nunca abrió trades manuales con magic 0; lo que importa es unicidad frente a otros EA.

**Por qué magic Y columna DB (D056), no solo uno:** la columna DB es para reporting legible; el magic es para control operativo (detección de cierres SL/TP, force-close de sesión, reconstrucción tras reinicio). MT5 es la fuente de verdad de posiciones abiertas; el magic es lo único que reconstruye "de quién es esta posición" si la DB falla.

**Alternativa descartada:** magics totalmente independientes por estrategia (sin base común) — más libre pero pierde el prefijo `2340xx` que identifica a Drift de un vistazo.

Relacionado: [[D050]], [[D056]], `drift/config.py`, `drift/executor.py`.

## D053 — Riesgo en dos niveles: presupuesto notional por estrategia + brake global

**Decisión:** Sobre una sola cuenta, la "asignación de capital" es **notional** (no reserva ni mueve dinero real; solo cambia el número sobre el que cada estrategia dimensiona). Position sizing:

```
capital_asignado(estrategia) = balance_cuenta * (allocation_pct / 100)
risk_usd(trade)              = capital_asignado * (percent_per_trade / 100)
```

`percent_per_trade` pasa de global a **por estrategia**. Límites en dos niveles:

| Límite | Nivel |
|---|---|
| `max_open_trades` | ambos (cap global de cuenta + cap por estrategia ≤ global) |
| `max_drawdown_percent` | ambos (brake global = kill switch que pausa TODO; brake por estrategia = pausa solo esa) |
| `max_same_currency_direction` (correlación) | **global** (la correlación es riesgo de cuenta sin importar quién abrió) |

- **Suma de allocations ≤ 100%**, validado en `_validate`. Permite < 100% (colchón) pero rechaza > 100% (apalancamiento accidental, contra el principio "cabeza fría").
- **Pausa = mantener posiciones (opción a) en AMBOS niveles.** Una estrategia (o la cuenta) pausada deja de abrir nuevos pero mantiene los abiertos, protegidos por su SL/TP en servidor. Consistente con el `/pause` y el brake actuales. Cerrar a mercado al pausar materializaría la pérdida en el peor momento (especialmente perverso para mean reversion, donde el trade está en pérdida justo cuando más probable es que revierta). Si más adelante se quiere un "cierre de emergencia", se añade como decisión aparte.
- **Drawdown por estrategia** requiere su curva de equity individual: `capital_asignado_baseline + P&L_realizado(estrategia, DB) + P&L_flotante(estrategia, posiciones por su magic)`, con peak por estrategia persistido en `strategy_state` (D056). El thread de monitoreo lo calcula cada 30s (ya tiene las posiciones en mano).

Relacionado: [[D050]], [[D056]], `drift/risk.py`. HC2.

## D054 — Esquema de config: lista `strategies[]` anidada, migración manual

**Decisión:** `strategy:` (bloque plano singular) → `strategies:` (mapa de instancias por nombre). Lo compartido (broker, telegram, reports) se queda arriba; el riesgo global se renombra a `risk_global:`; `pairs` deja de ser global y baja a cada estrategia (la unión de pares activos es lo que se activa en Market Watch). Cada instancia:

```yaml
strategies:
  daily_lull:
    enabled: true
    magic_offset: 0
    allocation_pct: 100
    pairs: [AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP]
    risk: { percent_per_trade: 1.0, max_open_trades: 4, max_drawdown_percent: 10.0 }
    params: { rsi_oversold: 35.0, ... }   # = el StrategyConfig actual, opaco para el motor
```

- **`params` es opaco para el motor.** Cada estrategia define su propio dataclass (el `StrategyConfig` actual → `DailyLullParams`) y parsea su dict `params`. El motor solo conoce `enabled`, `magic_offset`, `allocation_pct`, `pairs`, `risk`. Una estrategia futura mete sus propios params sin tocar config global.
- **Registry** `{"daily_lull": DailyLullStrategy}` mapea nombre → clase; nombre desconocido = error de validación.
- **`enabled: false`** desactiva sin borrar (A/B, apagado vía config + restart).
- **Migración manual** del `config.yaml` live (no loader retrocompat). Es un archivo que se edita a mano una vez; soportar ambos esquemas sería deuda permanente para un evento único.
- **Anidado** (sub-bloques `risk`/`params`) en vez de aplanado — separa "qué arriesga" de "cómo opera" y escala mejor.

Relacionado: [[D050]], [[D052]], [[D053]], `drift/config.py`, `config.example.yaml`.

## D055 — Backtest unificado: `on_bar` única fuente de verdad; portfolio backtest diferido

**Decisión:** El contrato `on_bar` (D051) es la **única** implementación de la lógica de señal. Live y backtest la consumen:
- **Live:** el motor llama `on_bar` por cada cierre real.
- **Backtest:** un **adaptador propio** (loop simple sobre el histórico) recorre las barras y llama al *mismo* `on_bar` con `MarketData`/`ctx` que sirven datos históricos. `backtest/lull_engine.py` (hoy una **segunda implementación portada a mano**, con riesgo de divergencia) se reemplaza. Se abandona Backtesting.py; las métricas (PF, Sharpe, DD) se calculan sobre la curva de equity resultante.
- **Tests de equivalencia:** mismas señales sobre el mismo histórico antes/después del port (baratos aunque no haya capital, HC4).
- **Portfolio backtest** (varias estrategias sobre una curva de equity compartida, con cap global de trades + correlación + drawdown de cuenta) se **difiere** a cuando exista la estrategia #2. Construirlo ahora sería infraestructura sin nada que probar.

**Por qué unificar ya:** con HC4 (paper, sin capital real) es el momento más barato; cada estrategia futura sobre una base duplicada multiplica la deuda. Sin downside live.

**Alternativa descartada:** dejar la duplicación actual y unificar después — perpetúa el smell y arriesga que el bot live opere distinto de lo validado.

Relacionado: [[D050]], [[D051]], `backtest/`, `drift/strategies/`.

## D056 — Atribución en DB: columna `strategy` + tabla `strategy_state`

**Decisión:**
- `trades` y `signals` ganan `strategy TEXT NOT NULL` (el `name` legible, no el magic). Índice en `trades(strategy)`. Migración: `ALTER TABLE ... ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull'` (todo lo histórico es Lull).
- `bot_events` gana `strategy TEXT NULL` (NULL = evento global de cuenta; no-NULL = evento de una estrategia, p.ej. su pausa por drawdown propio).
- Nueva tabla `strategy_state(strategy PK, peak_equity, paused, updated_at)` para el drawdown por estrategia (D053).

```sql
CREATE TABLE strategy_state (
    strategy    TEXT PRIMARY KEY,
    peak_equity REAL NOT NULL,
    paused      INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
```

Relacionado: [[D050]], [[D052]], [[D053]], `drift/db.py`, `docs/ARCHITECTURE.md` (schema).

## D057 — Telegram: comandos global + por estrategia

**Decisión:** El plano de control desglosa por estrategia:
- `/status`, `/balance`, `/trades`, `/report` muestran el agregado de cuenta **y** el desglose por estrategia (P&L, drawdown, trades abiertos, estado pausado).
- `/pause` y `/resume` aceptan argumento opcional: sin args = toda la cuenta; con nombre (`/pause daily_lull`) = solo esa estrategia.
- Nuevo `/strategies` — lista cada estrategia con su estado (enabled, paused, allocation, magic, trades abiertos, drawdown).
- `/stop` sigue siendo global (apaga el bot entero).
- Reporte semanal incluye sección por estrategia.

**Por qué:** con presupuesto y drawdown por estrategia (D053), el usuario necesita ver y controlar cada una por separado, no solo el agregado.

Relacionado: [[D050]], [[D053]], `drift/telegram_bot.py`, `docs/user/commands.md`.

## D058 — Scheduling del motor por `next_wake` + extensiones del contrato

**Decisión (2026-06-08, Step 32a):** El motor (Step 32b) no programa el sleep a partir de ventanas de sesión; le **pregunta a cada estrategia su próximo despertar** vía un nuevo método del contrato `next_wake(now) -> datetime | None`. El motor duerme hasta el **mínimo** de los `next_wake` de las estrategias activas y luego aplica el delay de broker. Refina [[D051]].

**(a) `next_wake` devuelve un boundary limpio; el delay de broker vive en el motor.** `next_wake` devuelve el **próximo instante de cierre de vela** (en hora servidor MT5) que la estrategia necesita evaluar, o `None` si está dormida indefinidamente. **No** incluye ningún retraso. El motor, tras despertar al boundary mínimo, le suma el delay post-cierre de broker: el `CANDLE_CLOSE_DELAY` normal (5s) y el **rollover-settle de [[D044]]** para el boundary 00:00. El delay es comportamiento del broker (rollover diario de ICMarkets, aplica a cualquier estrategia que despierte a las 00:00), no de una estrategia concreta, así que pertenece al motor. Mantenerlo fuera de `next_wake` evita que cada estrategia futura tenga que re-implementar la lógica de rollover.

**(b) La ventana de sesión y el skip-Friday/Saturday van DENTRO de la estrategia.** La elección entre "próximo cierre M15" (dentro de la ventana) y "próximo inicio de sesión" (fuera), el cálculo del inicio de sesión saltando viernes/sábado, y el tail 00:00-01:59 son **lógica específica del Lull** (D051: motor tonto, estrategia lista). `DailyLullStrategy.next_wake` porta 1:1 las funciones que hoy viven en `main.py` (`_in_session_window`, `_next_m15_close`, `_next_session_start`) usando **solo sus propios params** (`session_start_hour`/`session_end_hour`), nunca config global. La equivalencia con la lógica actual del loop está blindada por `tests/test_daily_lull_next_wake.py` (batería amplia de timestamps: dentro/fuera de ventana, viernes, sábado, domingo 20:00/21:00, tail 00:30/01:45, borde 02:00, jueves 23:50).

**(c) Extensiones del contrato `Strategy`.** El port a la plataforma añadió al Protocol, todas refinando [[D051]]:
- `on_fill(pair, signal, ticket)` y `on_order_rejected(pair, signal, reason)` (Step 29) — hooks de ciclo de vida tras la decisión de abrir: la estrategia confirma o revierte estado (el Lull marca `traded` solo en `on_fill`) sin que el motor toque su estado privado.
- `next_wake(now)` (Step 32a, esta decisión) — la pista de scheduling descrita arriba.

**Alternativas descartadas:** (a) meter el delay de broker dentro de `next_wake` — duplicaría la lógica de rollover en cada estrategia y acoplaría la estrategia al broker; (b) dejar el scheduling en el motor con "ventanas de sesión" de primera clase — es justo lo que D051 rechaza (no escala a una trend-following 24/5 sin ventana).

Relacionado: [[D051]], [[D044]], `drift/strategies/base.py`, `drift/strategies/daily_lull.py`, `tests/test_daily_lull_next_wake.py`.

## D059 — Motor de backtest propio sobre `on_bar`; warm-up profundo para equivalencia con `lull_engine`

**Decisión (2026-06-08, Step 35):** Se construye `backtest/engine.py`, un motor de backtest propio (loop simple, **sin Backtesting.py**) que consume el **mismo `DailyLullStrategy.on_bar`** que el bot vivo (cumple [[D055]]). `backtest/run_lull.py` y `backtest/optimize_lull.py` se repuntan a este motor. `backtest/lull_engine.py` **no se elimina** (los scripts de research `analyze_*.py`/`validate_oos.py` lo siguen usando; su migración queda diferida).

**Reparto decisión/fill.** El `on_bar` es la única fuente de la *lógica de señal* (cuándo entrar, SL, TP=midpoint del rango, time-stop a las 02:00). El *modelo de fills* (cómo una decisión se vuelve trade) es responsabilidad del **motor**, y replica EXACTAMENTE el de Backtesting.py tal como lo usa `lull_engine`:
- Entrada a mercado y salidas (cruce de midpoint, time-stop) llenan al **open de la barra siguiente**.
- El SL es un stop de broker intrabar (chequeado también en la barra de entrada, como hace Backtesting.py al reprocesar el SL recién adjuntado); un gap a través del stop llena al peor de open/stop.
- Comisión relativa `abs(size)*price*commission` cobrada en entrada **y** salida; sizing all-in en unidades enteras.
- La curva de equity se registra por barra (`cash + P&L flotante` al cierre), como `_Broker.next`. Las métricas (return, win rate, profit factor, max drawdown, Sharpe) se calculan en `backtest/_results.compute_metrics` replicando las fórmulas de `compute_stats` de Backtesting.py (incluido el `geometric_mean` que rellena NaN con 0).

**OJO — el TP NO se modela como en el vivo.** El executor vivo manda un **TP de broker** (orden límite intrabar); `lull_engine` lo modela como `position.close()` cuando el cierre cruza el midpoint (fill al open siguiente). Para que la equivalencia con `lull_engine` se cumpla, el motor reproduce el **midpoint-close-al-open-siguiente**, NO un TP de broker intrabar. Es una decisión deliberada: el objetivo de Step 35 es equivalencia con el backtest existente, no con el fill vivo. (Que el backtest llene sin spread mientras el vivo paga spread/TP de broker es exactamente lo que [[D046]] mide y filtra; no es regresión.)

**Divergencia encontrada y resuelta — warm-up de indicadores.** El `on_bar` vivo pide una ventana **acotada** (`_H4_COUNT=50` barras H4); el ADX (doblemente suavizado, DI→DX) **no converge** en 50 barras, mientras `lull_engine` lo computa sobre la **serie completa**. Con 50 barras el ADX salía inflado (ej. 39.1 vs 27.1 real) y rechazaba ~60 entradas en la hora de rollover → 82 trades vs 141. **Solución:** el motor sirve a `on_bar` una ventana **profunda pero acotada** (`DEFAULT_WARMUP_BARS = {H4: 320, M15: 250}`) vía el `MarketData` falso; a 320 barras H4 el ADX coincide con la serie completa a ~1e-6 (y las decisiones de entrada son bit-exactas, porque 1e-6 nunca cruza el umbral ADX<35). Ventanas acotadas mantienen el loop O(n). Con esto la equivalencia es **exacta a precisión de float**: trades, return, profit factor, win rate, max drawdown idénticos; Sharpe a ~1e-9. Guardado por `tests/test_backtest_engine.py` (dataset sintético determinista que ejerce buy/sell, SL, TP y time-stop; más un test de datos reales opt-in vía `DRIFT_RUN_SLOW_BACKTEST=1`).

**Costo conocido — optimización lenta.** Como cada combo del grid de `optimize_lull.py` arranca el `on_bar` vivo sobre todo el histórico (~55 s/par con el atajo de saltar horas fuera de sesión), el grid-search es mucho más lento que el optimizador en C de Backtesting.py. Es la consecuencia directa de [[D055]] (una sola implementación de la señal). Aceptado: `optimize_lull` mantiene su interfaz/salida pero advierte del costo; el usuario puede reducir el grid o correrlo de noche.

**Diferido (follow-up):** migrar `analyze_entry_hours.py`, `analyze_exclude_rollover.py`, `analyze_spread_cost.py` y `validate_oos.py` al motor nuevo y entonces eliminar `lull_engine.py`.

Relacionado: [[D055]], [[D045]], [[D046]], [[D051]], `backtest/engine.py`, `backtest/_results.py`, `backtest/run_lull.py`, `backtest/optimize_lull.py`, `tests/test_backtest_engine.py`.

## D060 — Alinear el warm-up H4 del bot vivo con el backtest (ADX convergido)

**Decisión (2026-06-09):** Subir `_H4_COUNT` en `drift/strategies/daily_lull.py` de **50 → 320** barras (`_M15_COUNT` se mantiene en 150). Así la `on_bar` viva computa el ADX(14) H4 **convergido**, idéntico al que valida el backtest.

**Qué corrige.** [[D059]] cuantificó que el ADX es doblemente suavizado y con solo 50 barras H4 no converge: leía ~**39** cuando el valor real (serie completa) era ~**27**. Como el filtro de régimen rechaza si ADX ≥ `adx_max_threshold` (35), el bot vivo **rechazaba ~60 entradas de la hora del rollover que el backtest acepta** (39 ≥ 35 rechaza; 27 < 35 aceptaría). Resultado: el bot en paper operaba **más conservador que el edge validado** — no perdía dinero, pero no reproducía la estrategia que se backtesteó. D059 resolvió esto solo del lado backtest (su `MarketData` falso sirve `DEFAULT_WARMUP_BARS = {H4: 320, M15: 250}`); esta decisión cierra el lado **vivo**.

**Por qué 320.** A 320 barras H4 el ADX coincide con la serie completa a ~1e-6, margen que nunca cruza el umbral de 35, así que las decisiones de entrada del vivo igualan a las del backtest. Es el mismo número que `DEFAULT_WARMUP_BARS["H4"]` en `backtest/engine.py` (se mantienen como constantes paralelas con comentario cruzado; `drift/` no importa de `backtest/` para no invertir la dependencia).

**Es un cambio de comportamiento (deliberado, con datos).** Subir el warm-up hace que el vivo **dispare más entradas** (las de la hora rollover que antes rechazaba por ADX inflado). La guardia de spread [[D046]] sigue filtrando las entradas estructuralmente perdedoras de esa ventana, así que las entradas netas nuevas son las de R:R aceptable. El cambio se toma con la medición de D059 (cabeza fría), no a ciegas. Bug **pre-existente**: el código viejo (`strategy.py`, ya eliminado) también pedía 50 barras H4.

**Costo.** Fetch H4 por tick algo mayor (320 vs 50 barras), cacheado por tick en `EngineMarketData` — despreciable.

**Validación.** `tests/test_daily_lull_strategy.py::TestH4WarmupConvergence` blinda que la ventana configurada converge (coincide con la serie completa a <1e-3) y que la antigua de 50 no, y que `_H4_COUNT >= 300`. Los golden tests no se mueven (sus fixtures H4 tienen 50 barras → `market.candles` devuelve las disponibles igual).

Relacionado: [[D059]], [[D046]], [[D055]], `drift/strategies/daily_lull.py`, `tests/test_daily_lull_strategy.py`.

## D061 — El time-stop de sesión debe anclarse al boundary del motor, no a la hora de la vela servida

**Decisión (2026-06-09, fix `df07fe2` + follow-up de logging):** El time-stop del Daily Lull (cierre de sesión a `session_end_hour`=02:00 server) se evalúa contra el **boundary autoritativo del motor** (`bar_close_time`, D058) más una **guardia de frescura** (la vela servida debe estar a ≤1 intervalo M15 del boundary), no contra `m15_df.index[-1].hour`. Además: el motor **deduplica** `close_all` a **una notificación por estrategia por tick**, con wording calmo (no "🛑 BOT STOPPED"), y reactiva la notificación de **inicio de sesión** por estrategia. El skip de `open_trade` (guardia de spread D046 / reversión) se loguea en el motor a **WARNING** ("returned no ticket — see executor log"), no ERROR — el executor ya loguea la causa al nivel correcto.

**Bug que corrige (regresión del refactor Phase 2.6, detectada en la 1ª sesión e2e en vivo, 2026-06-09).** El motor genérico portó el time-stop usando la hora de la **vela del dataframe**, no la del boundard. Eso produjo DOS fallas simétricas:
- **Cierre espurio al INICIO de sesión.** Tras el sueño largo, en el primer wake (boundary 21:00) el `MarketData` pudo servir una vela rancia cuyo índice caía en `hour==2` → disparó `Decision.close_all` al arranque. Como `close_all` es a-nivel-estrategia pero el Lull lo devuelve por par, `_handle_close_all` notificó **una vez por par** → las 4-5 alertas idénticas "🛑 BOT STOPPED · daily_lull: session_close" que reportó el usuario a las 13:00 Bogotá.
- **El cierre real NO se disparaba.** En el boundary real 02:00 server, la última vela cerrada es la de 01:45 (`hour==1`), así que `bar_time.hour==2` nunca se cumplía → el time-stop **no cerraba las posiciones** y quedaban abiertas pasada la sesión (la sesión del 2026-06-09 dejó EURJPY y GBPJPY abiertos; se cerraron manualmente para restaurar el estado plano). El código pre-refactor usaba la hora del boundary y sí cerraba; el motor la rompió. Esta es la cara más seria del bug.

**Por qué boundary + frescura.** El boundary es el reloj de sesión real (cuándo despierta el motor). Anclar ahí hace que: en el boundary 21:00 (inicio) `hour≠2` → no cierra; en el boundary 02:00 (cierre) `hour==2` con la vela 01:45 fresca (age ≤15min) → cierra. La guardia de frescura evita que una vela rancia en cualquier boundary fuerce un cierre. **Equivalencia de backtest intacta**: ahí `bar_close_time == index[-1]` y la vela siempre es fresca, así que el comportamiento es idéntico al `bar_time.hour == session_end_hour` original (verificado, `tests/test_backtest_engine.py` con `DRIFT_RUN_SLOW_BACKTEST=1`).

**Observación de la sesión (no es bug):** el AUDNZD sell cerró en −$30.05 vs ~1% objetivo (~$19). El sizing fue correcto (lot 0.26, riesgo esperado ~$16, incluso bajo el 1%); el exceso fue **slippage en el stop** (SL 1.20993, fill 1.21085 = ~9.2 pips de slippage), realidad de la ventana de baja liquidez. A vigilar con más muestras, sin acción de código.

**Validación.** `tests/test_engine.py` y `tests/test_daily_lull_strategy.py`: vela rancia hora-2 en boundary 21:00 → no close_all; vela rancia en boundary 02:00 → `stale_session_data` (no cierre); vela fresca en boundary 02:00 → sí close_all; N pares con close_all en un tick → un solo cierre + una sola notificación; inicio de sesión notifica una vez.

Relacionado: [[D051]], [[D058]], [[D057]], [[D046]], `drift/engine.py`, `drift/strategies/daily_lull.py`, `drift/telegram_bot.py`, `tests/test_engine.py`.

---

### D062 — Equity por estrategia: baseline notional fijo y persistido (no balance vivo)

**Contexto.** La auditoría 2026-06-09 (post-Phase 2.6) encontró que el freno de drawdown por estrategia mide sobre una equity mal construida. `Engine._check_strategy_drawdown` y `main._check_strategy_drawdown_pause` calculan `allocated_baseline = balance_vivo × allocation_pct/100` y luego `strategy_equity()` (D053) le **vuelve a sumar** `realized`. Pero el balance de MT5 ya incluye el realized → `equity = inicial + 2·realized + floating`.

**Impacto.** Para la config actual (daily_lull al 100%) el freno de 10% por estrategia salta a ~5% real (≈2× demasiado sensible; conservador, no peligroso, pero no es lo configurado y choca con el freno global de 10%). Para el objetivo real de la Phase 2.6 (N estrategias) es peor: como `balance_vivo` incluye el realized de **todas** las estrategias, el baseline de A se contamina con la P&L de B → A puede aparecer en drawdown por culpa de B. **Rompe el aislamiento de riesgo que justifica D053.**

**Decisión.** La equity por estrategia se calcula sobre un **baseline notional fijo y persistido** en `strategy_state.baseline_capital` (columna nueva), no sobre el balance vivo:

```
baseline_capital = balance_seed × allocation_pct/100 − realized_lifetime_seed   (sembrado UNA vez)
equity(t)        = baseline_capital + realized_lifetime(t) + floating(t)
```

El sembrado resta el `realized_lifetime` al momento de sembrar, de modo que la equity **arranca exactamente en el slice asignado** (la P&L pasada queda como agua bajo el puente) y **hacia adelante el realized se cuenta una sola vez**. Para 1 estrategia al 100% queda `equity = equity de cuenta`, idéntico al freno global. En la primera computación tras el deploy se siembra el baseline y se **resetea el peak inflado existente** a la nueva equity (la curva de drawdown se reinicia limpia desde el deploy — comportamiento intencional, no se intenta reconstruir el drawdown histórico).

**Implementación clave.** Se extrae **UNA función canónica** en `drift/risk.py` que computa equity+drawdown por estrategia; la usan los tres contextos (engine, monitor, telegram) — elimina la triplicación y la divergencia. Misma decisión incluye el fix de `get_stats(strategy=)`: hoy la subconsulta de `peak_balance` events **no filtra** por estrategia y devuelve el peak global (#5 del audit; latente porque solo se lee `total_pnl`, pero es una trampa).

**Alternativa descartada — "fracción del balance vivo"** (`equity = balance_vivo × alloc + floating`, sin sumar realized): cero migración y exacta para 1 estrategia al 100%, pero atribuye una fracción del realized de **toda la cuenta** a cada estrategia → sigue contaminando entre estrategias. No cumple el espíritu de D053; se prefirió el baseline fijo por ser el único que aísla de verdad y es forward-compatible al añadir estrategias.

**Limitación conocida.** Cambiar `allocation_pct` en config después de sembrar deja el baseline obsoleto; el re-seed manual (poner `baseline_capital` a NULL) lo recalcula. Documentar.

Relacionado: [[D053]], `drift/risk.py`, `drift/db.py`, `drift/engine.py`, `main.py`. Implementa: Phase 2.7 Step 37.

---

### D063 — `/resume <estrategia>` resetea el peak (ventana de drawdown nueva)

**Contexto.** `strategy_state.peak_equity` se mantiene con `MAX` (`upsert_strategy_peak`) y **nunca baja**. Una vez que una estrategia toca su freno, solo se libera si la equity vuelve al máximo histórico. Peor: el monitor recalcula cada 30s, así que tras un `/resume <estrategia>` (que solo limpiaba la pausa) el monitor **re-pausa en ≤30s y re-emite la alarma** — `/resume` era efectivamente inútil y ruidoso durante un drawdown (#2 y #3 del audit).

**Decisión.** `/resume <estrategia>` resetea `peak_equity = equity_actual` **y** limpia la pausa, abriendo una ventana de drawdown nueva desde donde está la estrategia. El monitor ya no la re-pausa (drawdown vs el peak nuevo = 0%). Requiere una función `reset_strategy_peak` de **escritura directa** (el `upsert` con `MAX` no puede bajar el valor). Es una palanca manual y consciente para "soltar el freno"; el trade-off (si la estrategia sigue sangrando podría re-pausar más abajo) es aceptable por ser una acción explícita del usuario.

**Alcance.** Solo per-estrategia. El `/resume` global NO resetea el peak global: este se almacena como eventos `peak_balance` con `MAX` en `get_stats`, así que bajarlo limpio necesita su propio diseño (tabla de estado con columna directa o evento `peak_reset` que `get_stats` honre). **Diferido** a follow-up (el freno global conserva su rigidez por ahora; los #2/#3 reportados eran per-estrategia).

Relacionado: [[D062]], [[D053]], `drift/db.py`, `drift/telegram_bot.py`. Implementa: Phase 2.7 Step 38.

---

### D064 — La pausa bloquea solo aperturas (close/close_all se honran)

**Contexto.** Hoy una estrategia pausada se **excluye del scheduler** (`_active_strategies`) y, bajo pausa global (`state.paused`), `_dispatch_strategy` salta `on_bar` para todos los pares. Consecuencia: el **time-stop de las 02:00 no se ejecuta** durante un kill switch global → una posición de sesión queda abierta pasada su hora de cierre, protegida solo por SL/TP server-side (#6 del audit).

**Decisión.** La pausa (global o por estrategia) bloquea **solo `open`**; `close` y `close_all` se ejecutan siempre. Coherente con "pausa = no asumir riesgo nuevo, pero respetar las salidas planeadas" y más seguro (ninguna posición cabalga pasada su hora de cierre). Implica un cambio de modelo: las estrategias pausadas **siguen agendadas** (ya no se excluyen del scheduler) y el bloqueo se mueve al manejo de decisiones (se descarta el `Decision.open` bajo pausa). El log "Bot paused — skipping" pasa a una vez por tick, no por par (#9).

**Alternativa descartada — "congelar todo" (actual):** simple y predecible, pero deja posiciones pasadas su hora de cierre solo bajo SL/TP. Se prefirió honrar las salidas porque un cierre es reducción de riesgo, no riesgo nuevo.

Relacionado: [[D053]], [[D057]], `drift/engine.py`. Implementa: Phase 2.7 Step 40.

---

### D065 — Offset de servidor robusto en arranque frío (guard +2/+3 + refresh antes del wake)

**Contexto.** Bug observado **en vivo el 2026-06-10** durante la validación e2e de Step 36. La máquina se reinició en frío por la mañana; MT5 arrancó con el feed frío y `get_server_utc_offset` derivó **UTC-5** (raw≈-19516s, un tick ~8h rancio) en vez del real **UTC+3**. El guard de stale-tick de D047 solo rechazaba offsets fuera de ±14h, y -5h **cae dentro** de esa banda → pasó. El scheduler computó el primer sleep con ese offset malo (`sleeping 42805s` → habría despertado a las 21:00 hora local en vez de 21:00 server) y, aunque `_refresh_server_offset` corrigió el offset 2s después, lo hizo **después** de computar el `wait_secs` → el sleep ya estaba envenenado y el bot **se habría saltado la sesión entera**. (No es regresión de Phase 2.7; el código de offset/scheduling es de D041/D047/D058.)

**Decisión (parche, dos partes).**
1. **Guard estricto en `get_server_utc_offset`** (`drift/mt5_client.py`): ICMarkets es NY-anchored y solo corre a **GMT+2 (invierno US) o GMT+3 (verano US)** (D041). El offset redondeado debe estar en `{+2, +3}`; cualquier otro valor (incluido el -5 del feed frío y los stale de fin de semana de decenas de horas) se trata como tick rancio y cae al **fallback DST-aware**. Esto supersede la banda ±14h de D047 (más laxa) y caza la causa raíz directamente.
2. **Reordenar el loop del engine** (`drift/engine.py`): `_refresh_server_offset()` se llama **antes** de computar `now`/`boundary`/`wait_secs`, no después. Así un offset malo en el arranque se corrige en el mismo ciclo **antes** de calcular el sleep, en vez de un ciclo tarde. Defensa en profundidad + mantiene la alineación ante transiciones DST.

**Validación.** `tests/test_timezones.py::TestServerOffsetStaleTickGuard`: el caso D065 (tick -5h dentro de ±14h → fallback, nunca UTC-5), off-by-one (+4 → fallback), +2 invierno y +3 verano OK. Suite 331 passed / 2 skipped.

**Operacional.** El 2026-06-10 se mitigó con un restart manual (feed ya caliente → UTC+3). Con este parche, un arranque frío en el VPS (escenario típico de reboot) ya no envenena el schedule. Relevante antes de Phase 3 (go-live en VPS).

Relacionado: [[D041]], [[D047]], [[D058]], `drift/mt5_client.py`, `drift/engine.py`. Patch directo (no Phase 2.7).

---

### D066 — El cierre de sesión a las 02:00 siempre notifica (heartbeat), incluso sin trades

**Contexto.** Bug de observabilidad detectado en vivo al auditar ~2 semanas de operación (2026-06-19). El usuario reportó que "hace días no aparece SESSION CLOSED" y "no hay trades". Los logs confirmaron que el bot estaba sano (sesiones abrían cada día, `SESSION OPEN` disparaba), pero el guard `if closed == 0: return` en `_handle_close_all` (#7) **silenciaba** la notificación de cierre cuando no había posiciones abiertas a las 02:00. Como el spread guard [[D046]] venía rechazando todas las aperturas (el fill llegaba en/pasado el TP-midpoint del Daily Lull), no había posiciones que cerrar → cero `SESSION CLOSED` por días. El silencio hizo **indistinguible** "bot sano sin setups" de "bot colgado/muerto", ocultando que la estrategia llevaba días sin operar.

**Decisión.** `_handle_close_all` **siempre** emite la notificación `session_closed` en el time-stop de las 02:00, incluso con `closed == 0`. El wording de la noche sin trades es calmo ("session closed, no trades this session, sleeping until next session") para que se lea como un ping de vida, no como ruido. Supersede la regla "quiet nights no noise" de #7: la visibilidad operacional pesa más que el ruido de un mensaje calmo por sesión por estrategia.

**Validación.** `tests/test_engine.py::test_close_all_session_close_heartbeat_when_no_positions` (renombrado desde `..._silent_when_no_positions`): con cero posiciones, dispara exactamente una notificación `session_closed` cuyo detalle contiene "no trades".

**No es la causa raíz de los síntomas.** Los negativos y la ausencia de trades del Daily Lull son estructurales (TP=midpoint < spread de la franja ilíquida; [[D043]]/[[D046]] ya medían el edge en +3-5% anual). D066 solo restaura la señal de vida; la decisión de pausar el Daily Lull y pivotar a una estrategia nueva se toma por separado.

Relacionado: [[D046]], [[D043]], [[D057]], [[D061]], `drift/engine.py`, `tests/test_engine.py`, `docs/user/commands.md`.

---

### D067 — Estrategia #2: London Opening-Range Breakout (`london_orb`)

**Contexto.** El Daily Lull (estrategia #1) corrió ~2 semanas en demo (jun-2026), dio PF 0.01 / 1W-6L y terminó sin abrir trades. Auditoría 2026-06-19: **una sola causa raíz** — el TP en el midpoint del rango daba un reward (6–25 pips) ≤ spread de la franja ilíquida 23:00–02:00 server (spreads JPY 5–20 pips), así que el spread guard [[D046]] abandonaba toda orden. El Lull queda pausado ([[D069]]); se pivota a una estrategia nueva diseñada para **invertir cada modo de falla del Lull** (ver §9 de `strategy-framework.md` y la memoria `daily-lull-live-lessons`). Sesión `/spec` 2026-06-19.

**Decisión.** La estrategia #2 es **London Opening-Range Breakout** — `name: london_orb`. Mean reversion → **momentum direccional**; franja ilíquida → **apertura de Londres (líquida)**; TP midpoint → **TP = múltiplo del spread**. Tesis (pitch): *"capturar el impulso direccional de la apertura de Londres rompiendo el rango de la primera hora."*

Mecánica (tickea en M15, igual que el Lull; razona en hora server):
- **Rango:** high/low de las 4 velas M15 entre **10:00–11:00 server** (apertura de Londres; ver [[D068]] para el anclaje horario).
- **Gatillo:** *cierre* de M15 más allá del extremo del rango (no la mecha) → entra en el sentido de la ruptura. **Ambos sentidos**, sin filtro de tendencia (simplicidad; revisable en optimización).
- **Geometría (R:R 1:1):** SL en el **extremo opuesto** del rango (riesgo = ancho del rango), TP = **1× ancho** más allá de la ruptura. Simétrica, un parámetro menos; el SL lejos evita los stop-outs por re-test de la ruptura.
- **Filtro de ancho de rango:** sólo operar si el rango está en `[range_atr_min, range_atr_max]` × ATR-de-primera-hora **y** por encima de un **piso absoluto en pips por par** (`range_pip_floor`). Este filtro **es** la garantía estructural reward≫spread — la lección L1 codificada como gate, no como esperanza: un piso de rango asegura que el TP nunca colapse al tamaño del spread.
- **Una operación por par por día**, sin re-entrada tras stop.
- **Time-stop:** flat a las **18:00 server** (fin de Londres); ver [[D068]]. Sin holds overnight.
- **Pares:** GBPJPY, GBPUSD, EURJPY, EURUSD (movers de Londres con buen ratio rango/spread). El backtest **rankea y poda** (como el Lull descartó USDJPY, D029) — los 4 son candidatos, no un compromiso.
- **Indicadores:** sólo ATR (reusa `drift/indicators.py`). Sin EMA (no hay filtro de tendencia).

**Por qué ORB y no trend-following/NY.** ORB es la **inversión más limpia y verificable** del post-mortem (líquido + direccional + target grande); es crisp y backtesteable (1–2 params, poca superficie de overfit); y **reutiliza el esqueleto define-range/lock/ventana** ya construido y depurado en el Lull (mismo andamiaje, gatillo opuesto). Trend-following H1 (la tesis "más robusta" de D029) se descartó por ser más vago, más params y validación más lenta; NY momentum es la misma idea en otra franja, reservada como variante futura. Refuerza la tesis original de Drift de que lo direccional es lo robusto para retail.

**Por qué R:R 1:1 (no 2:1).** Con SL en el extremo opuesto el SL queda lejos → menos stop-outs por el re-test clásico de la ruptura. El reward (30–50 pips típicos) deja el spread (0.7–3 pips en horas de Londres) en <10% del edge, así que [[D046]] prácticamente nunca rechazará — exactamente lo que el Lull no lograba. La optimización puede empujar el TP con datos después.

**Riesgo conocido — cesta correlacionada.** Los 4 pares comparten GBP/EUR/JPY/USD; el guard global `max_same_currency_direction` ([[D053]]) puede bloquear entradas simultáneas en el mismo sentido. Es control de riesgo correcto, no un bug.

Relacionado: [[D046]], [[D043]], [[D066]], [[D029]], [[D051]], [[D068]], [[D069]], `strategy-framework.md` §7/§9, `docs/knowledge/london-orb.md`. Implementa: Phase 2.8 (Steps 43–50).

---

### D068 — Anclaje horario de `london_orb`: 10:00 server fijo (drift de DST aceptado), Lun-Vie, time-stop 18:00

**Decisión (2026-06-19).** La ventana de `london_orb` se ancla a **hora server fija**, sin re-localizar — consistente con la regla del contrato "la estrategia razona en hora server, nunca re-localiza" (ver `strategy-framework.md` §2.1):
- **Rango 10:00–11:00 server**, time-stop **18:00 server**.
- **Opera Lun-Vie** (a diferencia del Lull, que salta el viernes por su franja asiática): la mañana de Londres es sesión líquida normal todos los días hábiles, y al quedar flat a las 18:00 no hay riesgo de gap de fin de semana.

**Drift de DST — aceptado y acotado.** El server está anclado a **NY DST** (GMT+2 invierno / GMT+3 verano, transiciones US: ~Mar 8 / Nov 1; ver [[D041]]) y Londres a **EU DST** (transiciones ~Mar 29 / Oct 25). Hay ~4 semanas/año (mediados de marzo y fines de octubre) donde el calendario US y el EU están desfasados y la apertura real de Londres cae a las **11:00 server** en vez de 10:00; esas semanas el rango 10:00–11:00 capturaría la hora previa a la apertura. Se acepta el desfase **fijo a 10:00 server** en lugar de computar la apertura real de Londres, porque la alternativa introduce la **única pieza de lógica fuera de hora-server**, rompiendo la regla del contrato. Es un error acotado (~4 sem/año, 1h) y documentado; se revisa sólo si el backtest muestra que esas semanas rinden mal.

**Filtro de noticias — diferido a Phase 3.** El primer viernes hay NFP (13:30 server), que puede pegar mid-trade; y la apertura de Londres trae data del UK. Igual que el Lull difirió el filtro de holidays japoneses, el filtro de calendario económico se difiere a Phase 3 (se evalúa con el journal). El SL fijo en el extremo del rango protege mientras tanto.

Relacionado: [[D041]], [[D067]], [[D058]], `drift/strategies/london_orb.py`. Implementa: Phase 2.8.

---

### D069 — Pivote operativo: Daily Lull `enabled: false`, `london_orb` allocation 100%, magic_offset 1

**Decisión (2026-06-19).** Al añadir `london_orb`, se pivota el capital notional fuera del Lull:
- **Daily Lull → `enabled: false`** en `config.yaml`. Ahora **sí se puede** desactivarlo (la config exige ≥1 estrategia *enabled*, y `london_orb` la satisface) — antes no, por ser la única (ver memoria `daily-lull-live-lessons`). `enabled: false` preserva su config para A/B o re-evaluación futura ([[D054]]); sus posiciones demo vivas, si quedaran, se conservan bajo su SL/TP server-side (la pausa/desactivación **no** cierra a mercado, [[D053]]).
- **`london_orb`: `allocation_pct: 100`** (todo el presupuesto notional al edge nuevo), **`magic_offset: 1`** → magic efectivo **234001** (único, no colisiona con el 234000 del Lull; `_validate` lo verifica, [[D052]]).
- **Riesgo por estrategia estándar:** `percent_per_trade: 1.0`, `max_open_trades: 4`, `max_drawdown_percent: 10.0` (prioridad #1 supervivencia; 4 pares ⇒ hasta 4 trades simultáneos, cap coincide).

**Nota de re-seed.** Al cambiar de estrategia activa, `london_orb` siembra su `baseline_capital` en la primera computación post-deploy ([[D062]]); su curva de drawdown arranca limpia desde el deploy (intencional). Si en el futuro se reactiva el Lull, habrá que re-balancear allocations a ≤100% y re-seedear (poner `baseline_capital` a NULL fuerza el recálculo, [[D062]]).

Relacionado: [[D052]], [[D053]], [[D054]], [[D062]], [[D067]], `config.yaml`, `config.example.yaml`. Implementa: Phase 2.8 Step 48.
