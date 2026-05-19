"""LangGraph Collector node — mock senaryo veya upload verisini normalize eder."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.core.state import IadeAjanState
from app.services import ai_converter, scenario_loader, upload_loader
from app.services.document_classifier import classify_document, is_supported_document_path
from app.services.document_inventory import classification_to_inventory_entry, upsert_inventory


def _log(message: str) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{ts}] [CollectorAgent] {message}"


def _resolve_scenario_id(state: IadeAjanState) -> str:
    return (
        state.get("scenario_id")
        or state.get("company_id")
        or "celik_as_high"
    )


def _normalize_invoice(raw: dict[str, Any], index: int) -> dict[str, Any]:
    # Upload path'de IngestionInvoice şeması is_export alanı içermez; type
    # alanından türeterek _detect_refund_type'ın doğru çalışmasını sağla.
    invoice_type = raw.get("type") or "belirsiz"
    is_export = bool(raw.get("is_export", False)) or (invoice_type == "ihracat")
    return {
        "id": raw.get("id") or f"UNKNOWN-{index}",
        "type": invoice_type,
        "date": raw.get("date") or "",
        "amount": float(raw.get("amount", 0.0)),
        "kdv_amount": float(raw.get("kdv_amount", 0.0)),
        "kdv_rate": int(raw.get("kdv_rate", 20)),
        "supplier_id": raw.get("supplier_id"),
        "is_export": is_export,
        "has_customs_declaration": bool(raw.get("has_customs_declaration", False)),
        "is_tevkifat": bool(raw.get("is_tevkifat", False)),
        "has_tevkifat_declaration": bool(raw.get("has_tevkifat_declaration", False)),
        "tevkifat_rate": raw.get("tevkifat_rate"),
        "period": raw.get("period") or "",
        "currency": raw.get("currency") or "TRY",
    }


def _normalize_supplier(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Tedarikçi normalize — Excel yüklemesinde olmayan alanlara varsayılan atama yapılmaz."""
    supplier: dict[str, Any] = {
        "id": raw.get("id") or f"SUP-UNKNOWN-{index}",
        "name": raw.get("name") or "",
        "tax_number": raw.get("tax_number") or "",
        "risk_level": raw.get("risk_level") or "düşük",
        "is_active": bool(raw.get("is_active", True)),
        "risk_note": raw.get("risk_note") or "",
    }
    for quarter_field in (
        "has_filed_kdv_2025_q1",
        "has_filed_kdv_2025_q2",
        "has_filed_kdv_2025_q3",
    ):
        if quarter_field in raw:
            supplier[quarter_field] = bool(raw[quarter_field])
    return supplier


def _normalize_document(raw: dict[str, Any], index: int) -> dict[str, Any]:
    normalized = {
        "id": raw.get("id") or f"DOC-UNKNOWN-{index}",
        "type": raw.get("type") or "",
        "status": raw.get("status") or "bilinmiyor",
        "required": bool(raw.get("required", True)),
        "note": raw.get("note") or "",
    }
    for key, value in raw.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _detect_refund_type(
    normalized_invoices: list[dict[str, Any]],
) -> dict[str, Any]:
    export_count = sum(1 for inv in normalized_invoices if inv["is_export"])
    tevkifat_count = sum(1 for inv in normalized_invoices if inv["is_tevkifat"])

    if export_count > 0 and tevkifat_count == 0:
        refund_type, confidence = "ihracat", "yüksek"
    elif tevkifat_count > 0 and export_count == 0:
        refund_type, confidence = "tevkifat", "yüksek"
    elif export_count > 0 and tevkifat_count > 0:
        refund_type, confidence = "belirsiz", "orta"
    else:
        refund_type, confidence = "belirsiz", "düşük"

    return {
        "refund_type": refund_type,
        "export_invoice_count": export_count,
        "tevkifat_invoice_count": tevkifat_count,
        "total_invoice_count": len(normalized_invoices),
        "detection_confidence": confidence,
    }


