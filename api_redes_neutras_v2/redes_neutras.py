from __future__ import annotations

import argparse
from collections import deque
import asyncio
import gc
import json
import os
import re
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

from helix_runner import DOWNLOAD_DIR, RESULT_DIR, main_async
from smartit import get_credentials_alert

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env", override=False)

_WO_RE = re.compile(r"\b(WO\d{13,14})\b", re.IGNORECASE)
_QUEUE_TIMEOUT_SECONDS = max(
    30, int(os.getenv("HELIX_CHAT_QUEUE_TIMEOUT_SECONDS", "600") or "600")
)

# Pool configurable de workers Helix.
# Cada worker recibe un slot fijo y un perfil Chromium independiente.
# Las consultas normales mantienen orden FIFO entre ellas; los reintentos
# tienen menor prioridad y solo toman capacidad libre cuando no hay consultas
# normales esperando.
_HELIX_GATE = threading.Condition()
_HELIX_MAX_WORKERS = max(1, int(os.getenv("HELIX_WORKERS", "1") or "1"))
_HELIX_ACTIVE = 0
_HELIX_FREE_SLOTS: deque[int] = deque(range(1, _HELIX_MAX_WORKERS + 1))
_NORMAL_QUEUE: deque[object] = deque()

# Reintentos en cola usando el MISMO pool de workers/perfiles.
_RETRY_QUEUE: Queue[dict[str, Any]] = Queue()
_RETRY_LOCK = threading.Lock()
_RETRY_PENDING: set[str] = set()
_RETRY_THREAD_STARTED = False
_RETRY_ENABLED = os.getenv("HELIX_RETRY_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "si",
    "sí",
    "on",
}
_RETRY_MAX_ATTEMPTS = max(1, int(os.getenv("HELIX_RETRY_MAX_ATTEMPTS", "3") or "3"))
_RETRY_DELAY_SECONDS = max(1, int(os.getenv("HELIX_RETRY_DELAY_SECONDS", "10") or "10"))
_RETRY_IDLE_GRACE_SECONDS = max(
    0, int(os.getenv("HELIX_RETRY_IDLE_GRACE_SECONDS", "5") or "5")
)
_RETRYABLE_CODES = {
    "HELIX_DESCARGA_ADJUNTO_FALLIDA",
    "HELIX_PRUEBA_FALLIDA",
    "HELIX_SERVICE_ERROR",
}


SMCC_MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
SMCC_TARGET_ATTACHMENT_BYTES = int(1.5 * 1024 * 1024)

XL_TYPE_PDF = 0
XL_QUALITY_STANDARD = 0
XL_QUALITY_MINIMUM = 1

_EXCEL_LOCK = threading.Lock()
_WORD_LOCK = threading.Lock()


class RedesNeutrasDocumentError(RuntimeError):
    pass


def _bytes_info(size: int) -> dict[str, Any]:
    return {
        "size_bytes": int(size),
        "size_mib": round(size / 1024 / 1024, 3),
        "under_target": size <= SMCC_TARGET_ATTACHMENT_BYTES,
        "under_max": size <= SMCC_MAX_ATTACHMENT_BYTES,
    }


