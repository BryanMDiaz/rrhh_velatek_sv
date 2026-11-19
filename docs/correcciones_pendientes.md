# Correcciones adicionales — versión 18.0.1.3.0

Alcance: corregir los pendientes encontrados en la revisión, conservando los permisos por rol sin reactivarlos.

## Cambios

- Los saldos del préstamo suman todos los importes efectivamente abonados, aunque una cuota continúe pendiente. Un préstamo de $40 con abono de $5 muestra $5 pagados y $35 pendientes inmediatamente.
- Un centavo pendiente no se considera cuota pagada. Los abonos quedan documentados con fecha, monto, referencia y método; no se inventan cuotas de abono sobrante. Se rechazan excesos o calendarios incompatibles.
- Los abonos directos se coordinan con la validación/cierre de planilla mediante bloqueos. Un abono antes de validar actualiza el descuento de las cuotas vinculadas cuando se recalcula la planilla. Si esta ya está validada, se exige volver a borrador antes del abono y recalcular; no se altera silenciosamente un pago aprobado.
- La fecha final usa el último vencimiento del calendario real. Antes de generarlo, utiliza las mismas reglas de fechas que el generador. Una sola cuota termina en la fecha del primer descuento.
- Las vigencias activas de ISSS, AFP, ISR y tasas laborales no pueden superponerse dentro de la misma compañía, código/frecuencia y administradora cuando corresponda. La configuración global y la específica de una compañía siguen siendo ámbitos distintos. La búsqueda también rechaza duplicados antiguos para no escoger una tasa de forma ambigua.
- Nocturnidad separa la duración máxima de la jornada nocturna de la base horaria diurna equivalente usada para el recargo. Con $800 mensuales y jornada diurna de ocho horas, cuatro horas al 25 % dan $3.33. La duración nocturna ordinaria continúa limitada a siete horas. Para remuneración variable se conserva la determinación manual en nómina.
- Las producciones e incidencias procesadas no permiten edición ni eliminación, incluso para administradores. La incidencia parcialmente utilizada en una preplanilla cerrada conserva sus datos, sin impedir que otra quincena complete su consumo. Sus asignaciones validadas/cerradas tampoco se pueden borrar, trasladar o editar.
- La prueba de viáticos especifica el tratamiento remunerativo, requerido por el modelo. No se eliminó esa validación para acomodar la prueba.

Los cambios en operational_security.py son controles de integridad de datos procesados. require_group continúa desactivado como antes; no se cambiaron sus condiciones ni se agregaron restricciones por perfil.

## Actualización de registros existentes

La migración post-migration.py de esta versión actualiza los campos derivados de saldo, total pagado y fecha final de los préstamos existentes al actualizar el módulo. No recalcula planillas cerradas ni modifica importes efectivamente pagados. Se permite liquidar registros históricos de anticipo que ya existían sin vínculo a quincena; las solicitudes nuevas siguen obligadas al flujo de anticipo quincenal.

## Verificación

En una base temporal de Odoo 18, las 92 pruebas seleccionadas pasaron sin fallos ni errores. Solo se excluyeron siete métodos de prueba cuyo propósito exige reactivar permisos por rol. La ejecución completa previa registró únicamente fallos de esos métodos (16 resultados fallidos incluyendo subcasos); no se declara aprobada esa parte excluida.

Se probó expresamente la migración de un saldo histórico incorrecto ($40 después de abonar $5) y una fecha final incorrecta (15/01/2027 en vez de 31/12/2026), así como el cierre de incidencias distribuidas entre dos quincenas.

Referencia de nocturnidad: Código de Trabajo, artículos 161 y 168:
https://infotrade.economia.gob.sv/wp-content/uploads/12-Legislacion_codigo-de-trabajo.pdf

La base operativa no se actualizó durante las pruebas. Para aplicar el código y la migración se necesita actualizar rrhh_velatek_sv y reiniciar Odoo.
