<?php
declare(strict_types=1);

date_default_timezone_set('America/Bogota');

$configPath =
    'C:\xampp\htdocs\CentralNOC\modules\Dashboard_Hogar'
    . '\config\database_bitacora.php';

if (!is_file($configPath)) {
    echo json_encode([
        'ok' => false,
        'codigo' => 'CONFIG_NO_ENCONTRADA',
        'error' => 'No se encontró database_bitacora.php.',
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit(1);
}

require_once $configPath;

function salida(array $payload, int $exitCode = 0): never
{
    echo json_encode(
        $payload,
        JSON_UNESCAPED_UNICODE |
        JSON_UNESCAPED_SLASHES |
        JSON_INVALID_UTF8_SUBSTITUTE
    );

    exit($exitCode);
}

function texto_argumento(int $indice): string
{
    global $argv;

    return trim((string) ($argv[$indice] ?? ''));
}

function normalizar_ot(string $numero): string
{
    return mb_strtoupper(trim($numero), 'UTF-8');
}

$accion = mb_strtolower(texto_argumento(1), 'UTF-8');
$valor = texto_argumento(2);
$limite = max(1, min(100, (int) (texto_argumento(3) ?: 20)));

try {
    $pdo = bitacora_db();

    if ($accion === 'health') {
        $pdo->query('SELECT 1');

        salida([
            'ok' => true,
            'servicio' => 'operation_bridge',
            'database' => 'conectada',
            'modo' => 'solo_lectura',
            'fecha' => date(DATE_ATOM),
        ]);
    }

    if ($accion === 'listar_ots') {
        $sql = '
            SELECT
                o.id,
                o.numero_ot,
                o.frente,
                o.categoria_solicitud,
                o.tipo_solicitud,
                o.mensaje_solicitud_original,
                o.aliado,
                o.ciudad,
                o.regional,
                o.estado_ot,
                o.incidente_relacionado,
                o.helix_estado,
                o.helix_codigo,
                o.helix_error,
                o.helix_intentos,
                o.helix_duracion_seg,
                o.helix_consultado_en,
                o.fecha_reporte,
                o.estado_gestion,
                o.nivel_escalamiento,
                o.proximo_seguimiento,
                o.responsable_nombre,
                o.observacion_inicial,
                o.created_at,
                o.updated_at,
                (
                    SELECT COUNT(*)
                    FROM mesa_ayuda_seguimientos s
                    WHERE s.ot_id = o.id
                ) AS total_seguimientos
            FROM mesa_ayuda_ots o
            ORDER BY
                CASE
                    WHEN o.estado_gestion IN ("CERRADO", "CANCELADO")
                    THEN 1
                    ELSE 0
                END,
                CASE
                    WHEN o.proximo_seguimiento IS NULL
                    THEN 1
                    ELSE 0
                END,
                o.proximo_seguimiento ASC,
                o.id DESC
            LIMIT :limite
        ';

        $stmt = $pdo->prepare($sql);
        $stmt->bindValue(':limite', $limite, PDO::PARAM_INT);
        $stmt->execute();

        $rows = $stmt->fetchAll();

        salida([
            'ok' => true,
            'total' => count($rows),
            'data' => $rows,
        ]);
    }

    if ($accion === 'buscar_ot') {
        $numeroOt = normalizar_ot($valor);

        if (!preg_match('/^WO\d{13}$/', $numeroOt)) {
            salida([
                'ok' => false,
                'codigo' => 'OT_INVALIDA',
                'error' => 'La OT debe tener formato WO seguido de 13 dígitos.',
            ], 2);
        }

        $stmt = $pdo->prepare(
            '
            SELECT
                o.*,
                (
                    SELECT COUNT(*)
                    FROM mesa_ayuda_seguimientos s
                    WHERE s.ot_id = o.id
                ) AS total_seguimientos
            FROM mesa_ayuda_ots o
            WHERE o.numero_ot = :numero_ot
            LIMIT 1
            '
        );

        $stmt->execute([
            'numero_ot' => $numeroOt,
        ]);

        $row = $stmt->fetch();

        if (!$row) {
            salida([
                'ok' => false,
                'codigo' => 'OT_NO_ENCONTRADA',
                'error' => 'La OT no está registrada en la Bitácora.',
                'numero_ot' => $numeroOt,
            ], 3);
        }

        salida([
            'ok' => true,
            'data' => $row,
        ]);
    }

    if ($accion === 'seguimientos_ot') {
        $numeroOt = normalizar_ot($valor);

        if (!preg_match('/^WO\d{13}$/', $numeroOt)) {
            salida([
                'ok' => false,
                'codigo' => 'OT_INVALIDA',
                'error' => 'La OT debe tener formato WO seguido de 13 dígitos.',
            ], 2);
        }

        $stmtOt = $pdo->prepare(
            '
            SELECT *
            FROM mesa_ayuda_ots
            WHERE numero_ot = :numero_ot
            LIMIT 1
            '
        );

        $stmtOt->execute([
            'numero_ot' => $numeroOt,
        ]);

        $ot = $stmtOt->fetch();

        if (!$ot) {
            salida([
                'ok' => false,
                'codigo' => 'OT_NO_ENCONTRADA',
                'error' => 'La OT no está registrada en la Bitácora.',
                'numero_ot' => $numeroOt,
            ], 3);
        }

        $stmt = $pdo->prepare(
            '
            SELECT
                id,
                ot_id,
                tipo_gestion,
                contactado,
                resultado,
                observacion,
                compromiso,
                proximo_seguimiento,
                nivel_escalamiento,
                estado_gestion,
                usuario_id,
                usuario_nombre,
                fecha_registro
            FROM mesa_ayuda_seguimientos
            WHERE ot_id = :ot_id
            ORDER BY fecha_registro ASC, id ASC
            '
        );

        $stmt->execute([
            'ot_id' => (int) $ot['id'],
        ]);

        $rows = $stmt->fetchAll();

        salida([
            'ok' => true,
            'ot' => $ot,
            'total' => count($rows),
            'data' => $rows,
        ]);
    }

    salida([
        'ok' => false,
        'codigo' => 'ACCION_NO_SOPORTADA',
        'error' => 'Acción no soportada por operation_bridge.',
        'accion' => $accion,
    ], 4);
} catch (Throwable $e) {
    salida([
        'ok' => false,
        'codigo' => 'DATABASE_ERROR',
        'error' => $e->getMessage(),
    ], 1);
}
