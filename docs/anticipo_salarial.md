# Anticipo del neto de una quincena

## Uso

1. Prepare la preplanilla ordinaria quincenal (1–15 o 16–último día), cargue empleados y actualice incidencias. Puede permanecer en borrador/cargada: todavía no se necesita una planilla definitiva.
2. En Préstamos, seleccione empleado, tipo **Anticipo de Salario** y **Quincena a anticipar**. Guarde y pulse **Calcular anticipo neto**.
3. Revise devengado, ISSS, AFP, ISR, otras deducciones y neto. El cálculo comparte los datos y el motor de la planilla; incluye los préstamos activos programados para esa quincena. No modifica las bases por el hecho de adelantar el pago.
4. Imprima la solicitud, obtenga firma/huella y adjunte el PDF en documentos de respaldo. Envíe a aprobación. Si existe otro préstamo activo, confirme el aviso adicional existente.
5. Un administrador de RRHH aprueba. Efectúe el pago fuera de Odoo, registre su referencia y pulse **Confirmar entrega del anticipo**. Este botón registra el desembolso; no ordena transferencias.
6. Al crear/cargar/calcular la planilla definitiva del mismo período, se mostrará **Salario ya anticipado** y el pendiente será el neto definitivo menos lo entregado. Las deducciones legales aparecen una sola vez en la planilla, sobre las bases devengadas. El recibo identifica el anticipo aparte.
7. Al cerrar la planilla se liquida el anticipo y queda vinculado a ella. La solicitud, los datos del cálculo y la referencia del pago quedan preservados.

Ejemplo sin otros movimientos, con salario mensual de $408.50 y los parámetros generales del módulo: devengado quincenal $204.25, ISSS $6.13, AFP $14.81, ISR $0, anticipo neto $183.31. Si no hay cambios, pendiente al liquidar $0. El cálculo real depende de los parámetros vigentes, contrato e incidencias.

## Controles y límites

- Una misma quincena no admite dos anticipos enviados/aprobados/entregados/liquidados para el mismo empleado y compañía. La protección incluye concurrencia entre entrega y cierre de planilla.
- Solo los anticipos entregados reducen el pago final; una solicitud o aprobación no es un desembolso.
- Un anticipo entregado no se cancela ni se edita como si el dinero no hubiera salido. No se utiliza Registrar Abono para simular ajustes de salario.
- Si los datos cambian antes de la entrega, es necesario recalcular en borrador o cancelar y crear una nueva solicitud.
- Si el neto definitivo aumenta, se paga la diferencia. Si disminuye por debajo del anticipo, se bloquea el cierre y se exige conciliar/documentar el ajuste. No se convierte el exceso automáticamente en un préstamo ni se descuenta de otra quincena. Esta versión no implementa un asistente de devolución de efectivo.
- Se exige coincidencia exacta de quincena y parámetros vigentes por compañía y frecuencia. No se permiten intereses ni cuotas de devolución futuras.
- Una solicitud impresa no acredita el pago. Tras la entrega, imprima la constancia y conserve el comprobante firmado o bancario en el expediente/chatter.
- Este desarrollo no genera asientos contables, pagos bancarios, declaraciones ni remesas de ISSS/AFP/ISR. Su presentación y pago permanecen bajo el procedimiento de la empresa. Los pagos anticipados entre meses necesitan revisión contable de la fecha de retención/reporte.
- No se migran silenciosamente los anticipos antiguos registrados como préstamos de una cuota. Requieren revisión antes de usar el nuevo flujo.

## Base normativa consultada (12 de noviembre de 2026)

- Código de Trabajo, arts. 130–138: periodicidad/protección salarial, pago y documentación de devengos, descuentos y cantidades pagadas. Se conserva el salario y se identifica el importe ya entregado, sin marcar ausencia sin goce. El art. 136 regula determinados créditos de entidades externas: no se aplica su autorización como fundamento genérico de un préstamo patronal.
  https://infotrade.economia.gob.sv/wp-content/uploads/12-Legislacion_codigo-de-trabajo.pdf
- ISSS: tasas generales de trabajador 3 % y patrono 7.5 %. El cálculo reutiliza los parámetros vigentes del módulo.
  https://www.isss.gob.sv/servicios/actualizacion-de-datos-patronales/
- Ley Integral del Sistema de Pensiones, arts. 14 y 16: ingreso base y cotización. Aporte del trabajador 7.25 %.
  https://www.jurisprudencia.gob.sv/DocumentosBoveda/D/2/2020-2029/2022/12/F4EC4.PDF
- Ministerio de Hacienda, Decreto Ejecutivo 10 de 2025: tablas específicas mensual/quincenal/semanal y base después de deducciones previsionales. No se introduce una tabla fiscal paralela en el anticipo.
  https://www.mh.gob.sv/wp-content/uploads/2025/05/Modificacion-a-las-tablas-de-retenci%C3%B3n-del-Impuesto-Sobre-la-Renta-Decreto-Ejecutivo-NO.10.pdf

La implementación no es una certificación jurídica del módulo completo. Antes de operar debe revisarse la política de pago anticipado y la configuración legal vigente con el responsable laboral/contable. Se detectaron controles generales deshabilitados en operational_security.py y fallos en la suite de autorización existente; este cambio no altera dichas decisiones previas. La versión 18.0.1.2.0 elimina el techo AFP para períodos desde enero de 2023 y exige parámetros fiscales vigentes. Las reglas y parámetros históricos deben configurarse explícitamente según su vigencia.

## Verificación técnica

Probado en Odoo 18.0-20260528, en una base temporal independiente: 22 pruebas de anticipo y regresiones relacionadas aprobadas (0 fallos, 0 errores). Incluyen el neto de $183.31, flujo hasta cierre, carga real desde preplanilla, préstamos coexistentes, diferencias a favor del empleado, bloqueo por exceso adelantado, duplicados, protección del período y renderizado de la solicitud.

La ejecución más amplia del módulo detectó 37 fallos y 1 error en la suite general de autorización/validación existente, ajena a las nuevas pruebas del anticipo. No se declara aprobada la suite completa ni se ha actualizado la base operativa como parte de esta verificación.
