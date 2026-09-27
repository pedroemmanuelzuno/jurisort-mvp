# Jurisort MVP

Prototipo web en Python para control documental jurídico.

## Incluye
- Login por usuario y rol administrador.
- Despachos y planes mensuales Solo / Office / Office+.
- Expedientes por despacho.
- Carga de PDF/JPG/PNG.
- Copia inalterada del archivo recibido + copia normalizada.
- Clasificación automática básica por reglas.
- Hash SHA-256 y detección de duplicados exactos.
- Cola de revisión humana cuando la confianza es baja.
- Centro de incidencias.
- Registro de auditoría.
- Panel de administrador.

## Ejecutar localmente
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

Abrir la dirección que muestre Streamlit.

## Primer acceso de demostración
- Correo: `admin@jurisort.local`
- Contraseña: `Cambiar123!`

**Cambiar estas credenciales antes de cualquier despliegue.**

## Importante antes de usar datos reales
Este repositorio es un MVP, no una plataforma lista para expedientes reales. Antes de producción hay que añadir, como mínimo: HTTPS, almacenamiento privado/cifrado, gestión segura de secretos, recuperación de contraseña, MFA para administradores, sesiones endurecidas, copias de seguridad cifradas, política de retención/borrado, aislamiento robusto por despacho, análisis de archivos maliciosos, límites de carga, monitoreo, gestión de vulneraciones y documentación contractual/privacidad.

La clasificación actual usa nombres de archivo. El siguiente módulo debería extraer texto localmente/OCR y producir datos estructurados con puntuación de confianza. Si se integra un proveedor externo de IA/OCR, revisar contrato, ubicación/tratamiento de datos, retención y autorización antes de enviar expedientes reales.
