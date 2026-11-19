<div align="center">

# 🇸🇻 HR Velatek SV

**Recursos humanos y planillas para El Salvador en Odoo 18**

![Odoo 18](https://img.shields.io/badge/Odoo-18.0-714B67?style=flat-square)
![Estado: en desarrollo](https://img.shields.io/badge/Estado-En%20desarrollo-F59E0B?style=flat-square)
![Licencia LGPL-3](https://img.shields.io/badge/Licencia-LGPL--3-2563EB?style=flat-square)

</div>

> 🚧 **Proyecto en desarrollo.** Todavía faltan ajustes, pruebas de flujos completos y documentación. Usa una base de pruebas para evaluar el módulo y valida los cálculos y los permisos antes de utilizarlo en producción.

## ✨ Funcionalidades disponibles

| Área | Qué incluye |
| --- | --- |
| 👥 Expedientes | Información personal, DUI, NIT, NUP, ISSS, AFP, cuentas bancarias, beneficiarios y documentos. |
| 📄 Contratos | Plantillas versionadas y generación de documentos. |
| 📊 Preplanilla y planilla | Registro de incidencias y producción, reportes de comprobantes de pago y exportación a Excel. |
| 💵 Préstamos y anticipos | Simulación de cuotas, seguimiento de saldos, abonos y descuentos en planilla. |
| ⚙️ Parámetros de El Salvador | Configuración por vigencia de ISSS, AFP, ISR y tasas laborales. |

## 🚀 Instalación en desarrollo

Requiere **Odoo 18**, una base de datos de pruebas y los módulos `hr`, `analytic`, `hr_contract` y `project`.

1. Clona el repositorio en un directorio de addons configurado en el `addons_path` de Odoo.

   ```bash
   git clone https://github.com/BryanMDiaz/rrhh_velatek_sv.git
   ```

2. Reinicia Odoo y actualiza la lista de aplicaciones con el modo desarrollador activado.
3. En **Aplicaciones**, quita el filtro predeterminado **Aplicaciones**, busca **HR Velatek SV** e instala el módulo.

## 🛠️ Pendientes antes de producción

- [ ] Validar de punta a punta la preplanilla, el cálculo y el cierre de planilla.
- [ ] Revisar permisos por rol y por compañía.
- [ ] Ampliar la guía de configuración, operación y actualización del módulo.

## 📚 Documentación del proyecto

- [Anticipo salarial](docs/anticipo_salarial.md)
- [Correcciones de planilla](docs/correcciones_planilla.md)
- [Correcciones adicionales y verificación](docs/correcciones_pendientes.md)
