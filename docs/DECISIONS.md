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

---

## D029 — Cambio de estrategia a Asian Session Scalper

**Decisión:** Reemplazar la estrategia activa (mean reversion H4 con filtro MLP) por Asian Session Scalper en M15, operando solo durante la ventana 21:00-02:00 GMT. Ver `docs/plans/asian-session-scalper-live.md` para el plan de implementación y `docs/knowledge/asian-session-scalper.md` para las reglas.

**Recorrido hasta llegar aquí:**

| Iteración | Estrategia | Resultado backtest (mejor par) |
|---|---|---|
| 1 | Trend following EMA 50/200 + MACD (D002+D005) | Negativo en mayoría de pares — majors demasiado eficientes |
| 2 | Trend following v2: EMA pullback + RSI + ADX | +0.1%, PF 1.31 — insuficiente |
| 3 | Mean reversion H4: BB + RSI + ADX < 25 | +0.1% portfolio, problemas R:R |
| 4 | Mean reversion H4 + MLP regime filter (walk-forward) | AUDCAD PF 1.74, WR 60%, solo 10 trades en 6 meses |
| 5 | **Asian Session Scalper M15** | **EURCHF PF 8.17, AUDNZD PF 4.29, 277 trades en 2 años** |

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

Detalle completo en `docs/plans/asian-session-scalper-live.md` y resultados raw en `backtest/results_asian_opt/`.

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

**Decisión:** Los defaults de clase de `AsianSessionStrategy` pasan a los valores optimizados de D029, y `run_asian_backtest`/`run_asian.py` aceptan y aplican los 6 parámetros desde `config.yaml`.

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
