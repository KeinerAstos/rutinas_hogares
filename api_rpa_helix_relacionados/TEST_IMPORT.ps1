$ErrorActionPreference = "Stop"

$Root = "C:\xampp\htdocs\rutinas_hogares\api_rpa_helix_relacionados"

Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "No existe entorno virtual: $Python"
}

& $Python -c @"
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(r'$Root') / '.env')

import app.services.helix.incident_related_batch_service as motor

print('IMPORT_OK=True')
print('iniciar_job=' + str(hasattr(motor, 'iniciar_job')))
print('obtener_job=' + str(hasattr(motor, 'obtener_job')))
print('pausar_job=' + str(hasattr(motor, 'pausar_job')))
print('reanudar_job=' + str(hasattr(motor, 'reanudar_job')))
print('detener_job=' + str(hasattr(motor, 'detener_job')))
print('URL_HELIX_CONFIGURADA=' + str(bool(getattr(motor, 'URL_HELIX', ''))))

print('')
print('--- FASTAPI ---')

import app.main as main

print('MAIN_IMPORT_OK=True')
print('FASTAPI_APP=' + str(main.app is not None))

for route in main.app.routes:
    path = getattr(route, 'path', None)

    if not path:
        continue

    if 'helix' in path.lower() or 'health' in path.lower():
        methods = sorted(getattr(route, 'methods', []) or [])
        print(
            'ROUTE=' + path +
            ' METHODS=' + ','.join(methods)
        )
"@

if ($LASTEXITCODE -ne 0) {
    throw "TEST_IMPORT fallo."
}