def _export_excel_pdf(
    source: Path,
    target: Path,
    *,
    minimum_quality: bool = False,
) -> None:

    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    excel = None
    workbook = None

    with _EXCEL_LOCK:

        pythoncom.CoInitialize()

        try:

            excel = win32com.client.DispatchEx("Excel.Application")

            excel.Visible = False
            excel.DisplayAlerts = False

            workbook = excel.Workbooks.Open(
                str(source),
                0,
                True,
            )

            for index in range(
                1,
                workbook.Worksheets.Count + 1,
            ):
                sheet = workbook.Worksheets.Item(index)

                try:
                    page_setup = sheet.PageSetup

                    page_setup.Zoom = False
                    page_setup.FitToPagesWide = 1
                    page_setup.FitToPagesTall = False
                    page_setup.CenterHorizontally = True

                finally:
                    sheet = None

            quality = XL_QUALITY_MINIMUM if minimum_quality else XL_QUALITY_STANDARD

            workbook.ExportAsFixedFormat(
                XL_TYPE_PDF,
                str(target),
                quality,
                True,
                False,
            )

        finally:

            if workbook is not None:
                try:
                    workbook.Close(False)
                except Exception:
                    pass

            if excel is not None:
                try:
                    excel.Quit()
                except Exception:
                    pass

            workbook = None
            excel = None

            gc.collect()

            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _export_word_pdf(
    source: Path,
    target: Path,
) -> None:

    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    word = None
    document = None

    with _WORD_LOCK:

        pythoncom.CoInitialize()

        try:

            word = win32com.client.DispatchEx("Word.Application")

            word.Visible = False
            word.DisplayAlerts = 0

            document = word.Documents.Open(
                str(source),
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )

            document.ExportAsFixedFormat(
                OutputFileName=str(target),
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=1,
                Range=0,
                Item=0,
                IncludeDocProps=False,
                KeepIRM=False,
                CreateBookmarks=0,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False,
            )

        finally:

            if document is not None:
                try:
                    document.Close(False)
                except Exception:
                    pass

            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass

            document = None
            word = None

            gc.collect()

            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _images_to_pdf_under_limit(
    source_paths: list[Path],
    target: Path,
    *,
    max_bytes: int = SMCC_MAX_ATTACHMENT_BYTES,
) -> Path:
    """Convierte una o varias imágenes en un único PDF <= max_bytes."""
    from PIL import Image, ImageOps

    profiles = (
        (2200, 84, 150),
        (2000, 80, 150),
        (1800, 76, 145),
        (1600, 70, 140),
        (1400, 64, 130),
        (1200, 58, 120),
        (1050, 50, 110),
        (900, 44, 100),
        (760, 38, 96),
        (640, 32, 90),
        (540, 26, 84),
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.tmp.pdf")

    for max_side, quality, dpi in profiles:
        pages = []
        try:
            for source in source_paths:
                with Image.open(source) as image:
                    image = ImageOps.exif_transpose(image)
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    else:
                        image = image.copy()
                    image.thumbnail(
                        (max_side, max_side),
                        Image.Resampling.LANCZOS,
                    )
                    pages.append(image)

            if not pages:
                raise RedesNeutrasDocumentError(
                    "No hay imágenes válidas para convertir."
                )

            temp.unlink(missing_ok=True)
            pages[0].save(
                temp,
                "PDF",
                save_all=True,
                append_images=pages[1:],
                resolution=float(dpi),
                quality=int(quality),
                optimize=True,
            )

            if temp.is_file() and temp.stat().st_size <= max_bytes:
                target.unlink(missing_ok=True)
                temp.replace(target)
                return target
        finally:
            for page in pages:
                try:
                    page.close()
                except Exception:
                    pass

    temp.unlink(missing_ok=True)
    raise RedesNeutrasDocumentError(
        "No fue posible comprimir las imágenes a un PDF de máximo 2 MB."
    )


def _compress_pdf_under_limit(
    source: Path,
    target: Path,
    *,
    max_bytes: int = SMCC_MAX_ATTACHMENT_BYTES,
) -> Path:
    """
    Reduce un PDF a <=2 MB rasterizando sus páginas progresivamente.
    Requiere PyMuPDF y Pillow. El original nunca se elimina.
    """
    if source.stat().st_size <= max_bytes:
        return source

    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except Exception as exc:
        raise RedesNeutrasDocumentError(
            "Para comprimir PDF mayores de 2 MB instala PyMuPDF y Pillow: "
            "python -m pip install PyMuPDF Pillow"
        ) from exc

    profiles = (
        (130, 78),
        (120, 72),
        (110, 66),
        (100, 60),
        (90, 54),
        (82, 48),
        (76, 42),
        (70, 36),
        (64, 30),
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.tmp.pdf")

    document = fitz.open(str(source))
    try:
        for dpi, quality in profiles:
            pages = []
            try:
                scale = float(dpi) / 72.0
                matrix = fitz.Matrix(scale, scale)

                for page in document:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    mode = "RGB" if pix.n < 4 else "RGBA"
                    image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    pages.append(image)

                if not pages:
                    raise RedesNeutrasDocumentError(
                        "El PDF no contiene páginas procesables."
                    )

                temp.unlink(missing_ok=True)
                pages[0].save(
                    temp,
                    "PDF",
                    save_all=True,
                    append_images=pages[1:],
                    resolution=float(dpi),
                    quality=int(quality),
                    optimize=True,
                )

                if temp.is_file() and temp.stat().st_size <= max_bytes:
                    target.unlink(missing_ok=True)
                    temp.replace(target)
                    return target
            finally:
                for image in pages:
                    try:
                        image.close()
                    except Exception:
                        pass
    finally:
        document.close()

    temp.unlink(missing_ok=True)
    raise RedesNeutrasDocumentError(
        "No fue posible reducir el PDF por debajo de 2 MB sin degradarlo más."
    )


def _finalize_pdf_under_limit(
    source_pdf: Path, output: Path, suffix: str = "_2mb"
) -> Path:
    if source_pdf.stat().st_size <= SMCC_MAX_ATTACHMENT_BYTES:
        return source_pdf
    target = output / f"{source_pdf.stem}{suffix}.pdf"
    return _compress_pdf_under_limit(source_pdf, target)


def preparar_documento_smcc(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    wo: str = "",
    incident: str = "",
) -> dict[str, Any]:
    """Normaliza una evidencia y garantiza <=2 MB cuando el formato lo permite."""
    source = Path(source_path)
    if not source.is_file():
        raise RedesNeutrasDocumentError(f"Archivo fuente no encontrado: {source}")

    output = Path(output_dir) if output_dir else source.parent
    output.mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower()

    safe_wo = (
        re.sub(r"[^A-Za-z0-9_-]+", "_", str(wo or "SIN_WO")).strip("_") or "SIN_WO"
    )
    safe_inc = re.sub(r"[^A-Za-z0-9_-]+", "_", str(incident or "")).strip("_")
    base_name = f"RN_{safe_wo}" + (f"_{safe_inc}" if safe_inc else "")

    # Imágenes: siempre salen como PDF y siempre <= 2 MB.
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        target = output / f"{base_name}.pdf"
        result = _images_to_pdf_under_limit([source], target)
        info = _bytes_info(result.stat().st_size)
        return {
            "ok": True,
            "codigo": "RN_SMCC_DOCUMENT_READY",
            "source_path": str(source),
            "source_filename": source.name,
            "output_path": str(result),
            "filename": result.name,
            "converted": True,
            "quality": "image_to_pdf_compressed",
            "wo": str(wo or ""),
            "incident": str(incident or ""),
            **info,
        }

    # PDF: conservar si ya cabe; si no, comprimir hasta <=2 MB.
    if ext == ".pdf":
        result = source
        converted = False
        quality = "original"
        if source.stat().st_size > SMCC_MAX_ATTACHMENT_BYTES:
            result = _compress_pdf_under_limit(
                source,
                output / f"{source.stem}_2mb.pdf",
            )
            converted = True
            quality = "pdf_raster_compressed"

        info = _bytes_info(result.stat().st_size)
        return {
            "ok": info["under_max"],
            "codigo": (
                "RN_SMCC_DOCUMENT_READY"
                if info["under_max"]
                else "RN_SMCC_DOCUMENT_TOO_LARGE"
            ),
            "source_path": str(source),
            "source_filename": source.name,
            "output_path": str(result),
            "filename": result.name,
            "converted": converted,
            "quality": quality,
            "wo": str(wo or ""),
            "incident": str(incident or ""),
            **info,
        }

    # Word: si ya pesa <=2 MB puede salir original. Si supera el límite,
    # convertir a PDF y luego comprimir si hace falta.
    if ext in {".doc", ".docx"}:
        if source.stat().st_size <= SMCC_MAX_ATTACHMENT_BYTES:
            info = _bytes_info(source.stat().st_size)
            return {
                "ok": True,
                "codigo": "RN_SMCC_DOCUMENT_READY",
                "source_path": str(source),
                "source_filename": source.name,
                "output_path": str(source),
                "filename": source.name,
                "converted": False,
                "quality": "original",
                "wo": str(wo or ""),
                "incident": str(incident or ""),
                **info,
            }

        target = output / f"{base_name}.pdf"
        target.unlink(missing_ok=True)
        _export_word_pdf(source, target)
        if not target.is_file():
            raise RedesNeutrasDocumentError("Word no generó el PDF.")
        result = _finalize_pdf_under_limit(target, output)
        info = _bytes_info(result.stat().st_size)
        return {
            "ok": info["under_max"],
            "codigo": (
                "RN_SMCC_DOCUMENT_READY"
                if info["under_max"]
                else "RN_SMCC_DOCUMENT_TOO_LARGE"
            ),
            "source_path": str(source),
            "source_filename": source.name,
            "output_path": str(result),
            "filename": result.name,
            "converted": True,
            "quality": "word_to_pdf_compressed" if result != target else "word_to_pdf",
            "conversion_engine": "Microsoft Word",
            "wo": str(wo or ""),
            "incident": str(incident or ""),
            **info,
        }

    # Excel: siempre PDF; si queda grande se comprime.
    if ext in {".xls", ".xlsx", ".xlsm"}:
        target = output / f"{base_name}.pdf"
        target.unlink(missing_ok=True)
        _export_excel_pdf(source, target, minimum_quality=False)
        if not target.is_file():
            raise RedesNeutrasDocumentError("Excel no generó el PDF.")

        # Primero intentar exportación de calidad mínima de Office.
        if target.stat().st_size > SMCC_MAX_ATTACHMENT_BYTES:
            minimum_target = output / f"{base_name}_office_min.pdf"
            minimum_target.unlink(missing_ok=True)
            _export_excel_pdf(source, minimum_target, minimum_quality=True)
            if (
                minimum_target.is_file()
                and minimum_target.stat().st_size < target.stat().st_size
            ):
                target.unlink(missing_ok=True)
                minimum_target.replace(target)
            else:
                minimum_target.unlink(missing_ok=True)

        result = _finalize_pdf_under_limit(target, output)
        info = _bytes_info(result.stat().st_size)
        return {
            "ok": info["under_max"],
            "codigo": (
                "RN_SMCC_DOCUMENT_READY"
                if info["under_max"]
                else "RN_SMCC_DOCUMENT_TOO_LARGE"
            ),
            "source_path": str(source),
            "source_filename": source.name,
            "output_path": str(result),
            "filename": result.name,
            "converted": True,
            "quality": (
                "excel_to_pdf_compressed" if result != target else "excel_to_pdf"
            ),
            "conversion_engine": "Microsoft Excel",
            "wo": str(wo or ""),
            "incident": str(incident or ""),
            **info,
        }

    # Otros formatos: si ya cumplen 2 MB se conservan. Para binarios que
    # superan 2 MB no hay una compresión genérica segura que no los corrompa.
    info = _bytes_info(source.stat().st_size)
    return {
        "ok": info["under_max"],
        "codigo": (
            "RN_SMCC_DOCUMENT_READY"
            if info["under_max"]
            else "RN_SMCC_DOCUMENT_TOO_LARGE_UNSUPPORTED"
        ),
        "source_path": str(source),
        "source_filename": source.name,
        "output_path": str(source),
        "filename": source.name,
        "converted": False,
        "quality": "original",
        "wo": str(wo or ""),
        "incident": str(incident or ""),
        "error": (
            "Formato mayor de 2 MB sin compresión segura disponible."
            if not info["under_max"]
            else ""
        ),
        **info,
    }


# RN_MULTI_IMAGE_PDF_V2_BEGIN
def _rn_consolidar_imagenes_descargadas_pdf(payload: dict, ot: str) -> dict:
    """Si todos los adjuntos descargados son imágenes, crea UN PDF <=2 MB."""
    attachments = list(payload.get("adjuntos") or [])
    if not attachments:
        return payload

    def _is_image(item: dict) -> bool:
        name = str(item.get("nombre") or "").strip().lower()
        return name.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"))

    if not all(isinstance(item, dict) and _is_image(item) for item in attachments):
        return payload

    downloaded = [
        item
        for item in attachments
        if bool(item.get("descargado")) and str(item.get("ruta_local") or "").strip()
    ]
    if len(downloaded) != len(attachments):
        return payload

    source_paths = [Path(str(item["ruta_local"])) for item in downloaded]
    if not all(path.is_file() for path in source_paths):
        return payload

    output_dir = source_paths[0].parent
    safe_ot = re.sub(r"[^A-Za-z0-9_-]+", "_", str(ot or "WO")).strip("_") or "WO"
    pdf_path = output_dir / f"EVIDENCIAS_{safe_ot}.pdf"
    original_names = [str(item.get("nombre") or "") for item in downloaded]

    try:
        reuse_pdf = False
        if pdf_path.is_file() and pdf_path.stat().st_size <= SMCC_MAX_ATTACHMENT_BYTES:
            try:
                import fitz  # PyMuPDF

                with fitz.open(str(pdf_path)) as document:
                    reuse_pdf = len(document) == len(source_paths) and len(document) > 0
            except Exception:
                reuse_pdf = False

        if reuse_pdf:
            print(
                f"[RN PDF] archivo existente reutilizado: {pdf_path} "
                f"({pdf_path.stat().st_size} bytes, {len(source_paths)} paginas)"
            )
        else:
            print(f"[RN PDF] consolidando {len(source_paths)} imagenes: {pdf_path}")
            _images_to_pdf_under_limit(source_paths, pdf_path)

        print(f"[RN PDF] tamaño final: {pdf_path.stat().st_size} bytes")

        pdf_item = dict(downloaded[0])
        pdf_item["nombre"] = pdf_path.name
        pdf_item["ruta_local"] = str(pdf_path)
        pdf_item["descargado"] = True
        pdf_item["error"] = ""
        pdf_item["extension"] = ".pdf"
        pdf_item["mime"] = "application/pdf"
        pdf_item["content_type"] = "application/pdf"

        payload["adjuntos"] = [pdf_item]
        payload["cantidad_documentos_adjuntos"] = 1
        payload["requiere_seleccion"] = False
        payload["pdf_imagenes_consolidado"] = True
        payload["pdf_imagenes_cantidad"] = len(downloaded)
        payload["pdf_imagenes_originales"] = original_names
        payload["pdf_imagenes_archivo"] = str(pdf_path)
        payload["pdf_imagenes_bytes"] = int(pdf_path.stat().st_size)

        if int(payload.get("cantidad_adjuntos_wo") or 0) > 0:
            payload["cantidad_adjuntos_wo"] = 1
        if int(payload.get("cantidad_adjuntos_inc") or 0) > 0:
            payload["cantidad_adjuntos_inc"] = 1

    except Exception as exc:
        payload["pdf_imagenes_consolidado"] = False
        payload["pdf_imagenes_error"] = f"{type(exc).__name__}: {exc}"

    return payload


# RN_MULTI_IMAGE_PDF_V2_END


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sÃ­", "on"}


def _latest_result(ot: str, started_at: float) -> Path | None:
    candidates = sorted(
        RESULT_DIR.glob(f"resultado_{ot}_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.stat().st_mtime >= started_at - 2:
            return candidate
    return None


def _public_file(item: dict[str, Any], ot: str, incident: str) -> dict[str, Any]:
    local_path = Path(str(item.get("ruta_local") or ""))
    filename = local_path.name or str(item.get("nombre") or "")

    source_id = ""
    if local_path.parent and local_path.parent.name:
        source_id = str(local_path.parent.name or "").strip()

    if not source_id:
        source_id = str(item.get("origen_id") or incident or ot).strip()

    source_type = (
        "WO"
        if source_id.upper().startswith("WO")
        else "INC" if source_id.upper().startswith("INC") else "HELIX"
    )

    return {
        "nombre": filename,
        "filename": filename,
        "download_url": (
            f"/api/deco/helix/files/{quote(ot)}/{quote(source_id)}/{quote(filename)}"
        ),
        "size_bytes": local_path.stat().st_size if local_path.is_file() else 0,
        "origen": "HELIX",
        "origen_tipo": source_type,
        "origen_id": source_id,
    }


def _build_response(payload: dict[str, Any]) -> dict[str, Any]:
    ot = str(payload.get("ot") or "")
    code = str(payload.get("codigo") or "HELIX_ERROR")
    incident = str(payload.get("incidente_abierto") or "")
    # RN_MULTI_IMAGE_PDF_V1
    payload = _rn_consolidar_imagenes_descargadas_pdf(
        payload,
        ot,
    )
    attachments = payload.get("adjuntos") or []
    names = [
        str(item.get("nombre") or "") for item in attachments if item.get("nombre")
    ]
    downloaded = [item for item in attachments if item.get("descargado")]

    if code == "HELIX_OT_NO_ENCONTRADA":
        response_text = f"Helix no encontrÃ³ la orden de trabajo {ot}."
    elif code == "HELIX_SIN_INCIDENTES_RELACIONADOS":
        response_text = (
            f"Helix encontrÃ³ {ot}, pero la orden no tiene incidentes relacionados."
        )
    elif code == "HELIX_SIN_DOCUMENTOS_ADJUNTOS":
        response_text = (
            f"Helix encontrÃ³ {ot}"
            + (f" y el incidente {incident}" if incident else "")
            + ", pero no encontrÃ³ documentos adjuntos."
        )
    elif code == "HELIX_DOCUMENTOS_ADJUNTOS_DETECTADOS":
        listing = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
        response_text = (
            f"Helix encontrÃ³ {len(names)} documento(s) para {ot}"
            + (f" en {incident}" if incident else "")
            + ":\n\n"
            + listing
        )
        if len(names) == 1:
            response_text += (
                f'\n\nPara descargarlo escribe: descargar helix {ot} "{names[0]}"'
            )
        elif names:
            response_text += (
                f"\n\nIndica cuÃ¡l deseas descargar, por ejemplo: "
                f'descargar helix {ot} "{names[0]}"'
            )
    elif code == "HELIX_REQUIERE_SELECCION_ADJUNTO":
        listing = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
        response_text = (
            f"Helix encontrÃ³ varios documentos para {ot}. Selecciona uno:\n\n{listing}"
        )
    elif code == "HELIX_ADJUNTOS_DESCARGADOS":
        response_text = (
            f"Documento(s) descargado(s) desde Helix para {ot}: "
            + ", ".join(str(item.get("nombre") or "") for item in downloaded)
        )
    else:
        response_text = (
            str(payload.get("error") or "")
            or f"No fue posible completar la consulta de {ot} en Helix."
        )

    files = [_public_file(item, ot, incident) for item in downloaded]

    # RN_SMCC_NORMALIZE_ALL_V1
    # Todo archivo que se publica pasa por el normalizador. Imágenes -> PDF;
    # PDF/Word/Excel grandes -> salida <=2 MB cuando técnicamente es posible.
    smcc_files: list[dict[str, Any]] = []
    smcc_documentos: list[dict[str, Any]] = []

    for item in downloaded:
        local_path = Path(str(item.get("ruta_local") or ""))
        if not local_path.is_file():
            continue

        try:
            prepared = preparar_documento_smcc(
                local_path,
                local_path.parent,
                wo=ot,
                incident=incident,
            )
            smcc_documentos.append(prepared)

            output_path = Path(str(prepared.get("output_path") or ""))
            if prepared.get("ok") and output_path.is_file():
                prepared_item = dict(item)
                prepared_item["ruta_local"] = str(output_path)
                prepared_item["nombre"] = output_path.name
                smcc_files.append(_public_file(prepared_item, ot, incident))
            else:
                # Si sigue sobre 2 MB no se publica en `archivos`; el detalle
                # queda en `documentos_smcc` para no entregar un archivo inválido.
                pass

        except Exception as exc:
            smcc_documentos.append(
                {
                    "ok": False,
                    "codigo": "RN_SMCC_DOCUMENT_ERROR",
                    "source_path": str(local_path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    # `archivos` contiene únicamente salidas listas y <= 2 MB.
    files = smcc_files

    return {
        "ok": bool(payload.get("ok")),
        "tipo_respuesta": "helix_redes_neutras",
        "codigo": code,
        "origen": "HELIX",
        "respuesta": response_text,
        "ot": ot,
        "ot_encontrada": bool(payload.get("ot_encontrada")),
        "incidente": incident,
        "incidentes_relacionados": payload.get("incidentes_relacionados") or [],
        "documentos_adjuntos_encontrados": bool(
            payload.get("documentos_adjuntos_encontrados")
        ),
        "cantidad_documentos_adjuntos": int(
            payload.get("cantidad_documentos_adjuntos") or 0
        ),
        "requiere_seleccion": bool(payload.get("requiere_seleccion")),
        "adjuntos": attachments,
        "archivos": files,
        "documentos_smcc": smcc_documentos,
        "duracion_seg": payload.get("duracion_seg") or 0,
        "error": payload.get("error") or "",
    }


def _adquirir_turno_helix(*, es_reintento: bool) -> int | None:
    """
    Reserva un slot del pool de workers Helix.

    - Consultas normales: FIFO real entre ellas.
    - Reintentos: prioridad menor; solo entran cuando no hay consultas
      normales esperando.
    - Retorna el worker_id reservado o None si vence la espera.
    """
    global _HELIX_ACTIVE

    token = object()
    deadline = time.monotonic() + _QUEUE_TIMEOUT_SECONDS

    with _HELIX_GATE:
        if not es_reintento:
            _NORMAL_QUEUE.append(token)

        while True:
            hay_worker = bool(_HELIX_FREE_SLOTS)

            if es_reintento:
                puede_entrar = hay_worker and not _NORMAL_QUEUE
            else:
                puede_entrar = (
                    hay_worker and bool(_NORMAL_QUEUE) and _NORMAL_QUEUE[0] is token
                )

            if puede_entrar:
                if not es_reintento:
                    _NORMAL_QUEUE.popleft()

                worker_id = _HELIX_FREE_SLOTS.popleft()
                _HELIX_ACTIVE += 1

                print(
                    f"[RN WORKER] toma worker={worker_id} "
                    f"activos={_HELIX_ACTIVE}/{_HELIX_MAX_WORKERS}"
                )

                _HELIX_GATE.notify_all()
                return worker_id

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not es_reintento:
                    try:
                        _NORMAL_QUEUE.remove(token)
                    except ValueError:
                        pass
                _HELIX_GATE.notify_all()
                return None

            _HELIX_GATE.wait(timeout=min(0.5, remaining))


def _liberar_turno_helix(worker_id: int) -> None:
    global _HELIX_ACTIVE

    with _HELIX_GATE:
        if worker_id not in _HELIX_FREE_SLOTS:
            _HELIX_FREE_SLOTS.append(worker_id)

        _HELIX_ACTIVE = max(0, _HELIX_ACTIVE - 1)

        print(
            f"[RN WORKER] libera worker={worker_id} "
            f"activos={_HELIX_ACTIVE}/{_HELIX_MAX_WORKERS}"
        )

        _HELIX_GATE.notify_all()


def _ejecutar_helix_una_vez(
    ot: str,
    *,
    descargar: bool = False,
    archivo: str = "",
    todos: bool = False,
    es_reintento: bool = False,
) -> dict[str, Any]:
    """Ejecuta una consulta usando un slot del pool de workers Helix."""
    ot_match = _WO_RE.fullmatch(str(ot or "").strip())
    if not ot_match:
        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": "HELIX_OT_INVALIDA",
            "origen": "HELIX",
            "respuesta": "La orden Helix debe tener el formato WO seguido de 13 o 14 dígitos.",
            "ot": str(ot or ""),
            "adjuntos": [],
            "archivos": [],
        }

    worker_id = _adquirir_turno_helix(es_reintento=bool(es_reintento))
    if worker_id is None:
        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": "HELIX_QUEUE_TIMEOUT",
            "origen": "HELIX",
            "respuesta": (
                "La consulta agotó el tiempo máximo esperando "
                "un worker disponible de Helix."
            ),
            "ot": ot_match.group(1).upper(),
            "adjuntos": [],
            "archivos": [],
        }

    started_at = time.time()
    normalized_ot = ot_match.group(1).upper()

    print(
        f"[RN {'RETRY' if es_reintento else 'QUEUE'}] "
        f"{normalized_ot} toma worker={worker_id}"
    )

    try:
        args = argparse.Namespace(
            ot=normalized_ot,
            incidente="",
            descargar=bool(descargar),
            archivo=str(archivo or "").strip(),
            todos=bool(todos),
            headless=_as_bool("HELIX_CHAT_HEADLESS", False),
            mantener_abierto=0,
            worker_id=worker_id,
        )

        asyncio.run(main_async(args))

        direct_result = Path(str(getattr(args, "result_path", "") or ""))
        result_file = (
            direct_result
            if direct_result.is_file()
            else _latest_result(normalized_ot, started_at)
        )
        if not result_file:
            raise RuntimeError(
                "Helix terminó sin generar el archivo JSON de resultado."
            )

        payload = json.loads(result_file.read_text(encoding="utf-8"))
        return _build_response(payload)

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        credentials_invalid = "HELIX_CREDENCIALES_INVALIDAS" in str(exc)
        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": (
                "HELIX_CREDENCIALES_INVALIDAS"
                if credentials_invalid
                else "HELIX_SERVICE_ERROR"
            ),
            "origen": "HELIX",
            "respuesta": (
                "⚠ Helix rechazó las credenciales configuradas. Se requiere actualizar el usuario de SmartIT."
                if credentials_invalid
                else "No fue posible completar la consulta en Helix."
            ),
            "requiere_actualizar_credenciales": credentials_invalid,
            "ot": normalized_ot,
            "adjuntos": [],
            "archivos": [],
            "error": error_text,
        }
    finally:
        _liberar_turno_helix(worker_id)


def _encolar_reintento(
    ot: str,
    *,
    descargar: bool,
    archivo: str,
    todos: bool,
) -> bool:
    global _RETRY_THREAD_STARTED

    if not _RETRY_ENABLED:
        return False

    normalized_ot = str(ot or "").strip().upper()
    if not _WO_RE.fullmatch(normalized_ot):
        return False

    with _RETRY_LOCK:
        if normalized_ot in _RETRY_PENDING:
            return False

        _RETRY_PENDING.add(normalized_ot)
        _RETRY_QUEUE.put(
            {
                "ot": normalized_ot,
                "descargar": bool(descargar),
                "archivo": str(archivo or "").strip(),
                "todos": bool(todos),
            }
        )

        if not _RETRY_THREAD_STARTED:
            thread = threading.Thread(
                target=_retry_worker_mismo_navegador,
                daemon=True,
                name="helix-retry-queue",
            )
            thread.start()
            _RETRY_THREAD_STARTED = True

    print(f"[RN RETRY QUEUE] {normalized_ot} encolada")
    return True


def _retry_worker_mismo_navegador() -> None:
    """
    Reintenta en segundo plano usando el mismo pool de workers/perfiles.
    Los reintentos tienen menor prioridad que las consultas nuevas.
    Nunca excede HELIX_WORKERS Chromium simultáneos.
    """
    while True:
        item = _RETRY_QUEUE.get()
        ot = str(item.get("ot") or "").strip().upper()

        try:
            for intento in range(1, _RETRY_MAX_ATTEMPTS + 1):
                # Dar prioridad práctica a las WOs nuevas que llegan justo
                # después de que falló la anterior.
                time.sleep(_RETRY_DELAY_SECONDS)

                if _RETRY_IDLE_GRACE_SECONDS:
                    print(
                        f"[RN RETRY] {ot} esperando ventana de gracia "
                        f"({_RETRY_IDLE_GRACE_SECONDS}s)"
                    )
                    time.sleep(_RETRY_IDLE_GRACE_SECONDS)

                print(f"[HELIX RETRY] {ot} intento {intento}/{_RETRY_MAX_ATTEMPTS}")
                resultado = _ejecutar_helix_una_vez(
                    ot,
                    descargar=bool(item.get("descargar")),
                    archivo=str(item.get("archivo") or ""),
                    todos=bool(item.get("todos")),
                    es_reintento=True,
                )

                if bool(resultado.get("ok")):
                    print(f"[HELIX RETRY OK] {ot} intento {intento}")
                    break

                codigo = str(resultado.get("codigo") or "")
                print(f"[HELIX RETRY FAIL] {ot} codigo={codigo}")

                if codigo not in _RETRYABLE_CODES:
                    break
            else:
                print(
                    f"[HELIX RETRY AGOTADO] {ot} después de {_RETRY_MAX_ATTEMPTS} intentos"
                )

        except Exception as exc:
            print(f"[HELIX RETRY ERROR] {ot}: {type(exc).__name__}: {exc}")
        finally:
            with _RETRY_LOCK:
                _RETRY_PENDING.discard(ot)
            _RETRY_QUEUE.task_done()


def consultar_redes_neutras_helix(
    ot: str,
    *,
    descargar: bool = False,
    archivo: str = "",
    todos: bool = False,
) -> dict[str, Any]:
    resultado = _ejecutar_helix_una_vez(
        ot,
        descargar=descargar,
        archivo=archivo,
        todos=todos,
    )

    codigo = str(resultado.get("codigo") or "")
    if not bool(resultado.get("ok")) and codigo in _RETRYABLE_CODES:
        programado = _encolar_reintento(
            ot,
            descargar=descargar,
            archivo=archivo,
            todos=todos,
        )
        resultado["reintento_programado"] = bool(programado)
        resultado["reintentos_maximos"] = _RETRY_MAX_ATTEMPTS
        # Se conserva la clave legacy para no romper consumidores antiguos.
        resultado["retry_mismo_worker"] = _HELIX_MAX_WORKERS == 1
        resultado["retry_mismo_pool"] = True
        if programado:
            resultado["respuesta_reintento"] = (
                "La WO quedó al final de la cola para reintento. "
                "Usará un worker libre del mismo pool sin superar HELIX_WORKERS."
            )

    return resultado


def helix_health() -> dict[str, Any]:
    try:
        import playwright  # noqa: F401

        playwright_ok = True
    except Exception:
        playwright_ok = False

    return {
        "loaded": True,
        "playwright_loaded": playwright_ok,
        "credentials_configured": bool(
            os.getenv("SMARTIT_USER", "").strip() and os.getenv("SMARTIT_PASSWORD", "")
        ),
        "headless": _as_bool("HELIX_CHAT_HEADLESS", False),
        "workers": int(_HELIX_MAX_WORKERS),
        "active_workers": int(_HELIX_ACTIVE),
        "available_workers": int(len(_HELIX_FREE_SLOTS)),
        "worker_busy": bool(_HELIX_ACTIVE),
        "normal_queue_size": len(_NORMAL_QUEUE),
        "retry_queue": {
            "enabled": bool(_RETRY_ENABLED),
            "queue_size": int(_RETRY_QUEUE.qsize()),
            "pending": sorted(_RETRY_PENDING),
            "max_attempts": int(_RETRY_MAX_ATTEMPTS),
            "delay_seconds": int(_RETRY_DELAY_SECONDS),
            "idle_grace_seconds": int(_RETRY_IDLE_GRACE_SECONDS),
            "same_worker": _HELIX_MAX_WORKERS == 1,
            "same_pool": True,
        },
        "downloads_dir": str(DOWNLOAD_DIR),
        "credentials_alert": get_credentials_alert(),
    }


helix_downloads_dir = DOWNLOAD_DIR
