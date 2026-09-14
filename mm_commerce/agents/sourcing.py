"""SOURCING — proveedores REALES; hard MATCH%; never invent; per-line isolation."""
from __future__ import annotations

import json
from typing import Any

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.mercadolibre import search_public
from mm_commerce.connectors.real_suppliers import search_real_pages
from mm_commerce.isolation import (
    MatchSession,
    assert_no_cross_contamination,
)
from mm_commerce.matching import (
    TECH_EXACTO,
    TECH_EQUIV,
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    CLASS_NO_CUMPLE,
    CLASS_NO_VER,
    VERIFIED_TECH,
    build_line_spec,
    full_spec_blob,
    infer_product_type,
    match_line_to_candidate,
)
from mm_commerce.models import Opportunity, Supplier, SupplierQuote, Tender
from mm_commerce.timing import now_ba


def _search_strategies(line_spec) -> list[tuple[str, str]]:
    """Up to 5 distinct strategies derived from HARD_REQUIREMENTS (no new sources)."""
    hard = {r.key: r for r in line_spec.hard_requirements}
    brand = line_spec.brand or ""
    model = line_spec.model or ""
    ptype = line_spec.product_type
    strategies: list[tuple[str, str]] = []

    # 1) Tech description / full normalized
    strategies.append(("tech_desc", line_spec.normalized_spec[:240]))

    # 2) Brand + model
    if brand or model:
        strategies.append(("brand_model", f"{brand} {model} {ptype}".strip()))

    # 3) Official distributor seeds (category via full hard blob)
    seed_q_parts = [ptype.replace("_", " ")]
    for key in ("model", "brand", "split_ratio", "nap_identity", "wifi_tech", "capacity_va"):
        if key in hard:
            seed_q_parts.append(hard[key].required)
    strategies.append(("official_dist", " ".join(seed_q_parts)[:200]))

    # 4) AR wholesalers (ML-oriented short query)
    ml_bits = [brand, model]
    if "split_ratio" in hard:
        ml_bits.append(hard["split_ratio"].required)
    if "capacity_va" in hard:
        ml_bits.append(hard["capacity_va"].required)
    if ptype:
        ml_bits.append(ptype.replace("_", " "))
    strategies.append(("ar_wholesale", " ".join(x for x in ml_bits if x)[:120]))

    # 5) Nomenclature variants
    variants: list[str] = []
    if model:
        variants.append(model)
        variants.append(model.replace("-", ""))
        variants.append(model.replace("-", " "))
    if "FO4075" in (model or "").upper() or "FO-4075" in (line_spec.normalized_spec or "").upper():
        variants.extend(["FO-4075", "FO4075", "PLC SPLITTER 1x16 SC/APC GLC"])
    if "FDB" in (model or "").upper():
        variants.extend(["GLC-FDB-012-01", "caja NAP 1x8 SC/APC"])
    if "9163" in (model or "").upper() or "meraki" in line_spec.normalized_spec.lower():
        variants.extend(["Catalyst 9163E", "Meraki Wi-Fi 6E Outdoor AP", "Cisco 9163E Meraki"])
    if "AP217" in (model or "").upper():
        variants.extend(["WI-AP217-Lite", "Wi-Tek access point interior"])
    if "UPS3500" in (model or "").upper() or "3000" in line_spec.normalized_spec:
        variants.extend(["Atomlux UPS3500 3500VA", "UPS 3000 VA Atomlux"])
    if "ODF" in ptype or "odf" in line_spec.normalized_spec.lower():
        variants.extend(["ODF 12 puertos SC/APC", "caja de empalme fibra 12 puertos"])
    strategies.append(("nomenclature", " ".join(dict.fromkeys(variants))[:200] or line_spec.product))

    # Dedup by query text, keep order, max 5
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for name, q in strategies:
        key = (q or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((name, q.strip()))
        if len(out) >= 5:
            break
    return out


class SourcingAgent(BaseAgent):
    name = "sourcing"

    def process(self, opp: Opportunity) -> list[SupplierQuote]:
        run = self.start_run(opp.id)
        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        # clear prior quotes for re-run honesty
        for old in (
            self.session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
        ):
            self.session.delete(old)
        self.session.flush()

        quotes: list[SupplierQuote] = []
        sources_used: list[str] = []
        session_iso = MatchSession()
        line_payloads: list[dict[str, Any]] = []
        best_payloads: list[dict[str, Any]] = []

        items = list(tender.items) if tender else []
        if not items:
            item_refs = [
                {
                    "tender_item_id": -1 * (opp.id or 1),  # synthetic but mandatory
                    "line_no": 0,
                    "query": opp.title[:80] or opp.rubros[:80] or "insumos oficina",
                    "qty": 1.0,
                    "product": opp.title[:80] or "insumos",
                    "specs": "",
                    "brand": "",
                    "model": "",
                }
            ]
        else:
            item_refs = []
            for it in items:
                full = full_spec_blob(it.product, it.specs, it.brand, it.model)
                item_refs.append(
                    {
                        "tender_item_id": it.id,
                        "line_no": it.line_no,
                        "query": full,
                        "qty": it.qty,
                        "product": it.product,
                        "specs": it.specs or full,
                        "brand": it.brand or "",
                        "model": it.model or "",
                    }
                )

        for ref in item_refs[:12]:
            tender_item_id = ref["tender_item_id"]
            if tender_item_id is None:
                raise ValueError("item_id mandatory on every candidate")
            line_no = int(ref.get("line_no") or 0)
            session_iso.begin_item(tender_item_id, line_no=line_no)

            line_spec = build_line_spec(
                line_no=line_no,
                product=ref["product"],
                specs=ref["specs"],
                brand=ref["brand"],
                model=ref["model"],
                qty=float(ref["qty"] or 1),
            )
            need_type = line_spec.product_type
            qty = float(ref["qty"] or 1)
            label = ref["product"]

            strategies = _search_strategies(line_spec)
            collected: list[dict[str, Any]] = []
            line_quotes: list[SupplierQuote] = []

            for strat_name, query in strategies:
                results, src = search_real_pages(query, limit=3)
                sources_used.append(f"{src}:{strat_name}")
                ml_results, ml_src = search_public(query[:120], limit=2)
                sources_used.append(f"{ml_src}:{strat_name}")
                batch = results + ml_results

                for res in batch:
                    url = (res.get("url") or "").strip()
                    if url and session_iso.url_used(url):
                        continue  # forbid URL reuse across lines
                    # Skip duplicate URLs within this line's collected set
                    if url and any((c.get("url") or "").strip() == url for c in collected):
                        continue
                    page_text = " ".join(
                        str(x)
                        for x in (
                            res.get("title"),
                            res.get("notes"),
                            res.get("raw_text"),
                            res.get("description"),
                        )
                        if x
                    )
                    # Early PRODUCT_TYPE gate — discard wrong type before deeper match
                    found_type = infer_product_type(
                        f"{res.get('title') or ''} {page_text}",
                        title_hint=res.get("title") or "",
                    )
                    if (
                        need_type
                        and need_type != "UNKNOWN"
                        and found_type != "UNKNOWN"
                        and found_type != need_type
                    ):
                        # keep as negative evidence only if we have nothing else yet
                        res = dict(res)
                        res["_wrong_type"] = f"{need_type}->{found_type}"
                        res["_strat"] = strat_name
                        collected.append(res)
                        continue
                    res = dict(res)
                    res["_strat"] = strat_name
                    collected.append(res)

                # Stop early if we already have a verified-tech candidate with price
                # (still continue strategies only when pending)
                # evaluated below after matching

            # If no results at all → SIN_PROVEEDOR_VERIFICADO
            if not collected:
                supplier = self._get_or_create_supplier(
                    "SIN_PROVEEDOR_VERIFICADO", source="none", url=""
                )
                cref = session_iso.bind_candidate(
                    tender_item_id=tender_item_id,
                    url="",
                    title="SIN_PROVEEDOR_VERIFICADO",
                    line_no=line_no,
                    salt="empty",
                )
                empty = match_line_to_candidate(
                    product=ref["product"],
                    specs=ref["specs"],
                    brand=ref["brand"],
                    model=ref["model"],
                    candidate_title="",
                    candidate_text="",
                    source_url="",
                    tender_item_id=tender_item_id,
                    candidate_id=cref.candidate_id,
                    line_no=line_no,
                    qty_needed=qty,
                )
                session_iso.evidence.put(
                    cref,
                    empty.evidence_matrix,
                    meta=empty.to_dict(),
                )
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=label or ref["query"][:200],
                    unit_cost=None,
                    currency="ARS",
                    qty=qty,
                    match_score=0,
                    match_pct=0,
                    match_class=TECH_NO_VER,
                    technical_status=TECH_NO_VER,
                    commercial_status="PRECIO_NO_VERIFICADO",
                    evidence_json=empty.to_json(),
                    verification="NO VERIFICADO",
                    url="",
                    notes="SIN_PROVEEDOR_VERIFICADO; sin evidencia pública",
                    stock_note="NO VERIFICADO",
                    shipping_neuquen="NO VERIFICADO",
                    verified_at=now_ba().isoformat(timespec="seconds"),
                )
                self.session.add(q)
                quotes.append(q)
                line_quotes.append(q)
                best_payloads.append(
                    {
                        "tender_item_id": tender_item_id,
                        "candidate_id": cref.candidate_id,
                        "url": "",
                        "product_type_need": empty.product_type_need,
                        "product_type_found": empty.product_type_found,
                        "candidate_title": "",
                        "evidence_json": empty.to_json(),
                    }
                )
                session_iso.end_item()
                continue

            line_best: SupplierQuote | None = None
            for res in collected:
                url = (res.get("url") or "").strip()
                page_text = " ".join(
                    str(x)
                    for x in (
                        res.get("title"),
                        res.get("notes"),
                        res.get("raw_text"),
                        res.get("description"),
                    )
                    if x
                )
                cref = session_iso.bind_candidate(
                    tender_item_id=tender_item_id,
                    url=url,
                    title=res.get("title") or "",
                    line_no=line_no,
                    salt=str(res.get("_strat") or ""),
                )
                mres = match_line_to_candidate(
                    product=ref["product"],
                    specs=ref["specs"],
                    brand=ref["brand"],
                    model=ref["model"],
                    candidate_title=res.get("title") or "",
                    candidate_text=page_text,
                    source_url=url,
                    price=res.get("price"),
                    stock=str(res.get("stock") or ""),
                    shipping_neuquen=str(res.get("shipping_neuquen") or ""),
                    qty_needed=qty,
                    tender_item_id=tender_item_id,
                    candidate_id=cref.candidate_id,
                    line_no=line_no,
                )
                # Force NO_CUMPLE if early wrong-type flag
                if res.get("_wrong_type"):
                    mres.technical_status = TECH_NO_CUMPLE
                    mres.match_class = TECH_NO_CUMPLE
                    mres.match_pct = min(mres.match_pct, 20)
                    mres.blockers = list(mres.blockers) + [
                        f"DISCARD_WRONG_TYPE:{res['_wrong_type']}"
                    ]

                session_iso.evidence.put(cref, mres.evidence_matrix, meta=mres.to_dict())
                payload = {
                    "tender_item_id": tender_item_id,
                    "candidate_id": cref.candidate_id,
                    "url": url,
                    "product_type_need": mres.product_type_need,
                    "product_type_found": mres.product_type_found,
                    "candidate_title": res.get("title") or "",
                    "brand": ref["brand"],
                    "model": ref["model"],
                    "price": res.get("price"),
                    "stock": res.get("stock"),
                    "evidence_json": mres.to_json(),
                }
                issues = session_iso.audit_candidate_payload(
                    cref, payload, other_item_payloads=line_payloads
                )
                if issues:
                    mres.notes = (mres.notes or "") + " | AUDIT:" + ",".join(issues)

                supplier = self._get_or_create_supplier(
                    res.get("seller") or "desconocido",
                    source=res.get("source", "unknown"),
                    url=url,
                )
                price = res.get("price")
                verification = res.get("verification", "NO VERIFICADO")
                if price is None:
                    verification = "NO VERIFICADO"
                tech = mres.technical_status or mres.match_class
                if tech in (TECH_NO_CUMPLE, "NO CUMPLE", CLASS_NO_CUMPLE):
                    verification = "NO CUMPLE"
                elif tech in (TECH_NO_VER, "NO VERIFICADO", CLASS_NO_VER):
                    verification = "NO VERIFICADO"
                elif tech in VERIFIED_TECH and price is not None:
                    verification = res.get("verification") or "PROBABLE"
                else:
                    verification = verification or tech

                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=res.get("title") or label or ref["query"][:200],
                    unit_cost=float(price) if price is not None else None,
                    currency=res.get("currency") or "ARS",
                    qty=qty,
                    match_score=mres.match_pct,
                    match_pct=mres.match_pct,
                    match_class=tech,
                    technical_status=tech,
                    commercial_status=mres.commercial_status,
                    evidence_json=mres.to_json(),
                    verification=verification,
                    url=url,
                    notes=(
                        f"{res.get('notes') or ''} | strat={res.get('_strat')} | "
                        f"class={mres.match_class} | cid={cref.candidate_id}"
                    ),
                    stock_note=str(res.get("stock") or "NO VERIFICADO"),
                    shipping_neuquen=str(
                        res.get("shipping_neuquen") or "NO VERIFICADO"
                    ),
                    verified_at=str(
                        res.get("verified_at")
                        or now_ba().isoformat(timespec="seconds")
                    ),
                )
                self.session.add(q)
                quotes.append(q)
                line_quotes.append(q)
                line_payloads.append(payload)

                # Prefer verified tech; among those prefer priced; else higher match_pct
                def _rank(qq: SupplierQuote) -> tuple:
                    t = qq.technical_status or ""
                    return (
                        0 if t in VERIFIED_TECH else 1 if t not in (TECH_NO_CUMPLE, "NO CUMPLE") else 2,
                        0 if qq.unit_cost is not None else 1,
                        -(qq.match_pct or 0),
                    )

                if line_best is None or _rank(q) < _rank(line_best):
                    line_best = q

            # Reserve best URL so other lines cannot reuse
            if line_best and line_best.url:
                session_iso.remember_url(line_best.url)

            if line_best is not None:
                try:
                    evid = json.loads(line_best.evidence_json or "{}")
                except Exception:
                    evid = {}
                best_payloads.append(
                    {
                        "tender_item_id": tender_item_id,
                        "candidate_id": evid.get("candidate_id") or "",
                        "url": line_best.url or "",
                        "product_type_need": evid.get("product_type_need"),
                        "product_type_found": evid.get("product_type_found"),
                        "candidate_title": line_best.product_label,
                        "evidence_json": line_best.evidence_json,
                    }
                )
            else:
                # All discarded — emit SIN_PROVEEDOR_VERIFICADO
                supplier = self._get_or_create_supplier(
                    "SIN_PROVEEDOR_VERIFICADO", source="none", url=""
                )
                cref = session_iso.bind_candidate(
                    tender_item_id=tender_item_id,
                    url="",
                    title="SIN_PROVEEDOR_VERIFICADO",
                    line_no=line_no,
                    salt="all_discarded",
                )
                empty = match_line_to_candidate(
                    product=ref["product"],
                    specs=ref["specs"],
                    brand=ref["brand"],
                    model=ref["model"],
                    candidate_title="",
                    candidate_text="",
                    source_url="",
                    tender_item_id=tender_item_id,
                    candidate_id=cref.candidate_id,
                    line_no=line_no,
                    qty_needed=qty,
                )
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=label,
                    unit_cost=None,
                    currency="ARS",
                    qty=qty,
                    match_score=0,
                    match_pct=0,
                    match_class=TECH_NO_VER,
                    technical_status=TECH_NO_VER,
                    commercial_status="PRECIO_NO_VERIFICADO",
                    evidence_json=empty.to_json(),
                    verification="NO VERIFICADO",
                    url="",
                    notes="SIN_PROVEEDOR_VERIFICADO after iterative strategies",
                    stock_note="NO VERIFICADO",
                    shipping_neuquen="NO VERIFICADO",
                    verified_at=now_ba().isoformat(timespec="seconds"),
                )
                self.session.add(q)
                quotes.append(q)
                best_payloads.append(
                    {
                        "tender_item_id": tender_item_id,
                        "candidate_id": cref.candidate_id,
                        "url": "",
                        "product_type_need": empty.product_type_need,
                        "product_type_found": empty.product_type_found,
                        "candidate_title": "",
                        "evidence_json": empty.to_json(),
                    }
                )

            session_iso.end_item()  # clear temp between items

        # Final cross-line audit
        contamination = assert_no_cross_contamination(best_payloads)
        audit_note = ""
        if contamination:
            audit_note = "CROSS_LINE_AUDIT:" + ";".join(contamination)
            self.finding(
                run,
                audit_note,
                opportunity_id=opp.id,
                severity="ERROR",
                code="CROSS_LINE_CONTAMINATION",
                blocks=True,
            )

        self.finish_run(
            run,
            f"quotes={len(quotes)}; sources={','.join(sorted(set(sources_used))[:8])}; "
            f"audit={audit_note or 'OK'}",
        )
        opp.state = "SOURCING"
        self.session.commit()
        return quotes

    def _get_or_create_supplier(self, name: str, source: str, url: str) -> Supplier:
        name = (name or "desconocido")[:255]
        s = self.session.query(Supplier).filter_by(name=name).one_or_none()
        if s:
            if url and not s.url:
                s.url = url
            return s
        s = Supplier(name=name, source=source, url=url)
        self.session.add(s)
        self.session.flush()
        return s
