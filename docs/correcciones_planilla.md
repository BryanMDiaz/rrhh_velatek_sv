# Correcciones de planilla — versión 18.0.1.2.0

Alcance autorizado: las seis correcciones de la evaluación, excluyendo expresamente los controles de permisos por rol.

- Se impide eliminar planillas y preplanillas validadas/cerradas. También se impide modificar sus datos de pago y agregar, editar, trasladar o eliminar líneas de planilla protegidas. Una planilla validada puede volver a borrador por el flujo existente antes de corregirse; no se admite mezclar reapertura y edición en la misma escritura. Los roles de ese flujo permanecen sin cambios.
- ISSS, AFP, ISR y tasas laborales requieren configuración activa para la fecha y compañía, o configuración global aplicable. Se eliminaron las búsquedas de cualquier compañía o fecha y las creaciones de emergencia. Una tabla ISR sin tramos provoca un error de configuración, incluso para base cero.
- El recibo muestra los viáticos como información ya incluida en el neto. Total a recibir coincide con Total a Liquidar y no suma nuevamente los viáticos. El pago por fuera se incluye una sola vez mediante Total a Liquidar.
- Carga directa y preplanilla usan la frecuencia del período y los mismos factores salariales: mes 1, quincena 1/2, semana 7/30 y catorcena 14/30. Se redondea el salario del período en la moneda de la compañía. Los topes específicos de ISSS se mantienen separados de la conversión del salario.
- El calendario del préstamo tiene exactamente la cantidad solicitada de cuotas positivas. Se trabaja con unidades mínimas de moneda, se ajusta el remanente y se rechazan cantidades incompatibles con el monto antes de borrar el calendario anterior. La última cuota mostrada comparte el mismo cálculo. Se valida que los intervalos personalizados sean positivos.
- AFP no aplica techo a períodos desde enero de 2023, aunque exista un valor heredado en la configuración. Para períodos anteriores, solo se aplica el techo explícito del parámetro histórico seleccionado. El parámetro general predeterminado queda sin techo y con vigencia desde enero de 2023.

Referencia AFP: https://www.crecer.com.sv/web/empresas/tasa-de-cotizacion-y-salarios/ (eliminación del salario tope desde enero de 2023).

Se conserva sin modificaciones models/operational_security.py. SHA-256 antes y después: d460cf66fc6743592e37affdf0fbdf256b5bde4361b1691d1866c9d5f1d14b38.

Verificación en base temporal de Odoo 18: 34 pruebas seleccionadas aprobadas, cero fallos y cero errores. Incluyen las correcciones, los anticipos, el reporte de recibo renderizado y las pruebas de parámetros y snapshots históricos. No se declara aprobada toda la suite de permisos, que permanece excluida del alcance. No se actualizó la base operativa.

Para aplicar: reiniciar Odoo y actualizar rrhh_velatek_sv. Si falta configuración válida, el cálculo se detendrá para que se registre la norma y su vigencia; no se reemplazará por valores supuestos. No se recalculan voluntariamente planillas cerradas ni se modifica su historial de pagos.