def _build_collected_data(
    scenario_id: str,
    normalized_invoices: list[dict[str, Any]],
    normalized_suppliers: list[dict[str, Any]],
    document_inventory: list[dict[str, Any]],
    classification_result: dict[str, Any],
) -> dict[str, Any]:
    total_sales = sum(
        inv["amount"] for inv in normalized_invoices if inv["type"] == "satis"
    )
    total_purchase = sum(
        inv["amount"] for inv in normalized_invoices if inv["type"] == "alis"
    )
    return {
        "scenario_id": scenario_id,
        "invoice_count": len(normalized_invoices),
        "supplier_count": len(normalized_suppliers),
        "document_count": len(document_inventory),
        "total_sales_amount": total_sales,
        "total_purchase_amount": total_purchase,
        "export_invoice_count": classification_result["export_invoice_count"],
        "tevkifat_invoice_count": classification_result["tevkifat_invoice_count"],
    }


def _resolve_started_at(state: IadeAjanState) -> str:
    existing = state.get("started_at")
    if existing:
        return existing
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _try_load_upload_payload(
    file_path: str,
    logs: list[str],
) -> dict[str, Any] | None:
    """Upload dosyasını okur; başarılıysa kanonik payload döner. Belge modunda None."""
    logs.append(_log(f"Upload modu aktif: {file_path}"))
    loaded = upload_loader.load_uploaded_file(file_path)

    if loaded["mode"] == "document":
        logs.append(
            _log(
                "Belge dosyası (PDF/görsel) — tabular canonical burada üretilmez; "
                "BelgeAnlama ile envantere eklenir."
            )
        )
        return None

    if loaded["mode"] == "canonical":
        canonical = loaded["canonical"]
        invoice_count = len(canonical.get("invoices", []))
        logs.append(_log(f"Upload okundu: canonical — {invoice_count} kayıt"))
        return canonical

    tabular = loaded["tabular"]
    canonical = ai_converter.convert_tabular_to_canonical(
        rows=tabular["rows"],
        columns=tabular["columns"],
        source_name=tabular.get("source_name", ""),
    )
    logs.append(
        _log("Tabular veri canonical formata çevrildi (heuristic/llm)")
    )
    invoice_count = len(canonical.get("invoices", []))
    logs.append(_log(f"Upload okundu: tabular — {invoice_count} kayıt"))
    return canonical


