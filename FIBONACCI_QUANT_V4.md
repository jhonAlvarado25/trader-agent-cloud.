# V4.2 + Fibonacci + matemática financiera

## Objetivo

Esta capa mejora el análisis del Trader Agent V4.2 sin depender de una VM ni cambiar el principio de ejecución manual en Binance.

No se incorporó ninguna regla con el objetivo de "forzar" resultados positivos. La lógica prioriza robustez fuera de muestra y evita seleccionar una variante usando el tramo TEST.

## Fibonacci

Se calculan retrocesos 38,2%, 50%, 61,8% y 78,6% sobre un rango previo de 55 velas.

Controles metodológicos:

- Los anclajes usan exclusivamente velas anteriores mediante `shift(1)`, evitando look-ahead.
- Fibonacci NO genera una entrada por sí solo.
- Se considera confluencia si el precio está cerca de un nivel Fibonacci dentro de una tolerancia expresada en ATR.
- La variante `FIBONACCI` se compara contra `BASE` usando únicamente el 20% de VALIDACIÓN.
- Solo se selecciona Fibonacci si mantiene muestra mínima y mejora score, expectativa y Profit Factor de validación.
- El 20% TEST permanece fuera de la selección y se usa después para medir evidencia.

La evidencia empírica publicada no respalda Fibonacci como regla autónoma. Por eso el agente lo utiliza únicamente como filtro condicional.

Referencia:
Tsinaslanidis, Guijarro y Voukelatos (2022), Automatic identification and evaluation of Fibonacci retracements: Empirical evidence from three equity markets, Expert Systems with Applications, DOI 10.1016/j.eswa.2021.115893.

## Matemática financiera incorporada

### Expectativa y Profit Factor
Se mantienen como métricas centrales de rentabilidad por unidad de riesgo.

### Sharpe por operaciones
Mide retorno medio en R frente a su dispersión y lo anualiza según la frecuencia observada de operaciones.

### Sortino
Penaliza principalmente la desviación negativa, separando volatilidad favorable de riesgo a la baja.

### Calmar
Relaciona la expectativa anualizada en R con el drawdown máximo observado.

### Probabilistic Sharpe Ratio (PSR)
Estima la probabilidad de que el Sharpe poblacional supere cero considerando tamaño de muestra, asimetría y curtosis.

Referencia:
Bailey y López de Prado, The Sharpe Ratio Efficient Frontier, Journal of Risk 15(2), 2012/2013.

### Kelly
Se calcula Kelly completo y una versión conservadora usando el límite inferior Wilson del win rate.

La V4.2 NO usa Kelly para aumentar automáticamente el riesgo por encima del 1,20% configurado. Se muestra como diagnóstico de eficiencia del edge.

### Crecimiento geométrico
Convierte cada resultado en R a retorno de cuenta usando el riesgo configurado y calcula crecimiento compuesto histórico anualizado.

Es un proxy histórico, no una proyección garantizada.

### Intervalo Wilson
Se calcula un límite inferior de confianza del win rate para evitar interpretar una tasa de aciertos pequeña como si fuera estable.

## Protección contra sobreajuste

La arquitectura mantiene:

- separación cronológica 60% desarrollo / 20% validación / 20% TEST;
- bootstrap;
- Monte Carlo;
- costos y slippage;
- funding cuando está disponible en Futures Lab;
- selección BASE/FIBONACCI basada en VALIDACIÓN, no TEST;
- evidencia TEST posterior;
- ejecución manual.

La literatura sobre Deflated Sharpe Ratio advierte que probar muchas estrategias puede inflar artificialmente el mejor backtest. Por esa razón esta versión añade PSR y mantiene un TEST separado, en lugar de ordenar configuraciones únicamente por rentabilidad histórica.

Referencia:
Bailey y López de Prado (2014), The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality, Journal of Portfolio Management 40(5).

## Volatilidad

Se calcula también `volatility_ratio_100`, que compara ATR% actual contra su mediana histórica reciente. Por ahora se usa como diagnóstico, no para aumentar apalancamiento.

Existe evidencia académica favorable a reducir exposición en regímenes de alta volatilidad, pero también trabajos posteriores encuentran resultados fuera de muestra menos consistentes. Por ello no se activa un escalamiento automático de riesgo en esta versión.

Referencias:
- Moreira y Muir (2017), Volatility-Managed Portfolios, Journal of Finance.
- Cederburg et al. (2020), On the performance of volatility-managed portfolios, Journal of Financial Economics.

## Regla de seguridad

Ninguna de estas métricas garantiza beneficios futuros. Una señal solo se considera candidata si supera los filtros técnicos y estadísticos existentes. Las órdenes continúan siendo manuales.