def _merge_llm_documents_into_inventory(
    uploaded_files: list[str],
    document_inventory: list[dict[str, Any]],
    logs: list[str],
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """PDF/görsel belgeleri birleşik doğrulama pipeline ile envantere ekler."""
    from app.services.verification.document_verification import ingest_document_file

    inv = list(document_inventory)
    state = state or {}
    for fp in uploaded_files:
        if not is_supported_document_path(fp):
            continue
        record, entry = ingest_document_file(fp, state=state)
        if entry:
            inv = upsert_inventory(inv, entry)
            logs.append(
                _log(
                    f"[BelgeAnlama] {fp} → verified {entry.get('type')} "
                    f"(status={record.verification_status})"
                )
            )
        else:
            cls_result = classify_document(fp)
            entry_legacy = classification_to_inventory_entry(cls_result) if cls_result else None
            if entry_legacy:
                entry_legacy["source"] = "llm_classifier"
                entry_legacy["verification_status"] = "unverified"
            inv = upsert_inventory(inv, entry_legacy)
            logs.append(
                _log(
                    f"[BelgeAnlama] {fp}: doğrulama geçmedi "
                    f"({record.verification_status})"
                )
            )
    return inv


def _load_scenario_payload(scenario_id: str) -> dict[str, Any]:
    scenario = scenario_loader.load_scenario(scenario_id)
    return {
        "company_profile": scenario["company_profile"],
        "invoices": scenario["invoices"],
        "suppliers": scenario["suppliers"],
        "documents": scenario["documents"],
    }


def _enrich_company_profile_from_upload(
    company_profile: dict[str, Any],
    normalized_invoices: list[dict[str, Any]],
    normalized_suppliers: list[dict[str, Any]],
    upload_path: str | None,
) -> dict[str, Any]:
    """Excel/CSV yüklemesinde boş company_profile'ı faturalardan türetir."""
    profile = dict(company_profile)
    if profile.get("company_name") and profile.get("tax_number"):
        return profile

    supplier_name = ""
    supplier_vkn = ""
    if normalized_suppliers:
        supplier_name = str(normalized_suppliers[0].get("name") or "").strip()
        supplier_vkn = str(normalized_suppliers[0].get("tax_number") or "").strip()

    if not profile.get("company_name"):
        if supplier_name:
            profile["company_name"] = supplier_name
        elif upload_path:
            stem = upload_path.rsplit("/", 1)[-1]
            if "." in stem:
                stem = stem.rsplit(".", 1)[0]
            profile["company_name"] = f"Yüklenen dosya — {stem}"
        else:
            profile["company_name"] = "Yüklenen dosya"

    if not profile.get("tax_number"):
        for inv in normalized_invoices:
            vkn = str(inv.get("supplier_id") or inv.get("supplier_tax_number") or "")
            digits = vkn.replace(".", "").replace(" ", "")
            if digits.isdigit() and 10 <= len(digits) <= 11:
                profile["tax_number"] = digits
                break
        if not profile.get("tax_number") and supplier_vkn:
            digits = supplier_vkn.replace(".", "").replace(" ", "")
            if digits.isdigit():
                profile["tax_number"] = digits

    if not profile.get("estimated_refund_amount"):
        profile["estimated_refund_amount"] = sum(
            abs(float(inv.get("amount") or 0)) for inv in normalized_invoices
        )

    dates = [
        str(inv.get("date", ""))[:10]
        for inv in normalized_invoices
        if inv.get("date") and len(str(inv.get("date"))) >= 10
    ]
    if dates and not profile.get("analysis_period"):
        profile["analysis_period"] = {"start": min(dates), "end": max(dates)}

    return profile


def _build_collector_output(
    state: IadeAjanState,
    payload: dict[str, Any],
    scenario_id: str,
    logs: list[str],
    error_state: str | None,
    from_scenario: bool,
) -> dict[str, Any]:
    company_profile = payload.get("company_profile") or {}
    raw_invoices = payload.get("invoices") or []
    raw_suppliers = payload.get("suppliers") or []
    raw_documents = payload.get("documents") or []

    if from_scenario:
        logs.append(
            _log(
                f"Senaryo yüklendi — {len(raw_invoices)} fatura, "
                f"{len(raw_suppliers)} tedarikçi, {len(raw_documents)} belge"
            )
        )

    normalized_invoices = [
        _normalize_invoice(raw, index) for index, raw in enumerate(raw_invoices)
    ]
    logs.append(_log("Faturalar normalize edildi"))

    normalized_suppliers = [
        _normalize_supplier(raw, index) for index, raw in enumerate(raw_suppliers)
    ]
    logs.append(_log("Tedarikçiler normalize edildi"))

    document_inventory = [
        _normalize_document(raw, index) for index, raw in enumerate(raw_documents)
    ]
    if from_scenario:
        for doc in document_inventory:
            doc.setdefault("source", "scenario")
            doc.setdefault("verification_status", "passed")
    logs.append(_log("Belgeler normalize edildi"))

    uploaded_all = list(state.get("uploaded_files") or [])
    process_warnings = list(state.get("process_warnings") or [])
    merge_ctx = {
        "tax_number": company_profile.get("tax_number", ""),
        "company_id": company_profile.get("company_id", ""),
        "date_range": {
            "start": (company_profile.get("analysis_period") or {}).get("start", ""),
            "end": (company_profile.get("analysis_period") or {}).get("end", ""),
        },
        "process_warnings": process_warnings,
    }
    document_inventory = _merge_llm_documents_into_inventory(
        uploaded_all, document_inventory, logs, state=merge_ctx
    )

    if not from_scenario:
        upload_path = None
        tabular_uploads = [
            p for p in uploaded_all if upload_loader.is_tabular_or_json_path(p)
        ]
        if tabular_uploads:
            upload_path = tabular_uploads[0]
        elif uploaded_all:
            upload_path = str(uploaded_all[0])
        company_profile = _enrich_company_profile_from_upload(
            company_profile,
            normalized_invoices,
            normalized_suppliers,
            upload_path,
        )
        if company_profile.get("company_name"):
            logs.append(
                _log(f"Şirket profili (upload): {company_profile.get('company_name')}")
            )

    classification_result = _detect_refund_type(normalized_invoices)
    company_profile["refund_type"] = classification_result["refund_type"]
    company_profile["refund_type_source"] = "collector_detection"
    logs.append(
        _log(
            f"İade türü tespit edildi: {classification_result['refund_type']} "
            f"(güven: {classification_result['detection_confidence']})"
        )
    )

    effective_scenario_id = (
        company_profile.get("company_id")
        or scenario_id
    )

    collected_data = _build_collected_data(
        effective_scenario_id,
        normalized_invoices,
        normalized_suppliers,
        document_inventory,
        classification_result,
    )

    analysis_period = company_profile.get("analysis_period", {})
    date_range = {
        "start": analysis_period.get("start", "2025-01-01"),
        "end": analysis_period.get("end", "2025-09-30"),
    }

    logs.append(_log("Collector tamamlandı → AnalyzerAgent'a geçiliyor"))

    return {
        "company_id": company_profile.get("company_id", effective_scenario_id),
        "company_name": company_profile.get("company_name", ""),
        "tax_number": company_profile.get("tax_number", ""),
        "date_range": date_range,
        "collected_data": collected_data,
        "normalized_invoices": normalized_invoices,
        "normalized_suppliers": normalized_suppliers,
        "document_inventory": document_inventory,
        "company_profile": company_profile,
        "classification_result": classification_result,
        "process_warnings": list(merge_ctx.get("process_warnings") or process_warnings),
        "current_agent": "AnalyzerAgent",
        "analysis_status": "running",
        "agent_logs": logs,
        "error_state": error_state,
        "started_at": _resolve_started_at(state),
    }


def collector_node(state: IadeAjanState) -> dict[str, Any]:
    """Mock senaryo veya upload verisini yükler, normalize eder ve state güncellemesi döner."""
    scenario_id = _resolve_scenario_id(state)
    logs: list[str] = []
    error_state: str | None = None
    payload: dict[str, Any] | None = None
    from_scenario = False

    uploaded_files = state.get("uploaded_files") or []
    if uploaded_files:
        tabular_paths = [
            p for p in uploaded_files if upload_loader.is_tabular_or_json_path(p)
        ]
        for tp in tabular_paths:
            try:
                cand = _try_load_upload_payload(tp, logs)
                if cand is not None:
                    payload = cand
                    error_state = None
                    break
            except (RuntimeError, ValueError, Exception) as exc:
                error_state = f"COLLECTOR_UPLOAD_ERROR: {exc}"
                logs.append(
                    _log(f"[Fallback] Upload başarısız ({tp}), sonraki denenecek: {exc}")
                )
        if tabular_paths and payload is None:
            allow_mock = os.getenv("ALLOW_MOCK_FALLBACK", "").lower() in ("1", "true")
            if not allow_mock:
                logs.append(_log("[Güvenlik] Upload başarısız — mock fallback kapalı"))
                return {
                    "error_state": error_state or "COLLECTOR_UPLOAD_FAILED",
                    "analysis_status": "failed",
                    "failure_reason": "UPLOAD_FAILED",
                    "current_agent": "CollectorAgent",
                    "agent_logs": logs,
                }
            logs.append(
                _log("[Fallback] Tüm tabular yüklemeler başarısız, senaryoya dönülüyor")
            )

    if payload is None:
        allow_mock = os.getenv("ALLOW_MOCK_FALLBACK", "").lower() in ("1", "true")
        if uploaded_files and not allow_mock:
            return {
                "error_state": error_state or "COLLECTOR_UPLOAD_FAILED",
                "analysis_status": "failed",
                "failure_reason": "UPLOAD_FAILED",
                "current_agent": "CollectorAgent",
                "agent_logs": logs,
            }
        logs.append(_log(f"Senaryo yükleniyor: {scenario_id}"))
        try:
            payload = _load_scenario_payload(scenario_id)
            from_scenario = True
        except RuntimeError as exc:
            logs.append(_log(f"HATA: {exc}"))
            return {
                "error_state": f"COLLECTOR_ERROR: {exc}",
                "analysis_status": "failed",
                "failure_reason": "COLLECTOR_ERROR",
                "current_agent": "CollectorAgent",
                "agent_logs": logs,
            }

    return _build_collector_output(
        state=state,
        payload=payload,
        scenario_id=scenario_id,
        logs=logs,
        error_state=error_state,
        from_scenario=from_scenario,
    )


if __name__ == "__main__":
    import os

    print("=" * 55)
    print("Test 1 — Senaryo: celik_as_high")
    print("=" * 55)

    out = collector_node({"scenario_id": "celik_as_high", "agent_logs": []})

    assert out["analysis_status"] == "running"
    assert out["current_agent"] == "AnalyzerAgent"
    assert out["classification_result"]["refund_type"] == "ihracat"
    assert len(out["normalized_invoices"]) == 15
    assert len(out["normalized_suppliers"]) == 8
    assert out["error_state"] is None

    print("✅ Senaryo testi geçti")
    print(f"  İade türü    : {out['classification_result']['refund_type']}")
    print(f"  Güven        : {out['classification_result']['detection_confidence']}")
    print(f"  Fatura sayısı: {len(out['normalized_invoices'])}")
    print(f"  Tedarikçi    : {len(out['normalized_suppliers'])}")
    print(f"  Belge        : {len(out['document_inventory'])}")
    print(f"  Sonraki ajan : {out['current_agent']}")
    print(f"  Log sayısı   : {len(out['agent_logs'])}")

    print()
    out_err = collector_node({"scenario_id": "OLMAYAN_SENARYO", "agent_logs": []})
    assert out_err["analysis_status"] == "failed"
    assert "COLLECTOR_ERROR" in out_err["error_state"]
    print("✅ Hata senaryosu geçti")
    print(f"  Hata: {out_err['error_state']}")

    print()
    print("=" * 55)
    print("Test 3 — Upload modu (demo_samples/senaryo_a_ihracat.json)")
    print("=" * 55)

    demo_path = "demo_samples/senaryo_a_ihracat.json"
    if os.path.exists(demo_path):
        out_upload = collector_node({
            "uploaded_files": [demo_path],
            "agent_logs": [],
        })
        print(f"  Status       : {out_upload['analysis_status']}")
        print(f"  Fatura sayısı: {len(out_upload['normalized_invoices'])}")
        print(f"  Sonraki ajan : {out_upload['current_agent']}")

        out_fallback = collector_node({
            "uploaded_files": ["OLMAYAN_DOSYA.csv"],
            "agent_logs": [],
        })
        fb_logged = any("[Fallback]" in log for log in out_fallback["agent_logs"])
        assert fb_logged, "Fallback logu görülmeli"
        assert out_fallback["analysis_status"] == "running"
        assert out_fallback["error_state"]
        print("✅ Upload + Fallback testi geçti")
    else:
        print(f"  ⚠️  {demo_path} bulunamadı — test atlandı.")
        print("  demo_samples/ klasörüne bir JSON dosyası ekleyerek test edebilirsin.")

    print("=" * 55)
